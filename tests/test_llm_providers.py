"""
LLM 统一架构单元测试（V5.0 新增）
覆盖：config 解析、utils JSON 提取、Provider URL 构造、Router Fallback、
以及业务服务（analyze_company / expand_keywords）走统一接口的兼容性。
"""
import asyncio
from typing import List, Optional, Dict, Any

import pytest

from app.llm.config import LLMConfig, get_env_config
from app.llm.exceptions import (
    LLMAuthenticationError,
    LLMConnectionError,
    LLMModelUnavailableError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from app.llm.providers.base import BaseLLMProvider, LLMChatResult
from app.llm.providers.glm import GLMProvider
from app.llm.providers.openai_compatible import OpenAICompatibleProvider
from app.llm.router import LLMRouter
from app.llm.utils import extract_json


# ═══════════════════════════════════════════
# utils.extract_json
# ═══════════════════════════════════════════

class TestExtractJson:
    def test_plain_object(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_plain_array(self):
        assert extract_json('["k1", "k2"]') == ["k1", "k2"]

    def test_markdown_codeblock(self):
        content = '```json\n{"b": 2}\n```'
        assert extract_json(content) == {"b": 2}

    def test_markdown_codeblock_array(self):
        content = '```\n["x", "y"]\n```'
        assert extract_json(content) == ["x", "y"]

    def test_prefixed_text_object(self):
        assert extract_json('好的，以下是结果：{"c": 3}') == {"c": 3}

    def test_prefixed_text_array(self):
        assert extract_json('结果如下：["a", "b"] 请查收') == ["a", "b"]

    def test_invalid_returns_none(self):
        assert extract_json("这不是 JSON") is None

    def test_empty_returns_none(self):
        assert extract_json("") is None
        assert extract_json(None) is None

    def test_unescaped_newlines_in_string(self):
        """LLM 在 body 字段返回真实换行（非法 JSON），应自动修复"""
        content = '```json\n{"subject": "Hi", "body": "Line one\nLine two\nLine three", "language": "en"}\n```'
        result = extract_json(content)
        assert result is not None
        assert result["subject"] == "Hi"
        assert result["body"] == "Line one\nLine two\nLine three"

    def test_unescaped_newlines_prefixed(self):
        content = '结果如下：{"a": "x\ny"} 请查收'
        assert extract_json(content) == {"a": "x\ny"}

    def test_escaped_newlines_untouched(self):
        content = '{"body": "a\\nb"}'
        assert extract_json(content) == {"body": "a\nb"}


# ═══════════════════════════════════════════
# config
# ═══════════════════════════════════════════

class TestConfig:
    def test_get_env_config_defaults(self, monkeypatch):
        monkeypatch.delenv("GLM_API_KEY", raising=False)
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("GLM_API_URL", raising=False)
        monkeypatch.delenv("GLM_MODEL", raising=False)
        monkeypatch.delenv("GLM_FALLBACK_MODELS", raising=False)
        cfg = get_env_config()
        assert cfg.provider == "glm"
        assert cfg.api_key == ""
        assert cfg.default_model == "glm-4.7-flash"
        assert cfg.fallback_models == [
            "glm-4.7-flash", "glm-4.6v-flash", "glm-4-flash-250414"
        ]

    def test_get_env_config_from_env(self, monkeypatch):
        monkeypatch.setenv("GLM_API_KEY", "test-key")
        monkeypatch.setenv("GLM_MODEL", "glm-4-flash-250414")
        monkeypatch.setenv("GLM_FALLBACK_MODELS", "a,b , c")
        cfg = get_env_config()
        assert cfg.api_key == "test-key"
        assert cfg.default_model == "glm-4-flash-250414"
        assert cfg.fallback_models == ["a", "b", "c"]

    def test_deepseek_backward_compat(self, monkeypatch):
        monkeypatch.delenv("GLM_API_KEY", raising=False)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
        cfg = get_env_config()
        assert cfg.api_key == "deepseek-key"

    def test_llm_config_dataclass(self):
        cfg = LLMConfig(provider="deepseek", api_key="k", default_model="deepseek-chat")
        assert cfg.provider == "deepseek"
        assert cfg.fallback_models == []


# ═══════════════════════════════════════════
# providers
# ═══════════════════════════════════════════

class TestProviders:
    def test_glm_provider_defaults(self):
        p = GLMProvider(api_key="test")
        assert p._build_url() == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
        assert p.default_model == "glm-4.7-flash"
        assert "glm-4.6v-flash" in p.get_models()
        assert "glm-4-flash-250414" in p.get_models()

    def test_glm_provider_custom(self):
        p = GLMProvider(
            api_key="k", base_url="https://custom", default_model="m1",
            fallback_models=["m2"],
        )
        assert p._build_url() == "https://custom/chat/completions"
        assert p.get_models() == ["m1", "m2"]

    def test_openai_compatible_url_with_trailing_slash(self):
        p = OpenAICompatibleProvider(api_key="k", base_url="https://api.deepseek.com/")
        assert p._build_url() == "https://api.deepseek.com/chat/completions"

    def test_openai_compatible_url_already_chat_completions(self):
        p = OpenAICompatibleProvider(
            api_key="k", base_url="https://x.com/v1/chat/completions"
        )
        assert p._build_url() == "https://x.com/v1/chat/completions"

    def test_get_models_dedup(self):
        p = GLMProvider(api_key="k", default_model="a", fallback_models=["a", "b"])
        assert p.get_models() == ["a", "b"]


# ═══════════════════════════════════════════
# router（自动 Fallback）
# ═══════════════════════════════════════════

class _FakeProvider(BaseLLMProvider):
    """可编程假 Provider：按 (model, call_index) 返回结果或抛出异常"""

    def __init__(self, behavior: Dict[str, Any], **kwargs):
        super().__init__(api_key="fake", default_model="primary", **kwargs)
        self.behavior = behavior
        self.calls = []

    async def chat(self, messages, model=None, temperature=0.3, max_tokens=4096):
        self.calls.append(model)
        key = model or self.default_model
        entry = self.behavior.get(key)
        if entry is None:
            return LLMChatResult(content="default", model=key)
        if isinstance(entry, type) and issubclass(entry, Exception):
            raise entry(f"fake error on {key}")
        if callable(entry):
            return entry(self)
        if entry == "empty":
            return LLMChatResult(content="", finish_reason="stop", model=key)
        return LLMChatResult(content=entry, model=key)

    async def test_connection(self, model=None):
        result = await self.chat([{"role": "user", "content": "Hello"}], model=model)
        return result.model


class TestRouter:
    def test_success_on_primary(self):
        provider = _FakeProvider({})
        router = LLMRouter(provider, max_retries=2)
        result = asyncio.run(router.chat([{"role": "user", "content": "hi"}]))
        assert result.content == "default"
        assert provider.calls == ["primary"]

    def test_fallback_on_rate_limit(self):
        provider = _FakeProvider({"primary": LLMRateLimitError, "backup": "ok"})
        router = LLMRouter(provider, max_retries=2)
        result = asyncio.run(
            router.chat([{"role": "user", "content": "hi"}], fallback_models=["backup"])
        )
        assert result.content == "ok"
        assert provider.calls == ["primary", "backup"]

    def test_fallback_chain_multiple(self):
        provider = _FakeProvider({
            "primary": LLMRateLimitError,
            "backup1": LLMModelUnavailableError,
            "backup2": "ok",
        })
        router = LLMRouter(provider, max_retries=1)
        result = asyncio.run(
            router.chat([{"role": "user", "content": "hi"}], fallback_models=["backup1", "backup2"])
        )
        assert result.content == "ok"
        assert provider.calls == ["primary", "backup1", "backup2"]

    def test_all_failed_returns_none(self):
        provider = _FakeProvider({"primary": LLMRateLimitError, "backup": LLMRateLimitError})
        router = LLMRouter(provider, max_retries=1)
        result = asyncio.run(
            router.chat([{"role": "user", "content": "hi"}], fallback_models=["backup"])
        )
        assert result is None

    def test_timeout_retry_then_fallback(self):
        calls = []

        def behavior(prov):
            calls.append(prov.calls[-1])
            if len(calls) < 2:
                raise LLMTimeoutError("timeout")
            return LLMChatResult(content="ok", model=prov.calls[-1])

        provider = _FakeProvider({"primary": behavior, "backup": "ok"})
        router = LLMRouter(provider, max_retries=2, retry_base_delay=0.01)
        result = asyncio.run(
            router.chat([{"role": "user", "content": "hi"}], fallback_models=["backup"])
        )
        # 第一次超时 → 重试；第二次成功
        assert result.content == "ok"
        assert len(provider.calls) >= 2

    def test_auth_error_fails_immediately(self):
        provider = _FakeProvider({"primary": LLMAuthenticationError})
        router = LLMRouter(provider, max_retries=2)
        result = asyncio.run(
            router.chat([{"role": "user", "content": "hi"}], fallback_models=["backup"])
        )
        assert result is None
        assert provider.calls == ["primary"]  # 不尝试 fallback

    def test_connection_error_fails_immediately(self):
        provider = _FakeProvider({"primary": LLMConnectionError})
        router = LLMRouter(provider, max_retries=2)
        result = asyncio.run(router.chat([{"role": "user", "content": "hi"}]))
        assert result is None

    def test_empty_content_retries_same_model(self):
        provider = _FakeProvider({"primary": "empty"})
        router = LLMRouter(provider, max_retries=2)
        result = asyncio.run(router.chat([{"role": "user", "content": "hi"}]))
        assert result is None  # 空内容重试耗尽后无模型可降级
        assert len(provider.calls) == 2

    def test_empty_content_length_falls_back(self):
        def behavior(prov):
            return LLMChatResult(content="", finish_reason="length", model=prov.calls[-1])

        provider = _FakeProvider({"primary": behavior, "backup": "ok"})
        router = LLMRouter(provider, max_retries=2)
        result = asyncio.run(
            router.chat([{"role": "user", "content": "hi"}], fallback_models=["backup"])
        )
        assert result.content == "ok"
        assert provider.calls == ["primary", "backup"]


# ═══════════════════════════════════════════
# 业务服务走统一接口（兼容性验证）
# ═══════════════════════════════════════════

class _FakeManager:
    def __init__(self, result: Optional[LLMChatResult], raise_auth=False):
        self.result = result
        self.raise_auth = raise_auth
        self.last_messages = None

    async def chat(self, messages, user_id=None, model=None, temperature=0.3, max_tokens=4096):
        self.last_messages = messages
        if self.raise_auth:
            from app.llm.manager import get_llm_manager
            return None
        return self.result


class TestServiceCompat:
    def test_analyze_company_returns_parsed_dict(self, monkeypatch):
        import app.services.glm_analyzer as glm_analyzer
        fake = _FakeManager(LLMChatResult(content='{"company_type": "EPC", "summary": "test"}'))
        monkeypatch.setattr(glm_analyzer, "get_llm_manager", lambda: fake)
        result = asyncio.run(glm_analyzer.analyze_company("网页内容"))
        assert result == {"company_type": "EPC", "summary": "test"}
        assert fake.last_messages[0]["role"] == "system"
        assert fake.last_messages[1]["role"] == "user"

    def test_analyze_company_codeblock_content(self, monkeypatch):
        import app.services.glm_analyzer as glm_analyzer
        fake = _FakeManager(LLMChatResult(content='```json\n{"company_type": "Manufacturer"}\n```'))
        monkeypatch.setattr(glm_analyzer, "get_llm_manager", lambda: fake)
        result = asyncio.run(glm_analyzer.analyze_company("x"))
        assert result == {"company_type": "Manufacturer"}

    def test_analyze_company_failure_returns_none(self, monkeypatch):
        import app.services.glm_analyzer as glm_analyzer
        fake = _FakeManager(None)
        monkeypatch.setattr(glm_analyzer, "get_llm_manager", lambda: fake)
        assert asyncio.run(glm_analyzer.analyze_company("x")) is None

    def test_analyze_company_parse_failure_returns_none(self, monkeypatch):
        import app.services.glm_analyzer as glm_analyzer
        fake = _FakeManager(LLMChatResult(content="这不是JSON"))
        monkeypatch.setattr(glm_analyzer, "get_llm_manager", lambda: fake)
        assert asyncio.run(glm_analyzer.analyze_company("x")) is None

    def test_expand_keywords_returns_list(self, monkeypatch):
        import app.services.keyword_expander as ke
        fake = _FakeManager(LLMChatResult(content='["kw1", "kw2", "kw2"]'))
        monkeypatch.setattr(ke, "get_llm_manager", lambda: fake)
        result = asyncio.run(ke.expand_keywords("water"))
        assert result == ["kw1", "kw2"]  # 去重

    def test_expand_keywords_failure_returns_base(self, monkeypatch):
        import app.services.keyword_expander as ke
        fake = _FakeManager(None)
        monkeypatch.setattr(ke, "get_llm_manager", lambda: fake)
        assert asyncio.run(ke.expand_keywords("water")) == ["water"]

    def test_expand_keywords_parse_failure_returns_base(self, monkeypatch):
        import app.services.keyword_expander as ke
        fake = _FakeManager(LLMChatResult(content="no json here"))
        monkeypatch.setattr(ke, "get_llm_manager", lambda: fake)
        assert asyncio.run(ke.expand_keywords("water")) == ["water"]

    def test_analyze_company_keeps_generate_summary(self):
        from app.services.glm_analyzer import generate_summary
        assert generate_summary({"summary": "short"}, {"country": "DE"}) == "short"
        assert generate_summary({}, {"country": "DE"}) == "DE"

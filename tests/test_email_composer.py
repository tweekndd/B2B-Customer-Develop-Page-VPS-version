"""
V4.6 开发信生成服务单元测试（email_composer）
覆盖：多语种自动检测、产品关键词驱动的 prompt 构建、以及 generate_email_draft
通过统一 LLM 接口调用（mock manager）。
"""
import asyncio
from typing import Optional

import pytest

from app.services.email_composer import (
    detect_email_language,
    _build_prompt,
    generate_email_draft,
    load_email_draft,
)


class TestDetectEmailLanguage:
    def test_mexico_spanish(self):
        assert detect_email_language("Mexico")["language"] == "Spanish"
        assert detect_email_language("Mexico")["code"] == "es"

    def test_spain_spanish(self):
        assert detect_email_language("Spain")["language"] == "Spanish"

    def test_china_chinese(self):
        assert detect_email_language("China")["language"] == "Chinese"

    def test_none_country_english(self):
        assert detect_email_language(None)["language"] == "English"

    def test_empty_country_english(self):
        assert detect_email_language("")["language"] == "English"


class TestBuildPrompt:
    def test_product_keywords_are_core(self):
        """产品关键词必须出现在 prompt 中（核心驱动）"""
        prompt = _build_prompt(
            company_name="Test Co",
            country="Mexico",
            product_keywords=["copper mine", "crushing equipment"],
            company_type="Mining Company",
            language="Spanish",
        )
        assert "copper mine" in prompt
        assert "crushing equipment" in prompt
        assert "Test Co" in prompt
        assert "Spanish" in prompt

    def test_distributor_tailoring_rule(self):
        """经销商提示强调供货稳定/OEM"""
        prompt = _build_prompt(
            company_name="Test Co", country="Germany", product_keywords=["pump"],
            company_type="Distributor", language="English",
        )
        assert "OEM" in prompt or "供货稳定" in prompt

    def test_default_products_fallback(self):
        """无产品关键词时使用默认占位"""
        prompt = _build_prompt(company_name="X", country="US", product_keywords=[], language="English")
        assert "我们的主打产品" in prompt


class FakeChatResult:
    def __init__(self, content):
        self.content = content


class FakeLLMManager:
    """mock get_llm_manager().chat()"""
    def __init__(self, content):
        self._content = content
        self.calls = []

    async def chat(self, messages, user_id=None, temperature=0.7, max_tokens=2048):
        self.calls.append({"messages": messages, "user_id": user_id, "temperature": temperature, "max_tokens": max_tokens})
        return FakeChatResult(self._content)


def _patch_manager(monkeypatch, content):
    manager = FakeLLMManager(content)
    monkeypatch.setattr("app.services.email_composer.get_llm_manager", lambda: manager)
    return manager


class TestGenerateEmailDraft:
    def test_success(self, monkeypatch):
        content = '```json\n{"subject": "Hi", "body": "Hello line1\nline2", "language": "English"}\n```'
        manager = _patch_manager(monkeypatch, content)
        draft = asyncio.get_event_loop().run_until_complete(
            generate_email_draft(
                company_name="Test Co", country="Mexico",
                product_keywords=["pump"], language="English",
            )
        )
        assert draft is not None
        assert draft["subject"] == "Hi"
        assert draft["body"] == "Hello line1\nline2"
        assert manager.calls[0]["user_id"] is None
        assert manager.calls[0]["temperature"] == 0.7

    def test_auto_language_detection(self, monkeypatch):
        """不传语言时按国家自动检测"""
        content = '{"subject": "S", "body": "B", "language": "Spanish"}'
        _patch_manager(monkeypatch, content)
        draft = asyncio.get_event_loop().run_until_complete(
            generate_email_draft(company_name="Co", country="Mexico", product_keywords=["pump"])
        )
        assert draft is not None
        assert draft["language"] == "Spanish"

    def test_manager_returns_none(self, monkeypatch):
        _patch_manager(monkeypatch, None)
        draft = asyncio.get_event_loop().run_until_complete(
            generate_email_draft(company_name="Co", country="US", product_keywords=["pump"])
        )
        assert draft is None

    def test_invalid_json_returns_none(self, monkeypatch):
        _patch_manager(monkeypatch, "not json at all")
        draft = asyncio.get_event_loop().run_until_complete(
            generate_email_draft(company_name="Co", country="US", product_keywords=["pump"])
        )
        assert draft is None


class TestLoadEmailDraft:
    def test_valid(self):
        d = load_email_draft('{"subject": "S", "body": "B"}')
        assert d == {"subject": "S", "body": "B"}

    def test_invalid(self):
        assert load_email_draft("not json") is None
        assert load_email_draft(None) is None
        assert load_email_draft("") is None

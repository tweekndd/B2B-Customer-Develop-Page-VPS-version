"""回归测试：LLM 测试连接/模型发现时 API Key 解析逻辑。

背景：settings 页保存 LLM 配置后前端会清空 API Key 输入框，
点「测试连接」时请求里 api_key 为空、但 provider 恒有值。
旧逻辑只要 provider 有值就走“临时配置”分支把 key 置空，
导致已保存的 Key 永不生效、永远报“未配置 API Key”。
修复后：未显式传 api_key 时以已保存配置（用户配置→环境变量）为基底。
"""
import asyncio
import pytest

from unittest import mock

from app.llm.config import LLMConfig
from app.llm.manager import LLMManager
from app.llm.providers.base import LLMChatResult

manager = LLMManager()


def test_saved_key_used_when_form_key_empty():
    """provider 有值但 api_key 为空 → 必须回退到已保存配置的 Key（场景1）。"""
    cfg = manager._resolve_config(
        user_id=1,
        provider="glm",
        api_key=None,
        base_url="https://open.bigmodel.cn/api/paas/v4",
        model="glm-4.7-flash",
    )
    assert isinstance(cfg, LLMConfig)
    assert cfg.api_key, "应回退到已保存的 API Key"
    assert cfg.provider == "glm"
    assert cfg.default_model == "glm-4.7-flash"


def test_explicit_key_prefers_temp_config():
    """显式传入 api_key → 使用临时配置（未保存表单）。"""
    cfg = manager._resolve_config(
        user_id=1,
        provider="deepseek",
        api_key="sk-test-123",
        base_url="https://api.deepseek.com/v1",
    )
    assert cfg.api_key == "sk-test-123"
    assert cfg.provider == "deepseek"
    assert cfg.base_url == "https://api.deepseek.com/v1"


def test_empty_everything_falls_back_to_saved_or_env():
    """后端内部调用（无任何表单参数）→ 用户已保存配置或环境变量。"""
    cfg = manager._resolve_config(user_id=1)
    assert isinstance(cfg, LLMConfig)
    assert cfg.api_key, "用户已保存配置应含有效 Key"


def test_explicit_overrides_win_over_saved_base():
    """未传 key 但显式给了 model → 基底用已保存 Key，model 被覆盖。"""
    cfg = manager._resolve_config(
        user_id=1,
        provider="glm",
        api_key=None,
        base_url=None,
        model="glm-4.6v-flash",
        fallback_models=[],
    )
    assert cfg.api_key, "应使用已保存 Key"
    assert cfg.default_model == "glm-4.6v-flash"
    assert cfg.fallback_models == []


def test_chat_stamps_provider_on_real_result():
    """chat() 返回的真实 LLMChatResult 必须带 provider 字段（曾缺失导致
    outreach_generation_service 读取 result.provider 抛 AttributeError）。"""
    mgr = LLMManager()
    fake_router = mock.MagicMock()

    async def fake_chat(messages, model=None, fallback_models=None, temperature=0.3, max_tokens=4096):
        return LLMChatResult(content="hello", model=model)

    fake_router.chat = fake_chat
    with mock.patch.object(mgr, "_get_router", return_value=fake_router), \
         mock.patch("app.llm.manager.resolve_config",
                    return_value=LLMConfig(provider="glm", api_key="k", default_model="glm-4.7-flash")):
        result = asyncio.run(
            mgr.chat([{"role": "user", "content": "hi"}], user_id=None)
        )
    assert result is not None
    assert isinstance(result, LLMChatResult)
    assert result.provider == "glm"


if __name__ == "__main__":
    raise SystemExit("Run with pytest")

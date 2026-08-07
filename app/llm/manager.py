"""
LLM Manager（V5.0 新增）

统一 AI 调用入口。业务代码禁止直接调用具体模型，一律通过：
    await get_llm_manager().chat(messages, ...)

流程：
    调用方(messages)
        → LLMManager.chat()
            → resolve_config(user_id)         # Round 3: 优先用户配置
            → get_provider(config)            # Provider 工厂
            → LLMRouter.chat()                # 自动 Fallback + 重试
                → Provider.chat()             # 单次 HTTP 调用
                    → AI Model API
"""
import logging
from typing import List, Optional, Dict, Any

from app.llm.config import LLMConfig, resolve_config
from app.llm.providers.base import BaseLLMProvider, LLMChatResult
from app.llm.providers.glm import GLMProvider
from app.llm.providers.openai_compatible import OpenAICompatibleProvider
from app.llm.router import LLMRouter

logger = logging.getLogger("llm.manager")

# Provider 名称 → 实现类的映射
_PROVIDER_CLASSES = {
    "glm": GLMProvider,
    "zhipu": GLMProvider,
    "zhipuai": GLMProvider,
    "bigmodel": GLMProvider,
    "openai": OpenAICompatibleProvider,
    "openai-compatible": OpenAICompatibleProvider,
    "compatible": OpenAICompatibleProvider,
    "deepseek": OpenAICompatibleProvider,
    "qwen": OpenAICompatibleProvider,
    "moonshot": OpenAICompatibleProvider,
    "custom": OpenAICompatibleProvider,
}


class LLMManager:
    """LLM 统一管理器"""

    def get_provider(self, config: LLMConfig) -> BaseLLMProvider:
        """按配置创建 Provider 实例"""
        provider_name = (config.provider or "glm").strip().lower()
        provider_cls = _PROVIDER_CLASSES.get(provider_name, GLMProvider)
        return provider_cls(
            api_key=config.api_key,
            base_url=config.base_url,
            default_model=config.default_model,
            fallback_models=config.fallback_models,
        )

    def _get_router(self, config: LLMConfig) -> LLMRouter:
        return LLMRouter(self.get_provider(config))

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        user_id: Optional[int] = None,
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> Optional[LLMChatResult]:
        """统一对话入口：解析配置 → Provider → Router 自动 Fallback"""
        config = resolve_config(user_id)
        if not config.api_key:
            logger.warning("未配置 LLM API Key（GLM_API_KEY / DEEPSEEK_API_KEY / 用户配置），跳过调用")
            return None

        router = self._get_router(config)
        return await router.chat(
            messages,
            model=model or config.default_model,
            fallback_models=config.fallback_models,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def test_connection(
        self,
        user_id: Optional[int] = None,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        fallback_models: Optional[List[str]] = None,
    ) -> str:
        """连通性测试。

        传入 provider/api_key/base_url/model 时使用临时配置（前端测试连接，未保存）；
        否则使用当前用户已保存的配置。
        成功返回实际使用的模型名，失败抛出 LLMError 子类。
        """
        if provider:
            config = LLMConfig(
                provider=provider,
                api_key=api_key or "",
                base_url=base_url or "",
                default_model=model or "",
                fallback_models=fallback_models or [],
            )
        else:
            config = resolve_config(user_id)

        if not config.api_key:
            from app.llm.exceptions import LLMAuthenticationError

            raise LLMAuthenticationError("未配置 API Key")

        provider_instance = self.get_provider(config)
        return await provider_instance.test_connection(model or config.default_model)

    def get_models(self, user_id: Optional[int] = None) -> List[str]:
        """返回当前用户配置下可用的模型列表"""
        config = resolve_config(user_id)
        return self.get_provider(config).get_models()


# 模块级单例
_manager: Optional[LLMManager] = None


def get_llm_manager() -> LLMManager:
    """获取 LLM Manager 单例"""
    global _manager
    if _manager is None:
        _manager = LLMManager()
    return _manager

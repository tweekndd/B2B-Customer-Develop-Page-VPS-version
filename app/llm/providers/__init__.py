"""
LLM Provider 注册（V5.0 新增）
"""
from app.llm.providers.base import BaseLLMProvider, LLMChatResult
from app.llm.providers.glm import GLMProvider
from app.llm.providers.openai_compatible import OpenAICompatibleProvider

__all__ = [
    "BaseLLMProvider",
    "LLMChatResult",
    "GLMProvider",
    "OpenAICompatibleProvider",
]

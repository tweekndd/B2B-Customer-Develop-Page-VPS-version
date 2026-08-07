"""
LLM Provider 抽象基类（V5.0 新增）

所有 Provider 必须实现：
  - chat()             单次模型对话（不做重试/Fallback）
  - test_connection()  连通性测试
  - get_models()       支持的模型列表
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class LLMChatResult:
    """一次成功的 LLM 对话结果"""

    content: str = ""
    finish_reason: str = ""
    model: str = ""
    raw: Optional[Dict[str, Any]] = None


class BaseLLMProvider(ABC):
    """所有 LLM Provider 的统一抽象"""

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        default_model: str = "",
        fallback_models: Optional[List[str]] = None,
        timeout: float = 120,
    ):
        self.api_key = api_key or ""
        self.base_url = base_url or ""
        self.default_model = default_model or ""
        self.fallback_models = list(fallback_models or [])
        self.timeout = timeout

    @abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> LLMChatResult:
        """执行一次模型对话，返回 LLMChatResult。

        Provider 层负责：
          - 构造请求、发起 HTTP、解析标准响应
          - 将异常归类为 LLMError 子类抛出
        不负责：重试、Fallback（由 LLMRouter 负责）
        """
        raise NotImplementedError

    @abstractmethod
    async def test_connection(self, model: Optional[str] = None) -> str:
        """发送简单测试消息，成功返回实际使用的模型名，失败抛出 LLMError 子类"""
        raise NotImplementedError

    def get_models(self) -> List[str]:
        """返回该 Provider 支持/配置的模型列表（主模型 + Fallback）"""
        models = []
        if self.default_model:
            models.append(self.default_model)
        for m in self.fallback_models:
            if m and m not in models:
                models.append(m)
        return models

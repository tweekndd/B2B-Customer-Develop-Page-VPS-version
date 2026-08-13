"""
OpenAI Compatible Provider（V5.0 新增）

兼容所有 OpenAI API 格式的服务：
  - DeepSeek / Qwen / Moonshot / GLM / Custom
通过 api_key + base_url + model 即可接入。

base_url 支持两种形式：
  - https://api.deepseek.com        → 自动拼接 /chat/completions
  - https://.../chat/completions    → 直接使用
"""
import logging
from typing import List, Optional, Dict, Any

import httpx

from app.llm.exceptions import (
    LLMAuthenticationError,
    LLMConnectionError,
    LLMContentError,
    LLMError,
    LLMModelUnavailableError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from app.llm.providers.base import BaseLLMProvider, LLMChatResult

logger = logging.getLogger("llm.openai_compatible")

# 模块级共享 httpx 客户端，复用连接池
_shared_client: Optional[httpx.AsyncClient] = None


def _get_shared_client(timeout: float = 60.0) -> httpx.AsyncClient:
    """获取或创建共享的 httpx.AsyncClient（连接池复用）"""
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=30,
            ),
        )
    return _shared_client


class OpenAICompatibleProvider(BaseLLMProvider):
    """OpenAI 兼容协议 Provider"""

    def _build_url(self) -> str:
        url = (self.base_url or "").strip().rstrip("/")
        if not url:
            raise LLMError("未配置 Base URL")
        if url.endswith("/chat/completions"):
            return url
        return f"{url}/chat/completions"

    def _build_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _extract_error_reason(exc: "httpx.HTTPStatusError") -> str:
        try:
            body = exc.response.json()
            err = body.get("error")
            if isinstance(err, dict):
                return err.get("message", "")
            return str(err) or ""
        except Exception:
            return exc.response.text[:100]

    def _classify_http_error(self, exc: "httpx.HTTPStatusError") -> None:
        status = exc.response.status_code
        reason = self._extract_error_reason(exc)
        if status in (401, 403):
            raise LLMAuthenticationError(f"API Key 无效或无权限（HTTP {status}）: {reason}")
        if status == 429:
            raise LLMRateLimitError(f"请求频率受限（HTTP 429）: {reason}")
        if status in (500, 502, 503, 504):
            raise LLMModelUnavailableError(f"模型/服务暂不可用（HTTP {status}）: {reason}")
        raise LLMConnectionError(f"HTTP {status}: {reason}")

    async def _post(
        self,
        payload: Dict[str, Any],
        model: str,
    ) -> LLMChatResult:
        client = _get_shared_client(self.timeout)
        try:
            response = await client.post(
                self._build_url(),
                headers=self._build_headers(),
                json=payload,
            )
        except httpx.TimeoutException as e:
            raise LLMTimeoutError(f"请求超时（>{self.timeout}s）") from e
        except httpx.HTTPError as e:
            raise LLMConnectionError(f"网络请求失败: {e}") from e

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            self._classify_http_error(e)

        # 限制响应大小（防 OOM）
        content_length = response.headers.get("content-length")
        if content_length and int(content_length) > 10 * 1024 * 1024:  # 10MB
            raise LLMContentError("模型响应过大（>10MB），拒绝处理")

        try:
            data = response.json()
            content = data["choices"][0]["message"].get("content", "")
            finish_reason = data["choices"][0].get("finish_reason", "")
        except (KeyError, IndexError, ValueError) as e:
            raise LLMContentError(f"无法解析模型响应: {e}") from e

        return LLMChatResult(
            content=content,
            finish_reason=finish_reason,
            model=model,
            raw=data,
        )

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> LLMChatResult:
        model = model or self.default_model
        if not self.api_key:
            raise LLMAuthenticationError("未配置 API Key")
        if not model:
            raise LLMError("未配置模型")

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        return await self._post(payload, model)

    async def test_connection(self, model: Optional[str] = None) -> str:
        """发送 "Hello" 测试消息，返回实际使用的模型名"""
        model = model or self.default_model
        messages = [{"role": "user", "content": "Hello"}]
        result = await self.chat(messages, model=model, max_tokens=10)
        if not result.content.strip():
            raise LLMContentError("模型返回空内容，连接测试失败")
        return result.model or model

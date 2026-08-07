"""
LLM Router（V5.0 新增）

核心职责：自动 Fallback + 重试。

模型链：主模型 + Fallback 模型列表（自动去重）。
策略（与旧版 glm_analyzer/keyword_expander 行为对齐）：
  - LLMTimeoutError         → 当前模型重试（指数等待），重试耗尽后切换下一个模型
  - LLMRateLimitError       → 立即切换下一个模型（429/502/503 等价处理）
  - LLMModelUnavailableError→ 立即切换下一个模型
  - LLMAuthenticationError / LLMConnectionError / 其他异常 → 立即失败
  - 空内容：
      - finish_reason == "length"（截断）→ 切换下一个模型
      - 其他 → 当前模型重试
"""
import asyncio
import logging
from typing import List, Optional, Dict, Any

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

logger = logging.getLogger("llm.router")


class LLMRouter:
    """模型自动 Fallback 路由器"""

    def __init__(
        self,
        provider: BaseLLMProvider,
        max_retries: int = 2,
        retry_base_delay: float = 3.0,
    ):
        self.provider = provider
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay

    @staticmethod
    def _build_chain(
        model: Optional[str],
        fallback_models: Optional[List[str]],
    ) -> List[str]:
        """构建去重后的完整模型链：主模型在前"""
        chain: List[str] = []
        if model:
            chain.append(model)
        for m in fallback_models or []:
            m = m.strip()
            if m and m not in chain:
                chain.append(m)
        return chain

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        fallback_models: Optional[List[str]] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> Optional[LLMChatResult]:
        """按模型链调用，失败自动 Fallback。全部失败返回 None。"""
        chain = self._build_chain(
            model or self.provider.default_model, fallback_models
        )
        if not chain:
            logger.error("模型列表为空，无法调用")
            return None

        for model_idx, model_name in enumerate(chain):
            has_next = model_idx < len(chain) - 1

            for attempt in range(1, self.max_retries + 1):
                try:
                    result = await self.provider.chat(
                        messages,
                        model=model_name,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )

                    # 空内容处理
                    if not result.content.strip():
                        if result.finish_reason == "length" and has_next:
                            logger.warning(
                                f"[{model_name}] 内容截断，降级到 {chain[model_idx + 1]}"
                            )
                            break
                        logger.warning(f"[{model_name}] 返回空内容，重试 {attempt}/{self.max_retries}")
                        continue

                    return result

                except LLMTimeoutError:
                    if attempt < self.max_retries:
                        wait = self.retry_base_delay * attempt
                        logger.warning(
                            f"[{model_name}] 请求超时，{wait:.0f}s 后第 {attempt + 1} 次重试..."
                        )
                        await asyncio.sleep(wait)
                    elif has_next:
                        logger.warning(f"[{model_name}] 连续超时，降级到 {chain[model_idx + 1]}")
                        break
                    else:
                        logger.error(f"[{model_name}] 请求超时，所有模型均失败")
                        return None

                except LLMRateLimitError as e:
                    if has_next:
                        logger.warning(f"[{model_name}] 限流({e})，降级到 {chain[model_idx + 1]}")
                        break
                    logger.error(f"[{model_name}] 限流({e})，所有模型均失败")
                    return None

                except LLMModelUnavailableError as e:
                    if has_next:
                        logger.warning(f"[{model_name}] 模型不可用({e})，降级到 {chain[model_idx + 1]}")
                        break
                    logger.error(f"[{model_name}] 模型不可用({e})，所有模型均失败")
                    return None

                except LLMAuthenticationError as e:
                    logger.error(f"[{model_name}] API Key 无效: {e}")
                    return None

                except (LLMConnectionError, LLMContentError) as e:
                    logger.error(f"[{model_name}] 调用失败: {e}")
                    return None

                except Exception as e:  # 未知异常，不降级
                    logger.error(f"[{model_name}] 未知异常: {type(e).__name__}: {str(e)[:200]}")
                    return None

        return None

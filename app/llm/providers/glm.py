"""
GLM（智谱）Provider（V5.0 新增）

基于 OpenAI 兼容协议实现，内置智谱官方默认配置：
  - Base URL:  https://open.bigmodel.cn/api/paas/v4/chat/completions
  - 免费模型:  glm-4.7-flash / glm-4.6v-flash / glm-4-flash-250414
"""
from typing import List, Optional

from app.llm.config import GLM_DEFAULT_MODEL, GLM_DEFAULT_MODELS, GLM_DEFAULT_URL
from app.llm.providers.openai_compatible import OpenAICompatibleProvider


class GLMProvider(OpenAICompatibleProvider):
    """智谱 GLM Provider（OpenAI 兼容）"""

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        default_model: str = "",
        fallback_models: Optional[List[str]] = None,
        timeout: float = 120,
    ):
        super().__init__(
            api_key=api_key,
            base_url=base_url or GLM_DEFAULT_URL,
            default_model=default_model or GLM_DEFAULT_MODEL,
            fallback_models=fallback_models or list(GLM_DEFAULT_MODELS),
            timeout=timeout,
        )

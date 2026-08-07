"""
LLM 配置解析（V5.0 新增）

Round 2：从环境变量解析（兼容旧逻辑 GLM_API_KEY / DEEPSEEK_API_KEY 等）。
Round 3：resolve_config() 优先读取 user_api_config 表（service='llm'），再回退环境变量。
"""
import os
import json
import logging
from dataclasses import dataclass, field
from typing import List, Optional

GLM_DEFAULT_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
GLM_DEFAULT_MODEL = "glm-4.7-flash"
# GLM 官方支持的免费模型（Fallback 链默认值）
GLM_DEFAULT_MODELS = ["glm-4.7-flash", "glm-4.6v-flash", "glm-4-flash-250414"]

logger = logging.getLogger("llm.config")


@dataclass
class LLMConfig:
    """单次 LLM 调用所需的完整配置"""

    provider: str = "glm"
    api_key: str = ""
    base_url: str = ""
    default_model: str = ""
    fallback_models: List[str] = field(default_factory=list)


def get_env_config() -> LLMConfig:
    """从环境变量解析配置（向后兼容旧逻辑）"""
    api_key = os.environ.get("GLM_API_KEY", "").strip()
    if not api_key:
        # 向后兼容：旧的 DEEPSEEK_API_KEY
        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()

    base_url = os.environ.get("GLM_API_URL", "").strip() or GLM_DEFAULT_URL
    default_model = os.environ.get("GLM_MODEL", "").strip() or GLM_DEFAULT_MODEL
    fallback_models = os.environ.get("GLM_FALLBACK_MODELS", "").strip()
    fallback_list = [
        m.strip() for m in fallback_models.split(",") if m.strip()
    ] or list(GLM_DEFAULT_MODELS)

    return LLMConfig(
        provider="glm",
        api_key=api_key,
        base_url=base_url,
        default_model=default_model,
        fallback_models=fallback_list,
    )


def resolve_config(user_id: Optional[int] = None, db=None) -> LLMConfig:
    """解析某次 LLM 调用使用的配置。

    Round 3：优先读取该用户的 user_api_config（service='llm'）；
    用户未配置或未传 user_id 时回退环境变量。

    Args:
        user_id: 用户 ID（可选）。传入时按用户解析其自有 API Key。
        db: 数据库会话（可选）。不传时自动开启一个短会话。
    """
    if user_id:
        user_cfg = _load_user_llm_config(user_id, db)
        if user_cfg is not None:
            return user_cfg
    return get_env_config()


def _load_user_llm_config(user_id: int, db=None) -> Optional[LLMConfig]:
    """从 user_api_config 表读取用户 LLM 配置（已解密）"""
    from app.services.user_config import get_user_api_config, decrypt_secret, SERVICE_LLM

    close_session = False
    if db is None:
        from app.database import SessionLocal

        db = SessionLocal()
        close_session = True
    try:
        row = get_user_api_config(db, user_id, SERVICE_LLM)
        if row is None or not row.api_key or row.enabled == 0:
            return None

        api_key = decrypt_secret(row.api_key)
        if not api_key:
            return None

        fallback_models: List[str] = []
        if row.fallback_models:
            try:
                parsed = json.loads(row.fallback_models)
                if isinstance(parsed, list):
                    fallback_models = [
                        m for m in parsed if isinstance(m, str) and m.strip()
                    ]
            except (json.JSONDecodeError, TypeError):
                logger.warning("用户 %s 的 fallback_models 解析失败，使用空列表", user_id)

        return LLMConfig(
            provider=(row.provider or "glm").strip() or "glm",
            api_key=api_key,
            base_url=(row.base_url or "").strip(),
            default_model=(row.default_model or "").strip(),
            fallback_models=fallback_models,
        )
    finally:
        if close_session:
            db.close()

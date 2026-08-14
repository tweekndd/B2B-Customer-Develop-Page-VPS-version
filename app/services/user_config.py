"""
用户 API Key 配置服务（Round 3 新增）
======================================
统一管理用户自定义的外部 API Key（LLM / Hunter / Tomba / Prospeo /
Tavily / SerpAPI / SearXNG / Firecrawl），实现 Multi-user SaaS 架构。

设计：
- 通用表 user_api_config：每用户每服务一行，API Key 使用 Fernet 对称加密存储。
- 加密密钥：优先读环境变量 API_CONFIG_ENCRYPTION_KEY；未设置时自动生成并
  持久化到 app/.config_encryption_key 文件（保证重启后仍可解密）。
- get_effective_*() 系列：优先返回用户配置，未配置时回退环境变量（向后兼容）。
"""
import os
import json
from typing import Optional, List, Dict, Any

from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from app.database import UserApiConfig

logger = __import__("logging").getLogger("user_config")

# ── 服务名称常量 ──
SERVICE_LLM = "llm"
SERVICE_HUNTER = "hunter"
SERVICE_TOMBA = "tomba"
SERVICE_PROSPEO = "prospeo"
SERVICE_TAVILY = "tavily"
SERVICE_SERPAPI = "serpapi"
SERVICE_SEARXNG = "searxng"
SERVICE_FIRECRAWL = "firecrawl"
# LinkedIn OAuth（api_key=Client ID，api_secret=Primary Client Secret）
SERVICE_LINKEDIN = "linkedin"
# Gmail OAuth（api_key=Client ID，api_secret=Client Secret，发信检测）
SERVICE_GMAIL = "gmail"
# 用户偏好的搜索引擎（base_url 字段存引擎名: tavily/serpapi/searxng）
SERVICE_SEARCH_ENGINE = "search_engine"

ALL_SERVICES = [
    SERVICE_LLM, SERVICE_HUNTER, SERVICE_TOMBA, SERVICE_PROSPEO,
    SERVICE_TAVILY, SERVICE_SERPAPI, SERVICE_SEARXNG,
    SERVICE_FIRECRAWL, SERVICE_LINKEDIN, SERVICE_GMAIL, SERVICE_SEARCH_ENGINE,
]

# 服务 → 环境变量名（按优先级排序）映射
_ENV_KEY_MAP = {
    SERVICE_LLM: ("GLM_API_KEY", "DEEPSEEK_API_KEY"),
    SERVICE_HUNTER: ("HUNTER_API_KEY",),
    SERVICE_TOMBA: ("TOMBA_API_KEY",),
    SERVICE_PROSPEO: ("PROSPEO_API_KEY",),
    SERVICE_TAVILY: ("TAVILY_API_KEY",),
    SERVICE_SERPAPI: ("SERPAPI_API_KEY",),
    SERVICE_FIRECRAWL: ("FIRECRAWL_API_KEY",),
    SERVICE_LINKEDIN: ("LINKEDIN_CLIENT_ID",),
    SERVICE_GMAIL: ("GMAIL_CLIENT_ID",),
}

_fernet: Optional[Fernet] = None


# ═══════════════════════════════════════════
# 加密（Fernet）
# ═══════════════════════════════════════════

def _key_file_path() -> str:
    """密钥持久化文件路径（app/.config_encryption_key）"""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".config_encryption_key",
    )


def _load_or_create_file_key() -> str:
    """从文件加载密钥，不存在则生成并持久化（保证重启后仍可解密）"""
    key_file = _key_file_path()
    try:
        if os.path.exists(key_file):
            with open(key_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return content
    except OSError:
        pass
    key = Fernet.generate_key().decode()
    try:
        with open(key_file, "w", encoding="utf-8") as f:
            f.write(key)
    except OSError:
        logger.warning("无法持久化加密密钥到 %s，重启后已保存的 Key 将无法解密", key_file)
    return key


def _get_fernet() -> Fernet:
    """获取（并缓存）Fernet 加密器"""
    global _fernet
    if _fernet is not None:
        return _fernet

    key_str = os.environ.get("API_CONFIG_ENCRYPTION_KEY", "").strip()
    if not key_str:
        key_str = _load_or_create_file_key()

    try:
        _fernet = Fernet(key_str.encode())
    except Exception:
        # 环境变量不是合法 Fernet Key 时回退文件/新生成密钥
        _fernet = Fernet(_load_or_create_file_key().encode())
    return _fernet


def encrypt_secret(plaintext: str) -> str:
    """加密敏感字段，空值返回空字符串"""
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    """解密敏感字段，失败或空值返回空字符串"""
    if not token:
        return ""
    try:
        return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except Exception:
        return ""


def mask_secret(value: str) -> str:
    """脱敏显示：仅保留后4位"""
    if not value:
        return ""
    if len(value) <= 4:
        return "****"
    return "****" + value[-4:]


# ═══════════════════════════════════════════
# 环境变量默认值
# ═══════════════════════════════════════════

def get_env_api_key(service: str) -> str:
    """读取环境变量的默认 Key（兼容旧逻辑）"""
    names = _ENV_KEY_MAP.get(service, ())
    for name in names:
        val = os.environ.get(name, "").strip()
        if val:
            return val
    return ""


def get_env_api_secret(service: str) -> str:
    """读取环境变量的默认 Secret"""
    if service == SERVICE_TOMBA:
        return os.environ.get("TOMBA_API_SECRET", "").strip()
    if service == SERVICE_LINKEDIN:
        return os.environ.get("LINKEDIN_CLIENT_SECRET", "").strip()
    if service == SERVICE_GMAIL:
        return os.environ.get("GMAIL_CLIENT_SECRET", "").strip()
    return ""


def get_env_base_url(service: str) -> str:
    """读取环境变量的默认 Base URL"""
    if service == SERVICE_SEARXNG:
        return os.environ.get("SEARXNG_URL", "").strip()
    if service == SERVICE_LLM:
        return os.environ.get("GLM_API_URL", "").strip()
    if service == SERVICE_FIRECRAWL:
        return os.environ.get("READER_BASE_URL", "").strip()
    return ""


# ═══════════════════════════════════════════
# 配置 CRUD
# ═══════════════════════════════════════════

def get_user_api_config(db: Session, user_id: int, service: str) -> Optional[UserApiConfig]:
    """查询某用户某服务的配置行"""
    if db is None or user_id is None:
        return None
    return db.query(UserApiConfig).filter(
        UserApiConfig.user_id == user_id,
        UserApiConfig.service == service,
    ).first()


def set_user_api_config(
    db: Session,
    user_id: int,
    service: str,
    *,
    api_key: str = None,
    api_secret: str = None,
    base_url: str = None,
    provider: str = None,
    default_model: str = None,
    fallback_models=None,
    enabled: int = None,
) -> UserApiConfig:
    """保存（新增或更新）用户某服务的配置，Key 加密存储"""
    if db is None or user_id is None:
        raise ValueError("保存用户配置需要 db 和 user_id")

    row = get_user_api_config(db, user_id, service)
    if row is None:
        row = UserApiConfig(user_id=user_id, service=service)
        db.add(row)

    if api_key is not None:
        row.api_key = encrypt_secret(api_key.strip())
    if api_secret is not None:
        row.api_secret = encrypt_secret(api_secret.strip())
    if base_url is not None:
        row.base_url = (base_url or "").strip() or None
    if provider is not None:
        row.provider = (provider or "").strip() or None
    if default_model is not None:
        row.default_model = (default_model or "").strip() or None
    if fallback_models is not None:
        if isinstance(fallback_models, (list, tuple)):
            cleaned = [m for m in fallback_models if isinstance(m, str) and m.strip()]
            row.fallback_models = json.dumps(cleaned, ensure_ascii=False)
        elif isinstance(fallback_models, str):
            row.fallback_models = fallback_models.strip() or None
        else:
            row.fallback_models = None
    if enabled is not None:
        row.enabled = 1 if enabled else 0

    db.commit()
    db.refresh(row)
    return row


def delete_user_api_config(db: Session, user_id: int, service: str) -> bool:
    """删除用户某服务的配置，返回是否删除成功"""
    row = get_user_api_config(db, user_id, service)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def _parse_fallback_models(raw) -> List[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [m for m in parsed if isinstance(m, str)]
    except (json.JSONDecodeError, TypeError):
        return [m.strip() for m in str(raw).split(",") if m.strip()]
    return []


def build_config_payload(row: UserApiConfig) -> Dict[str, Any]:
    """构造对外返回的配置（Key 已脱敏）"""
    api_key = decrypt_secret(row.api_key)
    api_secret = decrypt_secret(row.api_secret)
    return {
        "service": row.service,
        "provider": row.provider,
        "api_key": mask_secret(api_key),
        "api_key_set": bool(api_key),
        "api_secret": mask_secret(api_secret),
        "api_secret_set": bool(api_secret),
        "base_url": row.base_url,
        "default_model": row.default_model,
        "fallback_models": _parse_fallback_models(row.fallback_models),
        "enabled": bool(row.enabled),
        "configured": bool(api_key) or bool(api_secret) or bool(row.base_url),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_user_configs(db: Session, user_id: int) -> List[Dict[str, Any]]:
    """列出用户所有已配置的服务（脱敏）"""
    rows = db.query(UserApiConfig).filter(
        UserApiConfig.user_id == user_id
    ).order_by(UserApiConfig.service).all()
    return [build_config_payload(r) for r in rows]


# ═══════════════════════════════════════════
# 生效配置解析（用户配置优先，回退环境变量）
# ═══════════════════════════════════════════

def get_effective_api_key(db: Session, user_id: Optional[int], service: str) -> str:
    """返回用户生效的 API Key（用户配置解密值，未配置时回退环境变量）"""
    row = get_user_api_config(db, user_id, service)
    if row and row.api_key and row.enabled:
        key = decrypt_secret(row.api_key)
        if key:
            return key
    return get_env_api_key(service)


def get_effective_api_secret(db: Session, user_id: Optional[int], service: str) -> str:
    """返回用户生效的 API Secret（用户配置解密值，未配置时回退环境变量）"""
    row = get_user_api_config(db, user_id, service)
    if row and row.api_secret and row.enabled:
        secret = decrypt_secret(row.api_secret)
        if secret:
            return secret
    return get_env_api_secret(service)


def get_effective_base_url(db: Session, user_id: Optional[int], service: str) -> str:
    """返回用户生效的 Base URL（用户配置，未配置时回退环境变量）"""
    row = get_user_api_config(db, user_id, service)
    if row and row.base_url and row.enabled:
        return row.base_url.strip()
    return get_env_base_url(service)


def resolve_service_config(
    db: Session, user_id: Optional[int], service: str
) -> Dict[str, Any]:
    """汇总某服务的生效配置（供调试/前端展示）"""
    return {
        "service": service,
        "api_key_set": bool(get_effective_api_key(db, user_id, service)),
        "api_secret_set": bool(get_effective_api_secret(db, user_id, service)),
        "base_url": get_effective_base_url(db, user_id, service),
        "configured": bool(
            get_effective_api_key(db, user_id, service)
            or get_effective_api_secret(db, user_id, service)
            or get_effective_base_url(db, user_id, service)
        ),
    }


def _user_has_search_config(db: Session, user_id: Optional[int]) -> bool:
    """用户是否配置了任意搜索相关项（决定是否走用户专属引擎解析）"""
    if user_id is None or db is None:
        return False
    for svc in (SERVICE_SERPAPI, SERVICE_TAVILY, SERVICE_SEARXNG, SERVICE_SEARCH_ENGINE):
        if get_user_api_config(db, user_id, svc) is not None:
            return True
    return False


def resolve_search_config(
    db: Session,
    user_id: Optional[int] = None,
    global_engine: str = "none",
) -> Dict[str, Any]:
    """解析用户使用的搜索引擎配置。

    优先级：
    1. 用户偏好的引擎（search_engine 配置）且其 Key/URL 可用
    2. 用户配置的 Key 自动推导（tavily > serpapi > searxng）
    3. 未配置用户 → 回退全局运行时引擎（global_engine）+ 环境变量 Key
    """
    serpapi_key = get_effective_api_key(db, user_id, SERVICE_SERPAPI)
    tavily_key = get_effective_api_key(db, user_id, SERVICE_TAVILY)
    searxng_url = get_effective_base_url(db, user_id, SERVICE_SEARXNG)

    preferred = ""
    row = get_user_api_config(db, user_id, SERVICE_SEARCH_ENGINE)
    if row and row.base_url:
        preferred = row.base_url.strip().lower()

    available = {
        "tavily": bool(tavily_key),
        "serpapi": bool(serpapi_key),
        "searxng": bool(searxng_url),
    }

    user_configured = _user_has_search_config(db, user_id)

    if user_configured:
        if preferred in ("tavily", "serpapi", "searxng") and available.get(preferred):
            engine = preferred
        elif tavily_key:
            engine = "tavily"
        elif serpapi_key:
            engine = "serpapi"
        elif searxng_url:
            engine = "searxng"
        else:
            engine = "none"
        source = "user"
    else:
        engine = global_engine if global_engine in ("tavily", "serpapi", "searxng") else "none"
        source = "global"

    return {
        "engine": engine,
        "source": source,
        "preferred": preferred,
        "available": available,
        "serpapi_key": serpapi_key,
        "tavily_key": tavily_key,
        "searxng_url": searxng_url,
    }

"""
用户 API Key 配置路由（Round 3 新增）
提供每个用户独立的外部服务配置管理：
LLM / Hunter / Tomba / Prospeo / Tavily / SerpAPI / SearXNG / Firecrawl / 搜索偏好

- Key 加密存储（Fernet）
- 返回内容脱敏
- 未配置时自动回退服务器环境变量（向后兼容）
"""
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import require_user
from app.services import user_config as uc

router = APIRouter(tags=["用户 API 配置"])


class SaveConfigRequest(BaseModel):
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    base_url: Optional[str] = None
    provider: Optional[str] = None
    default_model: Optional[str] = None
    fallback_models: Optional[List[str]] = None
    enabled: Optional[bool] = None


def _validate_service(service: str) -> str:
    """校验服务名是否合法"""
    if service not in uc.ALL_SERVICES:
        raise HTTPException(
            status_code=400,
            detail=f"无效的服务: {service}，仅支持: {', '.join(uc.ALL_SERVICES)}",
        )
    return service


# ═══════════════════════════════════════════
# 配置列表 / 详情
# ═══════════════════════════════════════════

@router.get("/user-config/")
def list_my_configs(
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """列出当前用户已保存的 API 配置（脱敏）与各服务生效状态"""
    saved = uc.list_user_configs(db, user.id)
    saved_services = {s["service"] for s in saved}

    effective = {}
    for svc in uc.ALL_SERVICES:
        if svc == uc.SERVICE_SEARCH_ENGINE:
            sc = uc.resolve_search_config(db, user.id)
            effective[svc] = {
                "preferred": sc.get("preferred", ""),
                "engine": sc.get("engine", "none"),
                "source": sc.get("source", "global"),
                "configured": sc.get("engine") != "none",
            }
        else:
            r = uc.resolve_service_config(db, user.id, svc)
            effective[svc] = {
                "configured": r["configured"],
                "api_key_set": r["api_key_set"],
                "api_secret_set": r["api_secret_set"],
                "base_url": r["base_url"],
                "source": "user" if svc in saved_services else "global",
            }

    return {
        "saved_configs": saved,
        "effective": effective,
    }


@router.get("/user-config/{service}")
def get_my_config(
    service: str,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """获取当前用户指定服务的配置（脱敏）+ 生效解析结果"""
    service = _validate_service(service)
    row = uc.get_user_api_config(db, user.id, service)
    payload = uc.build_config_payload(row) if row else {
        "service": service,
        "configured": False,
        "api_key": "",
        "api_key_set": False,
        "api_secret": "",
        "api_secret_set": False,
        "base_url": None,
        "provider": None,
        "default_model": None,
        "fallback_models": [],
        "enabled": False,
        "updated_at": None,
    }
    if service == uc.SERVICE_SEARCH_ENGINE:
        sc = uc.resolve_search_config(db, user.id)
        payload["effective"] = {
            "engine": sc.get("engine", "none"),
            "source": sc.get("source", "global"),
            "available": sc.get("available", {}),
        }
    else:
        payload["effective"] = uc.resolve_service_config(db, user.id, service)
    return payload


# ═══════════════════════════════════════════
# 保存 / 删除
# ═══════════════════════════════════════════

@router.post("/user-config/{service}")
def save_my_config(
    service: str,
    req: SaveConfigRequest,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """保存当前用户指定服务的 API 配置（Key 加密存储，返回脱敏结果）"""
    service = _validate_service(service)
    if service == uc.SERVICE_SEARCH_ENGINE:
        raise HTTPException(status_code=400, detail="搜索引擎偏好请通过 /api/discovery/search-engine 切换")

    row = uc.set_user_api_config(
        db,
        user.id,
        service,
        api_key=req.api_key,
        api_secret=req.api_secret,
        base_url=req.base_url,
        provider=req.provider,
        default_model=req.default_model,
        fallback_models=req.fallback_models,
        enabled=req.enabled,
    )
    return {
        "message": f"服务「{service}」配置已保存",
        "config": uc.build_config_payload(row),
    }


@router.delete("/user-config/{service}")
def delete_my_config(
    service: str,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """删除当前用户指定服务的配置（删除后回退服务器环境变量）"""
    service = _validate_service(service)
    deleted = uc.delete_user_api_config(db, user.id, service)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"服务「{service}」未配置，无需删除")
    return {"message": f"服务「{service}」配置已删除"}


# ═══════════════════════════════════════════
# LLM 连通性测试
# ═══════════════════════════════════════════

class LLMTestRequest(BaseModel):
    provider: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    fallback_models: Optional[List[str]] = None


@router.post("/user-config/llm/test")
async def test_llm_connection(
    req: LLMTestRequest,
    user=Depends(require_user),
):
    """测试 LLM 连通性。

    传入 provider/api_key/base_url/model 时使用临时配置（不保存）；
    留空则使用当前用户已保存的 LLM 配置（未配置则回退环境变量）。
    """
    from app.llm.manager import get_llm_manager
    from app.llm.exceptions import LLMError

    try:
        model = await get_llm_manager().test_connection(
            user_id=user.id,
            provider=req.provider,
            api_key=req.api_key,
            base_url=req.base_url,
            model=req.model,
            fallback_models=req.fallback_models,
        )
        return {"success": True, "message": f"连接成功，模型: {model}"}
    except LLMError as e:
        return {"success": False, "message": str(e)}
    except Exception as e:
        return {"success": False, "message": f"连接失败: {str(e)[:200]}"}

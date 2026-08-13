"""
LinkedIn 公司主页发现 API（V5.1 新增）

候选发现 → 用户确认（is_verified=1）→ 手动新增/编辑/删除。
OAuth 2.0（3-legged）授权后可用 Organizations Lookup API 刷新组织详情。
"""
import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db, Customer
from app.auth import require_user
from app.services.linkedin_service import (
    discover_company_pages,
    get_customer_social_profiles,
    upsert_social_profile,
    set_profile_verified,
    delete_social_profile,
)
from app.services.linkedin_oauth_service import (
    LinkedInOAuthError,
    build_authorization_url,
    exchange_code_for_token,
    get_client_credentials,
    get_oauth_status,
    get_redirect_uri,
    save_access_token,
    delete_access_token,
    resolve_profile_with_official_api,
)

router = APIRouter(tags=["linkedin"])

# OAuth state 会话键
_SESSION_STATE_KEY = "linkedin_oauth_state"
_SESSION_NEXT_KEY = "linkedin_oauth_next"


class AddSocialProfileRequest(BaseModel):
    profile_url: str
    display_name: Optional[str] = None
    website_url: Optional[str] = None


class UpdateSocialProfileRequest(BaseModel):
    is_verified: Optional[bool] = None
    display_name: Optional[str] = None


def _profile_to_dict(p) -> dict:
    return {
        "id": p.id,
        "customer_id": p.customer_id,
        "platform": p.platform or "linkedin",
        "profile_type": p.profile_type or "company",
        "profile_url": p.profile_url,
        "vanity_name": p.vanity_name or "",
        "external_id": p.external_id or "",
        "display_name": p.display_name or "",
        "website_url": p.website_url or "",
        "logo_url": p.logo_url or "",
        "staff_count_range": p.staff_count_range or "",
        "source": p.source or "search",
        "confidence": p.confidence or 0.0,
        "is_verified": bool(p.is_verified),
        "last_fetched_at": p.last_fetched_at.isoformat() if p.last_fetched_at else None,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def _get_customer_or_404(db: Session, customer_id: int) -> Customer:
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="客户不存在")
    return customer


@router.get("/customers/{customer_id}/social-profiles")
def list_social_profiles(
    customer_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """获取客户所有社交主页记录（已确认优先）"""
    _get_customer_or_404(db, customer_id)
    profiles = get_customer_social_profiles(db, customer_id)
    return {
        "profiles": [_profile_to_dict(p) for p in profiles],
        "total": len(profiles),
    }


@router.post("/customers/{customer_id}/linkedin/discover")
async def discover_linkedin(
    customer_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """触发 LinkedIn 公司主页候选发现（返回候选，不自动确认）"""
    customer = _get_customer_or_404(db, customer_id)
    candidates = await discover_company_pages(
        company_name=customer.company_name or "",
        website=customer.website or "",
        country=customer.country or "",
        city=customer.city or "",
        discovery_keyword=customer.discovery_keyword or "",
        user_id=user.id,
        db=db,
    )
    return {"candidates": candidates, "total": len(candidates)}


@router.post("/customers/{customer_id}/social-profiles")
def add_social_profile(
    customer_id: int,
    req: AddSocialProfileRequest,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """手动新增/粘贴 LinkedIn 公司主页（来源固定为 manual）"""
    _get_customer_or_404(db, customer_id)
    try:
        profile = upsert_social_profile(
            db,
            customer_id,
            req.profile_url,
            source="manual",
            display_name=req.display_name,
            website_url=req.website_url,
            is_verified=False,
            created_by_user_id=user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return {"message": "社交主页已保存", "profile": _profile_to_dict(profile)}


@router.put("/social-profiles/{profile_id}")
def update_social_profile(
    profile_id: int,
    req: UpdateSocialProfileRequest,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """编辑候选：确认/取消确认主页、修改显示名称"""
    try:
        profile = set_profile_verified(db, profile_id, bool(req.is_verified))
        if req.display_name is not None:
            profile.display_name = req.display_name
            profile.updated_at = datetime.datetime.utcnow()
    except LookupError:
        raise HTTPException(status_code=404, detail="社交主页记录不存在")
    db.commit()
    return {"message": "已更新", "profile": _profile_to_dict(profile)}


@router.delete("/social-profiles/{profile_id}")
def remove_social_profile(
    profile_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """删除候选社交主页"""
    if not delete_social_profile(db, profile_id):
        raise HTTPException(status_code=404, detail="社交主页记录不存在")
    db.commit()
    return {"message": "已删除"}


# ═══════════════════════════════════════════
# LinkedIn OAuth 2.0（3-legged）授权
# ═══════════════════════════════════════════

def _safe_next(next_path: Optional[str]) -> str:
    """安全校验回调跳转目标：仅允许站内相对路径，防开放重定向"""
    if not next_path or not next_path.startswith("/") or next_path.startswith("//"):
        return "/settings"
    return next_path


@router.get("/linkedin/oauth/status")
def oauth_status(
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """LinkedIn OAuth 状态（凭据是否配置 + 是否已授权）"""
    return get_oauth_status(db, user.id)


@router.get("/linkedin/oauth/start")
def oauth_start(
    request: Request,
    next: str = None,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """启动 LinkedIn OAuth：生成 state（防 CSRF）并重定向到 LinkedIn 授权页"""
    import secrets
    client_id, _ = get_client_credentials(db, user.id)
    if not client_id:
        raise HTTPException(status_code=400, detail="尚未配置 LinkedIn Client ID，请先在设置页保存")

    state = secrets.token_urlsafe(32)
    request.session[_SESSION_STATE_KEY] = state
    request.session[_SESSION_NEXT_KEY] = _safe_next(next)

    redirect_uri = get_redirect_uri(request)
    url = build_authorization_url(client_id, redirect_uri, state)
    return RedirectResponse(url=url, status_code=302)


@router.get("/linkedin/oauth/callback")
def oauth_callback(
    request: Request,
    code: str = None,
    state: str = None,
    error: str = None,
    error_description: str = None,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """LinkedIn OAuth 回调：校验 state → 兑换 token → 跳转回设置页"""
    if error:
        raise HTTPException(status_code=400, detail=f"LinkedIn 授权被拒绝: {error_description or error}")

    expected_state = request.session.pop(_SESSION_STATE_KEY, None)
    next_path = request.session.pop(_SESSION_NEXT_KEY, "/settings")
    if not state or not expected_state or state != expected_state:
        raise HTTPException(status_code=400, detail="state 校验失败，请重新发起授权（防 CSRF）")
    if not code:
        raise HTTPException(status_code=400, detail="缺少授权码 code")

    client_id, client_secret = get_client_credentials(db, user.id)
    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="LinkedIn Client ID / Secret 未配置")

    redirect_uri = get_redirect_uri(request)
    try:
        data = exchange_code_for_token(code, client_id, client_secret, redirect_uri)
    except LinkedInOAuthError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))

    save_access_token(
        db,
        user.id,
        data.get("access_token", ""),
        scope=data.get("scope"),
        expires_in=data.get("expires_in"),
    )
    return RedirectResponse(url=next_path, status_code=302)


@router.post("/linkedin/oauth/disconnect")
def oauth_disconnect(
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """断开 LinkedIn 授权（删除本地 token）"""
    if delete_access_token(db, user.id):
        return {"message": "已断开 LinkedIn 授权"}
    return {"message": "当前未授权"}


@router.post("/social-profiles/{profile_id}/resolve")
def resolve_profile(
    profile_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """调用 LinkedIn 官方 Organizations Lookup API 刷新组织详情（需已授权）"""
    from app.database import CustomerSocialProfile

    profile = db.query(CustomerSocialProfile).filter(CustomerSocialProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="社交主页记录不存在")

    try:
        resolve_profile_with_official_api(db, user.id, profile)
    except LinkedInOAuthError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    db.commit()
    return {"message": "已用官方 API 刷新组织详情", "profile": _profile_to_dict(profile)}

"""
LinkedIn OAuth 2.0 与 Organizations Lookup API 服务（V5.1 新增）

3-legged OAuth 授权流程：
    1. 用户配置 Client ID / Primary Client Secret（user_api_config，Fernet 加密）
    2. GET /api/linkedin/oauth/start → 跳转 LinkedIn 授权页（state 防 CSRF）
    3. GET /api/linkedin/oauth/callback → 校验 state → 兑换 access token → 加密存储
    4. POST /api/social-profiles/{id}/resolve → 调用 Organizations Lookup API
       （GET /rest/organizations?q=vanityName&vanityName=...）刷新组织详情

参考：LinkedIn Organizations and Brands Overview / Organization Lookup API
（https://learn.microsoft.com/en-us/linkedin/marketing/community-management/organizations）
"""
import datetime
import logging
import os
from typing import Optional, Tuple

import httpx
from sqlalchemy.orm import Session

from app.database import LinkedInOAuthToken, CustomerSocialProfile
from app.services import user_config as uc
from app.services.user_config import encrypt_secret, decrypt_secret

logger = logging.getLogger("linkedin_oauth")

# ── LinkedIn 端点 ──
AUTHORIZE_URL = "https://www.linkedin.com/oauth/v2/authorization"
ACCESS_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
API_BASE = "https://api.linkedin.com"

# Organization Lookup API 所需 scope（读取组织非管理员字段）
OAUTH_SCOPE = "r_organization_social"

# LinkedIn-Version 请求头（当前 moniker li-lms-2026-07，可用环境变量覆盖）
LINKEDIN_API_VERSION = os.environ.get("LINKEDIN_API_VERSION", "202607")


class LinkedInOAuthError(Exception):
    """LinkedIn OAuth / API 异常"""

    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code


# ═══════════════════════════════════════════
# 凭据（用户配置优先，环境变量回退）
# ═══════════════════════════════════════════

def get_client_credentials(db: Session, user_id: Optional[int]) -> Tuple[str, str]:
    """返回 (client_id, client_secret)，用户配置优先，回退环境变量"""
    client_id = uc.get_effective_api_key(db, user_id, uc.SERVICE_LINKEDIN)
    client_secret = uc.get_effective_api_secret(db, user_id, uc.SERVICE_LINKEDIN)
    return client_id, client_secret


def is_credentials_configured(db: Session, user_id: Optional[int]) -> bool:
    client_id, client_secret = get_client_credentials(db, user_id)
    return bool(client_id and client_secret)


def get_redirect_uri(request) -> str:
    """从请求构造 OAuth 回调地址（生产环境经 Nginx HTTPS 反代）"""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", request.url.netloc)
    return f"{scheme}://{host}/api/linkedin/oauth/callback"


def build_authorization_url(client_id: str, redirect_uri: str, state: str) -> str:
    """构造 LinkedIn 授权页 URL（3-legged，response_type=code）"""
    from urllib.parse import urlencode
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": OAUTH_SCOPE,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


# ═══════════════════════════════════════════
# Token 兑换与存取
# ═══════════════════════════════════════════

def exchange_code_for_token(
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict:
    """用授权码兑换 access token（LinkedIn 3-legged）"""
    try:
        resp = httpx.post(
            ACCESS_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=30,
        )
    except httpx.HTTPError as e:
        raise LinkedInOAuthError(f"请求 LinkedIn 失败: {str(e)[:200]}")

    if resp.status_code != 200:
        logger.warning("LinkedIn token 兑换失败 HTTP %s: %s", resp.status_code, resp.text[:300])
        raise LinkedInOAuthError(
            f"LinkedIn 授权失败（HTTP {resp.status_code}），请检查 Client ID / Secret 是否正确",
            status_code=400 if resp.status_code in (400, 401) else 502,
        )
    try:
        data = resp.json()
    except Exception:
        raise LinkedInOAuthError("LinkedIn 返回了无法解析的响应")
    token = data.get("access_token")
    if not token:
        raise LinkedInOAuthError("LinkedIn 未返回 access_token")
    return data


def save_access_token(
    db: Session,
    user_id: int,
    access_token: str,
    scope: Optional[str] = None,
    expires_in: Optional[int] = None,
):
    """保存（覆盖）用户 LinkedIn access token，加密存储"""
    row = db.query(LinkedInOAuthToken).filter(LinkedInOAuthToken.user_id == user_id).first()
    now = datetime.datetime.utcnow()
    if row is None:
        row = LinkedInOAuthToken(user_id=user_id, created_at=now)
        db.add(row)
    row.access_token_encrypted = encrypt_secret(access_token)
    row.scope = scope or OAUTH_SCOPE
    row.expires_at = now + datetime.timedelta(seconds=expires_in) if expires_in else None
    row.updated_at = now
    db.commit()
    return row


def get_access_token(db: Session, user_id: int) -> Optional[str]:
    """获取用户有效 access token（未授权或已过期返回 None）"""
    row = db.query(LinkedInOAuthToken).filter(LinkedInOAuthToken.user_id == user_id).first()
    if row is None or not row.access_token_encrypted:
        return None
    if row.expires_at and row.expires_at <= datetime.datetime.utcnow():
        return None
    return decrypt_secret(row.access_token_encrypted) or None


def delete_access_token(db: Session, user_id: int) -> bool:
    """删除用户 OAuth token（断开授权）"""
    row = db.query(LinkedInOAuthToken).filter(LinkedInOAuthToken.user_id == user_id).first()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def get_oauth_status(db: Session, user_id: int) -> dict:
    """当前用户 LinkedIn OAuth 状态（供设置页展示）"""
    row = db.query(LinkedInOAuthToken).filter(LinkedInOAuthToken.user_id == user_id).first()
    authorized = False
    expires_at = None
    if row and row.access_token_encrypted:
        if row.expires_at and row.expires_at > datetime.datetime.utcnow():
            authorized = True
            expires_at = row.expires_at.isoformat()
    return {
        "client_configured": is_credentials_configured(db, user_id),
        "authorized": authorized,
        "expires_at": expires_at,
        "scope": row.scope if row else None,
    }


# ═══════════════════════════════════════════
# Organizations Lookup API
# ═══════════════════════════════════════════

def lookup_organization_by_vanity_name(
    access_token: str,
    vanity_name: str,
) -> Optional[dict]:
    """按 vanity name 查询组织（GET /rest/organizations?q=vanityName）

    返回元素（organizationUrn / localizedName / vanityName / logoV2 /
    locations / staffCountRange / localizedWebsite 等），未找到返回 None。
    """
    params = {"q": "vanityName", "vanityName": vanity_name}
    headers = {
        "Authorization": f"Bearer {access_token}",
        "LinkedIn-Version": LINKEDIN_API_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
    }
    try:
        resp = httpx.get(
            f"{API_BASE}/rest/organizations",
            params=params,
            headers=headers,
            timeout=30,
        )
    except httpx.HTTPError as e:
        raise LinkedInOAuthError(f"请求 LinkedIn API 失败: {str(e)[:200]}")

    if resp.status_code == 401 or resp.status_code == 403:
        raise LinkedInOAuthError(
            "LinkedIn 授权失效或权限不足，请重新授权（r_organization_social scope）",
            status_code=401,
        )
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        logger.warning("LinkedIn Lookup HTTP %s: %s", resp.status_code, resp.text[:300])
        raise LinkedInOAuthError(f"LinkedIn Lookup 失败（HTTP {resp.status_code}）", status_code=502)

    try:
        data = resp.json()
    except Exception:
        raise LinkedInOAuthError("LinkedIn 返回了无法解析的响应")

    elements = data.get("elements") or []
    return elements[0] if elements else None


def _parse_lookup_result(raw: dict) -> dict:
    """从 Lookup API 元素提取可保存字段（容错解析不同版本响应）"""
    result: dict = {}

    urn = raw.get("organizationUrn") or raw.get("id") or ""
    if urn:
        result["external_id"] = str(urn)

    name = raw.get("localizedName") or raw.get("name") or ""
    if name:
        result["display_name"] = str(name)

    vanity = raw.get("vanityName") or ""
    if vanity:
        result["vanity_name"] = str(vanity)

    # 官网（不同版本字段名不同，容错处理）
    website_raw = (
        raw.get("localizedWebsite")
        or raw.get("website")
        or raw.get("websiteUrl")
        or {}
    )
    if isinstance(website_raw, dict):
        url = (
            website_raw.get("localized", {}).get("en_US")
            if website_raw.get("localized")
            else (website_raw.get("url") or website_raw.get("value"))
        )
    else:
        url = website_raw
    if url:
        result["website_url"] = str(url)

    # Logo（digitalmediaAsset URN，无图 URL 时为 URN 字符串）
    logo = raw.get("logoV2") or raw.get("logo") or {}
    if isinstance(logo, dict):
        urn_candidate = logo.get("original") or logo.get("urn") or ""
        if urn_candidate:
            result["logo_url"] = str(urn_candidate)

    # 地点
    locations = raw.get("locations") or []
    if locations:
        result["location_json"] = str(locations)[:2000]

    # 员工规模范围
    staff = raw.get("staffCountRange") or {}
    if isinstance(staff, dict) and (staff.get("start") is not None or staff.get("end") is not None):
        start = staff.get("start")
        end = staff.get("end")
        if start is not None and end is not None:
            result["staff_count_range"] = f"{start}-{end}"
        elif start is not None:
            result["staff_count_range"] = f"{start}+"

    return result


def resolve_profile_with_official_api(
    db: Session,
    user_id: int,
    profile: CustomerSocialProfile,
) -> CustomerSocialProfile:
    """用官方 API 刷新候选组织详情（需已授权）"""
    token = get_access_token(db, user_id)
    if not token:
        raise LinkedInOAuthError(
            "尚未完成 LinkedIn OAuth 授权，请先在设置页授权",
            status_code=401,
        )

    vanity = profile.vanity_name
    if not vanity:
        raise LinkedInOAuthError("该候选没有 vanity name，无法调用官方 API", status_code=400)

    raw = lookup_organization_by_vanity_name(token, vanity)
    if raw is None:
        raise LinkedInOAuthError(f"LinkedIn 中未找到组织 {vanity}", status_code=404)

    fields = _parse_lookup_result(raw)
    now = datetime.datetime.utcnow()
    profile.vanity_name = fields.get("vanity_name") or profile.vanity_name
    profile.external_id = fields.get("external_id") or profile.external_id
    profile.display_name = fields.get("display_name") or profile.display_name
    profile.website_url = fields.get("website_url") or profile.website_url
    profile.logo_url = fields.get("logo_url") or profile.logo_url
    profile.location_json = fields.get("location_json") or profile.location_json
    profile.staff_count_range = fields.get("staff_count_range") or profile.staff_count_range
    profile.source = "official_api"
    profile.last_fetched_at = now
    profile.updated_at = now
    db.flush()
    return profile

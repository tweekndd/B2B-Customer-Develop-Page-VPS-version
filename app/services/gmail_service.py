"""
Gmail 发信检测服务（V5.2 新增）

读取用户自有 Gmail 的「已发送」邮件（只读 scope gmail.readonly），
通过 history 增量同步 + 可选 Pub/Sub 推送，解析出元数据供域名匹配。

流程：
    OAuth（offline 授权拿 refresh token）→ watch（可选 Pub/Sub）→
    history.list 增量 / messages.list 初始 → messages.get 解析 → 同步服务写入活动表
"""
import base64
import datetime
import json
import logging
from typing import List, Optional, Tuple
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.database import MailAccount
from app.services import user_config as uc
from app.services.user_config import encrypt_secret, decrypt_secret

logger = logging.getLogger("gmail_service")

# ── Google 端点 ──
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"

# 最小权限：只读（方案 6.3）
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

# 已发送标签
LABEL_SENT = "SENT"


class GmailServiceError(Exception):
    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code


# ═══════════════════════════════════════════
# 凭据与 OAuth
# ═══════════════════════════════════════════

def get_client_credentials(db: Session, user_id: Optional[int]) -> Tuple[str, str]:
    """返回 (client_id, client_secret)，用户配置优先，回退环境变量"""
    client_id = uc.get_effective_api_key(db, user_id, uc.SERVICE_GMAIL)
    client_secret = uc.get_effective_api_secret(db, user_id, uc.SERVICE_GMAIL)
    return client_id, client_secret


def get_redirect_uri(request) -> str:
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", request.url.netloc)
    return f"{scheme}://{host}/api/mail-accounts/oauth/callback/gmail"


def build_authorization_url(client_id: str, redirect_uri: str, state: str) -> str:
    """构造 Google OAuth 授权 URL（offline 授权拿 refresh token）"""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GMAIL_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def exchange_code_for_token(code: str, client_id: str, client_secret: str, redirect_uri: str) -> dict:
    """授权码兑换 token（含 refresh_token）"""
    try:
        resp = httpx.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=30,
        )
    except httpx.HTTPError as e:
        raise GmailServiceError(f"请求 Google 失败: {str(e)[:200]}")
    if resp.status_code != 200:
        logger.warning("Gmail token 兑换失败 HTTP %s: %s", resp.status_code, resp.text[:300])
        raise GmailServiceError(
            f"Gmail 授权失败（HTTP {resp.status_code}），请检查 Client ID / Secret",
            status_code=400 if resp.status_code in (400, 401) else 502,
        )
    data = resp.json()
    if not data.get("access_token"):
        raise GmailServiceError("Google 未返回 access_token")
    return data


def refresh_access_token(refresh_token: str, client_id: str, client_secret: str) -> Tuple[str, int]:
    """用 refresh token 刷新 access token，返回 (access_token, expires_in)"""
    try:
        resp = httpx.post(
            GOOGLE_TOKEN_URL,
            data={
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
    except httpx.HTTPError as e:
        raise GmailServiceError(f"刷新 token 失败: {str(e)[:200]}")
    if resp.status_code != 200:
        raise GmailServiceError(
            "Google 拒绝刷新 token，请重新授权",
            status_code=401,
        )
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise GmailServiceError("刷新响应缺少 access_token", status_code=401)
    return token, int(data.get("expires_in", 3600))


def get_account_access_token(db: Session, account: MailAccount) -> Optional[str]:
    """获取账户有效 access token；过期则用 refresh token 刷新并持久化"""
    client_id, client_secret = get_client_credentials(db, account.user_id)
    now = datetime.datetime.utcnow()

    if account.access_token_encrypted and account.token_expires_at and account.token_expires_at > now:
        token = decrypt_secret(account.access_token_encrypted)
        if token:
            return token

    refresh_token = decrypt_secret(account.refresh_token_encrypted)
    if not refresh_token or not client_id or not client_secret:
        account.status = "reauth_required"
        account.last_error = "缺少 refresh token 或凭据，请重新授权"
        db.commit()
        return None

    try:
        token, expires_in = refresh_access_token(refresh_token, client_id, client_secret)
    except GmailServiceError as e:
        account.status = "reauth_required"
        account.last_error = str(e)
        db.commit()
        return None

    account.access_token_encrypted = encrypt_secret(token)
    account.token_expires_at = now + datetime.timedelta(seconds=expires_in)
    account.status = "active"
    account.last_error = None
    db.commit()
    return token


# ═══════════════════════════════════════════
# Gmail API 调用
# ═══════════════════════════════════════════

def _gmail_get(access_token: str, path: str, params: dict = None) -> dict:
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        resp = httpx.get(
            f"{GMAIL_API_BASE}{path}",
            params=params or {},
            headers=headers,
            timeout=30,
        )
    except httpx.HTTPError as e:
        raise GmailServiceError(f"Gmail API 请求失败: {str(e)[:200]}")
    if resp.status_code == 401:
        raise GmailServiceError("Gmail 授权失效，请重新授权", status_code=401)
    if resp.status_code == 403:
        raise GmailServiceError("Gmail 权限不足（需 gmail.readonly scope）", status_code=403)
    if resp.status_code != 200:
        logger.warning("Gmail API HTTP %s: %s", resp.status_code, resp.text[:300])
        raise GmailServiceError(f"Gmail API 失败（HTTP {resp.status_code}）", status_code=502)
    try:
        return resp.json()
    except Exception:
        raise GmailServiceError("Gmail API 返回了无法解析的响应")


def gmail_watch(access_token: str, topic_name: str) -> Tuple[str, str]:
    """开启 watch（返回 historyId, expiration ISO）。未配置 topic 时跳过。"""
    if not topic_name:
        return "", ""
    data = {"labelIds": [LABEL_SENT], "topicName": topic_name}
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    try:
        resp = httpx.post(
            f"{GMAIL_API_BASE}/users/me/watch",
            json=data,
            headers=headers,
            timeout=30,
        )
    except httpx.HTTPError as e:
        raise GmailServiceError(f"开启 Gmail watch 失败: {str(e)[:200]}")
    if resp.status_code == 401:
        raise GmailServiceError("Gmail 授权失效，请重新授权", status_code=401)
    if resp.status_code != 200:
        logger.warning("Gmail watch HTTP %s: %s", resp.status_code, resp.text[:300])
        raise GmailServiceError(f"Gmail watch 失败（HTTP {resp.status_code}）", status_code=502)
    data = resp.json()
    return str(data.get("historyId", "")), str(data.get("expiration", ""))


def gmail_stop_watch(access_token: str):
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        httpx.post(f"{GMAIL_API_BASE}/users/me/stop", headers=headers, timeout=30)
    except httpx.HTTPError:
        pass


def list_sent_messages(
    access_token: str,
    start_history_id: Optional[str] = None,
    page_token: Optional[str] = None,
    max_results: int = 100,
    after_date: Optional[datetime.date] = None,
) -> dict:
    """列出已发送消息。

    - start_history_id → history.list 增量（watch/Pub/Sub 模式，响应含 historyId）
    - 否则 messages.list（q=in:sent），可传 after_date 做轮询增量
      （注意：messages.list 响应不含 historyId，轮询模式必须用 after: 查询增量）
    """
    if start_history_id:
        params = {"startHistoryId": start_history_id, "labelId": LABEL_SENT}
        if page_token:
            params["pageToken"] = page_token
        return _gmail_get(access_token, "/users/me/history", params)
    q = "in:sent"
    if after_date is not None:
        q += f" after:{after_date.strftime('%Y/%m/%d')}"
    params = {"q": q, "maxResults": max_results}
    if page_token:
        params["pageToken"] = page_token
    return _gmail_get(access_token, "/users/me/messages", params)


def get_message_metadata(access_token: str, message_id: str) -> dict:
    """获取单封邮件元数据（不读正文，仅 Header + snippet）

    注意：metadataHeaders 为重复参数（官方要求逐个 header 传），
    httpx 对 list 值会渲染为多个同名 query 参数。
    """
    params = {
        "format": "metadata",
        "metadataHeaders": [
            "Subject", "To", "Cc", "From", "Date", "Message-ID", "References",
        ],
    }
    return _gmail_get(access_token, f"/users/me/messages/{message_id}", params)


def get_profile(access_token: str) -> dict:
    """获取当前授权用户邮箱地址"""
    return _gmail_get(access_token, "/users/me/profile")


# ═══════════════════════════════════════════
# 消息解析
# ═══════════════════════════════════════════

def _parse_header(raw: str, name: str) -> str:
    for line in raw or []:
        if line.get("name", "").lower() == name.lower():
            return line.get("value", "")
    return ""


def _extract_addresses(value: str) -> List[str]:
    """从 'Name <a@b.com>' 格式中提取邮箱地址"""
    if not value:
        return []
    import re as _re
    return [m.lower() for m in _re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", value)]


def parse_message_payload(raw: dict) -> dict:
    """解析 messages.get(metadata) 响应为标准化字典"""
    payload = raw.get("payload", {})
    headers = payload.get("headers", []) or []
    return {
        "id": raw.get("id", ""),
        "thread_id": raw.get("threadId", ""),
        "snippet": (raw.get("snippet") or "")[:300],
        "internal_date": raw.get("internalDate"),
        "from_header": _parse_header(headers, "From"),
        "subject": _parse_header(headers, "Subject"),
        "date_header": _parse_header(headers, "Date"),
        "message_id": _parse_header(headers, "Message-ID"),
        "to_raw": _parse_header(headers, "To"),
        "cc_raw": _parse_header(headers, "Cc"),
    }


def parse_sent_datetime(date_header: str, internal_date: Optional[str]) -> Optional[datetime.datetime]:
    """解析发送时间（优先 Date header，回退 internalDate 毫秒时间戳）"""
    if date_header:
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(date_header)
            if dt:
                return dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        except Exception:
            pass
    if internal_date:
        try:
            return datetime.datetime.utcfromtimestamp(int(internal_date) / 1000)
        except (ValueError, TypeError):
            pass
    return None


def extract_history_message_ids(history_data: dict) -> List[str]:
    """从 history.list 响应提取 messagesAdded 的消息 ID（仅 SENT 标签）"""
    ids = []
    for history in history_data.get("history", []) or []:
        for added in history.get("messagesAdded", []) or []:
            message = added.get("message") or {}
            label_ids = message.get("labelIds", []) or []
            if LABEL_SENT in label_ids or not label_ids:
                mid = message.get("id")
                if mid and mid not in ids:
                    ids.append(mid)
    return ids

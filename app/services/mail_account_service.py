"""
自有邮箱账户服务（V5.2 新增）

OAuth 绑定 / 列表 / 断开 / 状态。token 加密存储，绝不返回明文。
"""
import datetime
import json
from typing import List, Optional

from sqlalchemy.orm import Session

from app.database import MailAccount
from app.services import gmail_service as gs
from app.services.user_config import encrypt_secret, decrypt_secret


class MailAccountError(Exception):
    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code


def create_gmail_account(
    db: Session,
    user_id: int,
    code: str,
    redirect_uri: str,
) -> MailAccount:
    """OAuth 回调：兑换 token → 获取邮箱 → 创建/更新账户绑定"""
    client_id, client_secret = gs.get_client_credentials(db, user_id)
    if not client_id or not client_secret:
        raise MailAccountError("Gmail Client ID / Secret 未配置，请先在设置页保存", status_code=400)

    data = gs.exchange_code_for_token(code, client_id, client_secret, redirect_uri)
    access_token = data.get("access_token", "")
    refresh_token = data.get("refresh_token", "")
    if not access_token:
        raise MailAccountError("Google 未返回 access_token", status_code=400)
    if not refresh_token:
        # offline 授权必须返回 refresh_token
        raise MailAccountError(
            "未获取到 refresh_token：请在 Google Cloud 控制台确认 OAuth consent 状态为「Testing 或 Production」且勾选了 offline access",
            status_code=400,
        )

    profile = gs.get_profile(access_token)
    email_address = (profile.get("emailAddress") or "").lower()
    provider_user_id = (profile.get("id") or "")[:255]
    if not email_address:
        raise MailAccountError("无法获取 Gmail 邮箱地址", status_code=400)

    now = datetime.datetime.utcnow()
    account = db.query(MailAccount).filter(
        MailAccount.user_id == user_id,
        MailAccount.email_address == email_address,
    ).first()
    if account is None:
        account = MailAccount(user_id=user_id, email_address=email_address, created_at=now)
        db.add(account)

    account.provider = "gmail"
    account.provider_user_id = provider_user_id or None
    account.access_token_encrypted = encrypt_secret(access_token)
    account.refresh_token_encrypted = encrypt_secret(refresh_token)
    account.token_expires_at = now + datetime.timedelta(seconds=int(data.get("expires_in", 3600)))
    account.scopes = json.dumps(data.get("scope", gs.GMAIL_SCOPE).split())
    account.status = "active"
    account.last_error = None
    account.updated_at = now

    # 尝试开启 watch（未配置 Pub/Sub topic 时静默跳过）
    topic = _get_pubsub_topic()
    if topic:
        try:
            history_id, expiration = gs.gmail_watch(access_token, topic)
            if history_id:
                account.sync_cursor = history_id
            if expiration:
                try:
                    account.watch_expiration_at = datetime.datetime.utcfromtimestamp(int(expiration) / 1000)
                except (ValueError, TypeError):
                    pass
        except gs.GmailServiceError:
            pass

    db.commit()
    return account


def _get_pubsub_topic() -> str:
    import os
    return os.environ.get("GMAIL_PUBSUB_TOPIC", "").strip()


def list_accounts(db: Session, user_id: int) -> List[MailAccount]:
    return db.query(MailAccount).filter(MailAccount.user_id == user_id).all()


def get_account(db: Session, account_id: int, user_id: int) -> Optional[MailAccount]:
    return db.query(MailAccount).filter(
        MailAccount.id == account_id, MailAccount.user_id == user_id
    ).first()


def disconnect_account(db: Session, account: MailAccount) -> bool:
    """断开：停止 watch（若配置了 topic）并删除本地账户记录"""
    token = gs.get_account_access_token(db, account)
    if token:
        try:
            gs.gmail_stop_watch(token)
        except gs.GmailServiceError:
            pass
    db.delete(account)
    db.commit()
    return True


def account_to_dict(account: MailAccount) -> dict:
    return {
        "id": account.id,
        "provider": account.provider or "gmail",
        "email_address": account.email_address,
        "status": account.status,
        "last_error": account.last_error,
        "watch_enabled": bool(account.watch_expiration_at),
        "watch_expiration_at": account.watch_expiration_at.isoformat() if account.watch_expiration_at else None,
        "last_synced_at": account.last_synced_at.isoformat() if account.last_synced_at else None,
        "created_at": account.created_at.isoformat() if account.created_at else None,
    }


def oauth_status(db: Session, user_id: int) -> dict:
    """设置页展示：凭据是否配置 + 已绑定账户数"""
    client_id, _ = gs.get_client_credentials(db, user_id)
    accounts = list_accounts(db, user_id)
    return {
        "client_configured": bool(client_id),
        "account_count": len(accounts),
        "accounts": [account_to_dict(a) for a in accounts],
    }

"""
自有邮箱账户 API（V5.2 新增）

Gmail OAuth 绑定 / 列表 / 手动同步 / 续期 / 断开 / 状态。
"""
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import require_user
from app.services import mail_account_service as mas
from app.services import mail_sync_service
from app.services import gmail_service as gs

router = APIRouter(tags=["mail_accounts"])

_SESSION_STATE_KEY = "gmail_oauth_state"


@router.get("/mail-accounts")
def list_mail_accounts(
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """获取当前用户已绑定的自有邮箱账户（不返回 token）"""
    accounts = mas.list_accounts(db, user.id)
    return {
        "accounts": [mas.account_to_dict(a) for a in accounts],
        "status": mas.oauth_status(db, user.id),
    }


@router.get("/mail-accounts/{provider}/oauth/start")
def gmail_oauth_start(
    request: Request,
    provider: str,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """生成 Gmail OAuth URL（offline 授权）"""
    if provider != "gmail":
        raise HTTPException(status_code=400, detail="当前仅支持 gmail")

    client_id, _ = gs.get_client_credentials(db, user.id)
    if not client_id:
        raise HTTPException(status_code=400, detail="尚未配置 Gmail Client ID，请先在设置页保存")

    state = secrets.token_urlsafe(32)
    request.session[_SESSION_STATE_KEY] = state

    redirect_uri = gs.get_redirect_uri(request)
    url = gs.build_authorization_url(client_id, redirect_uri, state)
    return RedirectResponse(url=url, status_code=302)


@router.get("/mail-accounts/oauth/callback/gmail")
def gmail_oauth_callback(
    request: Request,
    code: str = None,
    state: str = None,
    error: str = None,
    error_description: str = None,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """Gmail OAuth 回调：校验 state → 绑定账户 → 回设置页"""
    if error:
        raise HTTPException(status_code=400, detail=f"Gmail 授权被拒绝: {error_description or error}")

    expected_state = request.session.pop(_SESSION_STATE_KEY, None)
    if not state or not expected_state or state != expected_state:
        raise HTTPException(status_code=400, detail="state 校验失败，请重新发起授权（防 CSRF）")
    if not code:
        raise HTTPException(status_code=400, detail="缺少授权码 code")

    redirect_uri = gs.get_redirect_uri(request)
    try:
        account = mas.create_gmail_account(db, user.id, code, redirect_uri)
    except mas.MailAccountError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except gs.GmailServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))

    return RedirectResponse(url=f"/settings?gmail_bound={account.email_address}", status_code=302)


@router.post("/mail-accounts/{account_id}/sync")
def sync_mail_account(
    account_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """手动触发该邮箱的增量同步"""
    account = mas.get_account(db, account_id, user.id)
    if not account:
        raise HTTPException(status_code=404, detail="邮箱账户不存在")
    try:
        stats = mail_sync_service.sync_account(db, account)
    except gs.GmailServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    return {"message": "同步完成", "stats": stats, "account": mas.account_to_dict(account)}


@router.post("/mail-accounts/{account_id}/renew")
def renew_mail_account(
    account_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """手动续期 Gmail watch"""
    account = mas.get_account(db, account_id, user.id)
    if not account:
        raise HTTPException(status_code=404, detail="邮箱账户不存在")
    import os
    topic = os.environ.get("GMAIL_PUBSUB_TOPIC", "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="未配置 GMAIL_PUBSUB_TOPIC，watch 推送不可用（可改用轮询同步）")
    token = gs.get_account_access_token(db, account)
    if not token:
        raise HTTPException(status_code=401, detail="Gmail 授权失效，请重新授权")
    try:
        history_id, expiration = gs.gmail_watch(token, topic)
    except gs.GmailServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    if history_id:
        account.sync_cursor = history_id
    db.commit()
    return {"message": "watch 已续期", "account": mas.account_to_dict(account)}


@router.post("/mail-accounts/{account_id}/disconnect")
def disconnect_mail_account(
    account_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """断开邮箱账户（停止订阅并删除本地凭据）"""
    account = mas.get_account(db, account_id, user.id)
    if not account:
        raise HTTPException(status_code=404, detail="邮箱账户不存在")
    mas.disconnect_account(db, account)
    return {"message": "已断开邮箱账户"}


@router.get("/mail-accounts/{account_id}/status")
def mail_account_status(
    account_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """查看同步状态、最近错误和最后同步时间"""
    account = mas.get_account(db, account_id, user.id)
    if not account:
        raise HTTPException(status_code=404, detail="邮箱账户不存在")
    return mas.account_to_dict(account)

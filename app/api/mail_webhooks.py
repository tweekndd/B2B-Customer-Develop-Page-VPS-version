"""
Gmail Pub/Sub Webhook（V5.2 新增）

接收 Gmail 推送通知（无需浏览器 Session，绕过登录中间件）。
快速确认后触发异步增量同步；依赖唯一键与幂等处理防重复投递。

验证：Google Pub/Sub push 默认会附带发布者配置的 Bearer token
（GMAIL_PUBSUB_TOKEN，可选）；未配置时不强制校验（部署在内网时）。
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db, MailAccount
from app.services import mail_sync_service

router = APIRouter(tags=["webhooks"])
logger = logging.getLogger("mail_webhook")

_GMAIL_HISTORY_ID = "gmail_history_id"


def _verify_pubsub_token(request: Request) -> bool:
    """校验 Pub/Sub 推送 Authorization Bearer（可选配置 GMAIL_PUBSUB_TOKEN）"""
    import os
    expected = os.environ.get("GMAIL_PUBSUB_TOKEN", "").strip()
    if not expected:
        return True  # 未配置时不校验
    auth = request.headers.get("authorization", "")
    return auth == f"Bearer {expected}"


@router.post("/webhooks/gmail/pubsub")
async def gmail_pubsub_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """接收 Gmail Pub/Sub 推送：解析 emailAddress + historyId → 异步同步"""
    if not _verify_pubsub_token(request):
        raise HTTPException(status_code=401, detail="invalid token")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")

    message = body.get("message", {})
    if not message:
        return {"received": True}

    try:
        import base64
        import json
        data = json.loads(base64.b64decode(message.get("data", "")).decode("utf-8"))
    except Exception:
        return {"received": True}  # 无法解析的数据忽略

    email_address = (data.get("emailAddress") or "").lower()
    if not email_address:
        return {"received": True}

    # 找到该邮箱的账户并触发异步同步
    account = db.query(MailAccount).filter(
        MailAccount.email_address == email_address,
        MailAccount.status == "active",
    ).first()
    if account is None:
        return {"received": True}

    async def _run_sync():
        from app.database import SessionLocal
        sync_db = SessionLocal()
        try:
            fresh = sync_db.query(MailAccount).filter(MailAccount.id == account.id).first()
            if fresh:
                mail_sync_service.sync_account(sync_db, fresh)
        except Exception as e:
            logger.warning("webhook 同步失败 %s: %s", email_address, e)
        finally:
            sync_db.close()

    import asyncio
    asyncio.get_event_loop().create_task(_run_sync())

    return {"received": True}

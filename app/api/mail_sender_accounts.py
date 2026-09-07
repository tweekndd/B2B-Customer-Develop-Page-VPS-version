"""
发件邮箱账户 API（Phase1 新增）

用户配置 Himalaya SMTP 账户：新增/编辑/删除/测试连接。
凭据 Fernet 加密入库；测试连接用 Python smtplib/imaplib（无需 himalaya 二进制）。
"""
import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import require_user
from app.database import get_db
from app.services import himalaya_service as hs
from app.services import mail_sender_service as mss

router = APIRouter(tags=["mail_sender_accounts"])


def _err(e: Exception):
    code = getattr(e, "status_code", 400)
    return HTTPException(status_code=code, detail=str(e))


@router.get("/mail-sender-accounts")
def list_sender_accounts(db: Session = Depends(get_db), user=Depends(require_user)):
    rows = mss.list_sender_accounts(db, user.id)
    return {"accounts": [mss.sender_account_to_dict(r) for r in rows]}


@router.post("/mail-sender-accounts")
def create_sender_account(
    body: dict,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """新增发件账户（SMTP）。

    body: {email_address, name?, display_name?, smtp_host, smtp_port=587,
           smtp_encryption=starttls, smtp_login?, smtp_password,
           imap_host?, imap_port?, imap_encryption?, imap_login?, imap_password?,
           daily_limit=30, enabled=true}
    """
    try:
        row = mss.upsert_sender_account(
            db, user.id,
            name=body.get("name"),
            email_address=body.get("email_address", ""),
            display_name=body.get("display_name"),
            smtp_host=body.get("smtp_host", ""),
            smtp_port=body.get("smtp_port", 587),
            smtp_encryption=body.get("smtp_encryption", "starttls"),
            smtp_login=body.get("smtp_login"),
            smtp_password=body.get("smtp_password", ""),
            imap_host=body.get("imap_host"),
            imap_port=body.get("imap_port"),
            imap_encryption=body.get("imap_encryption"),
            imap_login=body.get("imap_login"),
            imap_password=body.get("imap_password"),
            daily_limit=body.get("daily_limit", 30),
            enabled=body.get("enabled", True),
        )
    except mss.SenderAccountError as e:
        raise _err(e)
    return {"message": "发件账户已保存", "account": mss.sender_account_to_dict(row)}


@router.put("/mail-sender-accounts/{account_id}")
def update_sender_account(
    account_id: int,
    body: dict,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    try:
        row = mss.upsert_sender_account(
            db, user.id, account_id=account_id,
            name=body.get("name"),
            email_address=body.get("email_address", ""),
            display_name=body.get("display_name"),
            smtp_host=body.get("smtp_host", ""),
            smtp_port=body.get("smtp_port", 587),
            smtp_encryption=body.get("smtp_encryption", "starttls"),
            smtp_login=body.get("smtp_login"),
            smtp_password=body.get("smtp_password", ""),
            imap_host=body.get("imap_host"),
            imap_port=body.get("imap_port"),
            imap_encryption=body.get("imap_encryption"),
            imap_login=body.get("imap_login"),
            imap_password=body.get("imap_password"),
            daily_limit=body.get("daily_limit"),
            enabled=body.get("enabled"),
        )
    except mss.SenderAccountError as e:
        raise _err(e)
    return {"message": "发件账户已更新", "account": mss.sender_account_to_dict(row)}


@router.post("/mail-sender-accounts/test")
def test_sender_account(
    body: dict,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """测试 SMTP(+IMAP) 连接与认证。

    body 与新增相同；支持传入 account_id 用已保存配置测（password 为空时读库存），
    或完全不传 account_id 用 body 里临时凭据测（未保存也能测）。
    """
    account_id = body.get("account_id")
    if account_id:
        row = mss.get_sender_account(db, account_id, user.id)
        if row is None:
            raise HTTPException(status_code=404, detail="发件账户不存在")
        # 允许用临时新密码覆盖测试（避免先保存才能测）
        result = mss.test_sender_account(row, test_password=body.get("smtp_password") or None)
        row.last_test_at = datetime.datetime.utcnow()
        row.last_test_ok = 1 if result["ok"] else 0
        row.last_error = None if result["ok"] else (
            result.get("smtp", {}).get("message", "") + (
                ("；" + result["imap"]["message"]) if result.get("imap") and not result["imap"].get("ok") else ""
            )
        )
        db.commit()
        return {"ok": result["ok"], "smtp": result["smtp"], "imap": result["imap"],
                "account": mss.sender_account_to_dict(row)}
    # 临时凭据测试（未入库）
    if not body.get("smtp_host") or not body.get("smtp_password"):
        raise HTTPException(status_code=400, detail="临时测试需要 smtp_host 与 smtp_password")
    smtp_result = hs.test_smtp_connection(
        host=body.get("smtp_host", ""),
        port=body.get("smtp_port", 587),
        encryption=body.get("smtp_encryption", "starttls"),
        login=body.get("smtp_login") or body.get("email_address", ""),
        password=body.get("smtp_password", ""),
    )
    return {"ok": smtp_result["ok"], "smtp": smtp_result, "imap": None}


@router.delete("/mail-sender-accounts/{account_id}")
def delete_sender_account(account_id: int, db: Session = Depends(get_db),
                          user=Depends(require_user)):
    row = mss.get_sender_account(db, account_id, user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="发件账户不存在")
    mss.delete_sender_account(db, row)
    return {"message": "发件账户已删除"}

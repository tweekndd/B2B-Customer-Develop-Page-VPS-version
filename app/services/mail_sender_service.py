"""
发件邮箱账户服务（Phase1 新增）

用户级 Himalaya SMTP 发件账户管理：CRUD / 凭据加密 / 测试连接 /
生成 himalaya config（密码只落 DATA_DIR，权限 0600）。
"""
import datetime
import json
import os
from typing import List, Optional

from sqlalchemy.orm import Session

from app.database import MailSenderAccount
from app.services.user_config import encrypt_secret, decrypt_secret, mask_secret
from app.services import himalaya_service as hs

SENDER_STATUS_ACTIVE = "active"
SENDER_STATUS_ERROR = "error"
SENDER_STATUS_DISABLED = "disabled"


class SenderAccountError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def list_sender_accounts(db: Session, user_id: int) -> List[MailSenderAccount]:
    return (
        db.query(MailSenderAccount)
        .filter(MailSenderAccount.user_id == user_id)
        .order_by(MailSenderAccount.id.desc())
        .all()
    )


def get_sender_account(db: Session, account_id: int, user_id: int) -> Optional[MailSenderAccount]:
    return db.query(MailSenderAccount).filter(
        MailSenderAccount.id == account_id, MailSenderAccount.user_id == user_id
    ).first()


def _decrypt(row: MailSenderAccount):
    return {
        "smtp_password": decrypt_secret(row.smtp_password_encrypted),
        "imap_password": decrypt_secret(row.imap_password_encrypted),
    }


def sender_account_to_dict(row: MailSenderAccount, include_password: bool = False) -> dict:
    d = {
        "id": row.id,
        "name": row.name,
        "email_address": row.email_address,
        "display_name": row.display_name,
        "provider": row.provider or "himalaya",
        "smtp_host": row.smtp_host,
        "smtp_port": row.smtp_port,
        "smtp_encryption": row.smtp_encryption or "starttls",
        "smtp_login": row.smtp_login or row.email_address,
        "smtp_password_masked": mask_secret(decrypt_secret(row.smtp_password_encrypted)),
        "smtp_password_set": bool(row.smtp_password_encrypted),
        "imap_host": row.imap_host,
        "imap_port": row.imap_port,
        "imap_encryption": row.imap_encryption,
        "imap_login": row.imap_login,
        "imap_password_masked": mask_secret(decrypt_secret(row.imap_password_encrypted)),
        "imap_password_set": bool(row.imap_password_encrypted),
        "daily_limit": row.daily_limit,
        "enabled": bool(row.enabled),
        "status": row.status or SENDER_STATUS_ACTIVE,
        "last_test_at": row.last_test_at.isoformat() if row.last_test_at else None,
        "last_test_ok": bool(row.last_test_ok) if row.last_test_ok is not None else None,
        "last_error": row.last_error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "himalaya_available": hs.is_available(),
    }
    return d


def upsert_sender_account(
    db: Session,
    user_id: int,
    *,
    account_id: Optional[int] = None,
    name: Optional[str] = None,
    email_address: str,
    display_name: Optional[str] = None,
    smtp_host: str,
    smtp_port: int = 587,
    smtp_encryption: str = "starttls",
    smtp_login: Optional[str] = None,
    smtp_password: str = "",
    imap_host: Optional[str] = None,
    imap_port: Optional[int] = 993,
    imap_encryption: Optional[str] = "tls",
    imap_login: Optional[str] = None,
    imap_password: Optional[str] = None,
    daily_limit: int = 30,
    enabled: bool = True,
) -> MailSenderAccount:
    email_address = (email_address or "").strip().lower()
    if not email_address or "@" not in email_address:
        raise SenderAccountError("邮箱地址无效")
    if not smtp_host or not smtp_host.strip():
        raise SenderAccountError("SMTP 主机不能为空")
    if smtp_encryption not in ("tls", "starttls", "none"):
        raise SenderAccountError("smtp_encryption 必须是 tls/starttls/none")
    try:
        smtp_port = int(smtp_port)
        imap_port = int(imap_port) if imap_port else None
    except (TypeError, ValueError):
        raise SenderAccountError("端口必须是数字")

    if account_id:
        row = get_sender_account(db, account_id, user_id)
        if row is None:
            raise SenderAccountError("发件账户不存在", status_code=404)
        # 邮箱变更需查重
        if row.email_address != email_address:
            dup = db.query(MailSenderAccount).filter(
                MailSenderAccount.user_id == user_id,
                MailSenderAccount.email_address == email_address,
                MailSenderAccount.id != account_id,
            ).first()
            if dup:
                raise SenderAccountError("该邮箱已配置为发件账户")
        row.email_address = email_address
    else:
        dup = db.query(MailSenderAccount).filter(
            MailSenderAccount.user_id == user_id,
            MailSenderAccount.email_address == email_address,
        ).first()
        if dup:
            raise SenderAccountError("该邮箱已配置为发件账户，请直接编辑")
        row = MailSenderAccount(user_id=user_id, email_address=email_address, provider="himalaya")
        db.add(row)

    if name is not None:
        row.name = (name or "").strip() or None
    if display_name is not None:
        row.display_name = (display_name or "").strip() or None
    row.smtp_host = smtp_host.strip()
    row.smtp_port = smtp_port
    row.smtp_encryption = smtp_encryption
    row.smtp_login = (smtp_login or "").strip() or email_address
    if smtp_password:  # 空=保持原密码
        row.smtp_password_encrypted = encrypt_secret(smtp_password)
    if imap_host is not None:
        row.imap_host = (imap_host or "").strip() or None
    if imap_port is not None:
        row.imap_port = imap_port
    if imap_encryption is not None:
        row.imap_encryption = imap_encryption or None
    if imap_login is not None:
        row.imap_login = (imap_login or "").strip() or None
    if imap_password:  # 空=保持原密码
        row.imap_password_encrypted = encrypt_secret(imap_password)
    if daily_limit is not None:
        row.daily_limit = max(0, int(daily_limit))
    if enabled is not None:
        row.enabled = 1 if enabled else 0
    row.status = SENDER_STATUS_ACTIVE
    row.last_error = None
    row.updated_at = datetime.datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


def delete_sender_account(db: Session, account: MailSenderAccount) -> bool:
    """删除前先移除 himalaya 配置文件（凭据清理）"""
    try:
        cfg = hs.account_config_path(account.id)
        if os.path.exists(cfg):
            os.remove(cfg)
    except OSError:
        pass
    db.delete(account)
    db.commit()
    return True


def get_plain_password(row: MailSenderAccount) -> str:
    return decrypt_secret(row.smtp_password_encrypted)


def ensure_himalaya_config(row: MailSenderAccount) -> Optional[str]:
    """把账户写成 himalaya config.toml；返回路径。缺密码时返回 None。"""
    password = get_plain_password(row)
    if not password:
        return None
    cfg_text = hs.build_account_toml(
        email_address=row.email_address,
        display_name=row.display_name or "",
        smtp_host=row.smtp_host,
        smtp_port=row.smtp_port,
        smtp_encryption=row.smtp_encryption or "starttls",
        smtp_login=row.smtp_login or row.email_address,
        smtp_password=password,
        imap_host=row.imap_host,
        imap_port=row.imap_port,
        imap_encryption=row.imap_encryption,
        imap_login=row.imap_login,
        imap_password=decrypt_secret(row.imap_password_encrypted) or None,
    )
    return hs.write_config_file(cfg_text, hs.account_config_path(row.id))


def test_sender_account(row: MailSenderAccount, test_password: Optional[str] = None) -> dict:
    """测试 SMTP(+IMAP) 连接与认证。test_password 传入时用未保存的新密码试连。

    Returns: {"ok": bool, "smtp": {...}, "imap": {...}}
    """
    password = test_password if test_password is not None else get_plain_password(row)
    smtp_result = hs.test_smtp_connection(
        host=row.smtp_host,
        port=row.smtp_port,
        encryption=row.smtp_encryption or "starttls",
        login=row.smtp_login or row.email_address,
        password=password,
    )
    imap_result = None
    if row.imap_host:
        imap_result = hs.test_imap_connection(
            host=row.imap_host,
            port=row.imap_port or 993,
            encryption=row.imap_encryption or "tls",
            login=row.imap_login or row.email_address,
            password=decrypt_secret(row.imap_password_encrypted) or password,
        )
    ok = bool(smtp_result.get("ok")) and (imap_result is None or bool(imap_result.get("ok")))
    return {"ok": ok, "smtp": smtp_result, "imap": imap_result}


def set_status(db: Session, row: MailSenderAccount, status: str, error: Optional[str] = None):
    row.status = status
    if error is not None:
        row.last_error = error[:1000]
    row.updated_at = datetime.datetime.utcnow()
    db.commit()

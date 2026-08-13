"""
客户邮箱统一服务（V5.1 新增）

CustomerEmail 表为邮箱唯一事实源，Customer.emails JSON 降级为兼容视图（双写）。
所有邮箱写入必须经过本模块，禁止直接修改 Customer.emails 字段。
"""
import datetime
import json
import re
from typing import List, Optional

from sqlalchemy.orm import Session

from app.database import Customer, CustomerEmail

# RFC 基础邮箱校验（宽松但排除明显非法值；域名要求 ≥2 字符 TLD）
_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}$"
)

# 通用/无效前缀黑名单（与 waterfall_discovery 的过滤规则保持一致）
_EMAIL_BLACKLIST_PREFIXES = {
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply",
    "support", "postmaster", "webmaster", "abuse", "spam", "unsubscribe",
    "mailer-daemon", "mdaemon", "root",
}

VALID_SOURCES = {"website", "hunter", "tomba", "prospeo", "manual", "legacy", "migrated"}


def normalize_email(raw: str) -> Optional[str]:
    """规范化邮箱：去空格转小写 + 基础格式校验。非法返回 None。"""
    if not raw or not isinstance(raw, str):
        return None
    email = raw.strip().lower()
    if len(email) > 254 or not _EMAIL_RE.match(email):
        return None
    local_part, domain = email.split("@", 1)
    if len(local_part) > 64:
        return None
    return email


def is_blacklisted_email(email: str) -> bool:
    """判断邮箱前缀是否在通用黑名单中（不适用于用户手动输入，仅发现流程）"""
    local_part = (email or "").split("@", 1)[0].strip().lower()
    return local_part in _EMAIL_BLACKLIST_PREFIXES


def _sync_emails_json(db: Session, customer: Customer):
    """从 CustomerEmail 表重建 Customer.emails JSON 视图（双写一致性核心）"""
    emails = (
        db.query(CustomerEmail.email)
        .filter(CustomerEmail.customer_id == customer.id)
        .order_by(CustomerEmail.is_primary.desc(), CustomerEmail.created_at.asc())
        .all()
    )
    customer.emails = json.dumps([e[0] for e in emails], ensure_ascii=False)


def get_customer_emails(db: Session, customer_id: int) -> List[CustomerEmail]:
    """获取客户的结构化邮箱列表（主邮箱优先）"""
    return (
        db.query(CustomerEmail)
        .filter(CustomerEmail.customer_id == customer_id)
        .order_by(CustomerEmail.is_primary.desc(), CustomerEmail.created_at.asc())
        .all()
    )


def upsert_customer_email(
    db: Session,
    customer_id: int,
    email: str,
    source: str = "manual",
    source_detail: Optional[str] = None,
    notes: Optional[str] = None,
    created_by_user_id: Optional[int] = None,
    is_primary: bool = False,
    contact: Optional[dict] = None,
) -> CustomerEmail:
    """新增或更新客户邮箱（按 customer_id + email 去重，幂等）。

    contact 可选字段：first_name/last_name/position/department/phone/
    linkedin/score/verification，来自 Hunter/Tomba/Prospeo 等发现服务。
    """
    email_norm = normalize_email(email)
    if not email_norm:
        raise ValueError("邮箱格式无效")

    source = (source or "manual").lower()
    if source not in VALID_SOURCES:
        source = "manual"

    record = (
        db.query(CustomerEmail)
        .filter(CustomerEmail.customer_id == customer_id, CustomerEmail.email == email_norm)
        .first()
    )
    now = datetime.datetime.utcnow()
    if record:
        # 更新已有记录（保留主邮箱标记；补全缺失的联系人信息）
        record.updated_at = now
        if notes:
            record.notes = notes
        if source_detail:
            record.source_detail = source_detail
        if contact:
            for key in ("first_name", "last_name", "position", "department", "phone", "linkedin"):
                if contact.get(key) and not getattr(record, key):
                    setattr(record, key, str(contact[key]))
            if contact.get("score") is not None:
                record.score = int(contact["score"])
            if contact.get("verification"):
                record.verification = str(contact["verification"])
        email_record = record
    else:
        record = CustomerEmail(
            customer_id=customer_id,
            email=email_norm,
            local_part=email_norm.split("@", 1)[0],
            domain=email_norm.split("@", 1)[1],
            source=source,
            source_detail=source_detail,
            notes=notes,
            created_by_user_id=created_by_user_id,
            is_primary=1 if is_primary else 0,
            created_at=now,
            updated_at=now,
        )
        if contact:
            record.first_name = (contact.get("first_name") or "")[:100]
            record.last_name = (contact.get("last_name") or "")[:100]
            record.position = (contact.get("position") or "")[:200]
            record.department = (contact.get("department") or "")[:100]
            record.phone = (contact.get("phone") or "")[:50]
            record.linkedin = (contact.get("linkedin") or "")[:500]
            record.score = int(contact.get("score") or 0)
            record.verification = (contact.get("verification") or "")[:30]
        db.add(record)
        email_record = record

    if is_primary:
        # 唯一主邮箱：始终清除同客户其他主标记（新建与更新都适用）
        db.query(CustomerEmail).filter(
            CustomerEmail.customer_id == customer_id,
            CustomerEmail.id != email_record.id,
        ).update({CustomerEmail.is_primary: 0})
        email_record.is_primary = 1
        email_record.updated_at = now

    db.flush()
    _sync_emails_json(db, db.query(Customer).filter(Customer.id == customer_id).first())
    return email_record


def bulk_upsert_customer_emails(
    db: Session,
    customer_id: int,
    emails: List[str],
    source: str = "website",
    source_detail: Optional[str] = None,
) -> int:
    """批量新增邮箱（官网提取等场景），返回成功写入的数量（含更新）"""
    processed = 0
    for raw in emails:
        try:
            upsert_customer_email(
                db, customer_id, raw, source=source, source_detail=source_detail
            )
            processed += 1
        except ValueError:
            continue
    return processed


def update_customer_email(
    db: Session,
    email_id: int,
    email: Optional[str] = None,
    notes: Optional[str] = None,
    is_primary: Optional[bool] = None,
    verification: Optional[str] = None,
) -> CustomerEmail:
    """编辑客户邮箱记录"""
    record = db.query(CustomerEmail).filter(CustomerEmail.id == email_id).first()
    if not record:
        raise LookupError("邮箱记录不存在")

    if email is not None:
        email_norm = normalize_email(email)
        if not email_norm:
            raise ValueError("邮箱格式无效")
        duplicate = (
            db.query(CustomerEmail)
            .filter(
                CustomerEmail.customer_id == record.customer_id,
                CustomerEmail.email == email_norm,
                CustomerEmail.id != record.id,
            )
            .first()
        )
        if duplicate:
            raise ValueError("该邮箱已存在")
        record.email = email_norm
        record.local_part = email_norm.split("@", 1)[0]
        record.domain = email_norm.split("@", 1)[1]

    if notes is not None:
        record.notes = notes or None
    if verification is not None:
        record.verification = verification or None
    if is_primary is not None and bool(is_primary) and not record.is_primary:
        db.query(CustomerEmail).filter(
            CustomerEmail.customer_id == record.customer_id,
            CustomerEmail.id != record.id,
        ).update({CustomerEmail.is_primary: 0})
        record.is_primary = 1
    elif is_primary is not None and not bool(is_primary) and record.is_primary:
        record.is_primary = 0

    record.updated_at = datetime.datetime.utcnow()
    db.flush()
    _sync_emails_json(db, db.query(Customer).filter(Customer.id == record.customer_id).first())
    return record


def delete_customer_email(db: Session, email_id: int) -> bool:
    """删除客户邮箱记录，返回是否删除成功"""
    record = db.query(CustomerEmail).filter(CustomerEmail.id == email_id).first()
    if not record:
        return False
    customer_id = record.customer_id
    db.delete(record)
    db.flush()
    _sync_emails_json(db, db.query(Customer).filter(Customer.id == customer_id).first())
    return True


def merge_legacy_emails(db: Session, customer_id: int) -> dict:
    """把 Customer.emails JSON 中的旧邮箱并入 CustomerEmail 表（幂等）"""
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise LookupError("客户不存在")

    legacy = []
    if customer.emails:
        try:
            parsed = json.loads(customer.emails)
            if isinstance(parsed, list):
                legacy = parsed
        except (json.JSONDecodeError, TypeError):
            legacy = [e.strip() for e in customer.emails.split(",") if e.strip()]

    merged = 0
    for raw in legacy:
        try:
            record = upsert_customer_email(db, customer_id, raw, source="legacy")
            if record.source == "legacy":
                merged += 1
        except ValueError:
            continue
    return {"total": len(legacy), "merged": merged}

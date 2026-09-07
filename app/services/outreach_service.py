"""
外联发送策略与工作流服务（Phase1 新增）

方案 Phase1 #5/#6/#7/#8/#9 的服务层：
- 草稿审批状态机（outreach_drafts）
- 发送前检查：全局退订名单（#9）、每日发送上限（#8）、幂等键（#6）
- 发送日志与 provider message id（#7）落库
- 回写客户跟进状态/发信记录（复用 CustomerEmailActivity + customers.status）
"""
import datetime
import hashlib
import json
import logging
import re
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.database import (
    AutomationTask,
    Customer,
    MailSenderAccount,
    OutreachDraft,
    OutreachSendLog,
    UnsubscribeBlacklist,
)
from app.models.outreach_send import (
    DRAFT_STATUS_DRAFT, DRAFT_STATUS_PENDING, DRAFT_STATUS_APPROVED,
    DRAFT_STATUS_SENDING, DRAFT_STATUS_SENT, DRAFT_STATUS_REJECTED,
    DRAFT_STATUS_FAILED, DRAFT_STATUS_CANCELLED,
    SEND_LOG_QUEUED, SEND_LOG_SENDING, SEND_LOG_SENT, SEND_LOG_FAILED, SEND_LOG_BOUNCED,
)
from app.services import mail_sender_service as mss

logger = logging.getLogger("outreach_policy")

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


class OutreachError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def extract_domain(email: str) -> str:
    return (email or "").strip().lower().split("@")[-1] if email and "@" in email else ""


def normalize_recipient(email: str) -> str:
    return (email or "").strip().lower()


# ── 草稿查询/序列化 ───────────────────────────────────────────────

def draft_to_dict(d: OutreachDraft) -> Dict:
    return {
        "id": d.id,
        "customer_id": d.customer_id,
        "sender_account_id": d.sender_account_id,
        "recipient_email": d.recipient_email,
        "recipient_name": d.recipient_name,
        "subject": d.subject,
        "body": d.body_text,
        "language": d.language,
        "tone": d.tone,
        "generation_run_id": d.generation_run_id,
        "prompt_template_id": d.prompt_template_id,
        "prompt_version_id": d.prompt_version_id,
        "model": d.model,
        "facts_used": _load_json(d.facts_used_json, []),
        "risk_flags": _load_json(d.risk_flags_json, []),
        "status": d.status,
        "human_edited": bool(d.human_edited),
        "review_note": d.review_note,
        "reviewed_by_user_id": d.reviewed_by_user_id,
        "reviewed_at": d.reviewed_at.isoformat() if d.reviewed_at else None,
        "approved_at": d.approved_at.isoformat() if d.approved_at else None,
        "created_by_user_id": d.created_by_user_id,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }


def _load_json(raw, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def get_draft(db: Session, draft_id: int) -> OutreachDraft:
    d = db.query(OutreachDraft).filter(OutreachDraft.id == draft_id).first()
    if d is None:
        raise OutreachError("草稿不存在", status_code=404)
    return d


def list_drafts(
    db: Session,
    *,
    status: Optional[str] = None,
    customer_id: Optional[int] = None,
    page: int = 1,
    page_size: int = 25,
) -> Tuple[List[Dict], int]:
    q = db.query(OutreachDraft)
    if status:
        q = q.filter(OutreachDraft.status == status)
    if customer_id:
        q = q.filter(OutreachDraft.customer_id == customer_id)
    total = q.count()
    rows = (
        q.order_by(OutreachDraft.created_at.desc())
        .offset((max(1, page) - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return [draft_to_dict(r) for r in rows], total


# ── 草稿编辑/审批状态机 ───────────────────────────────────────────

def edit_draft(
    db: Session,
    draft_id: int,
    *,
    subject: Optional[str] = None,
    body: Optional[str] = None,
    recipient_email: Optional[str] = None,
    recipient_name: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Dict:
    """编辑草稿（仅 draft/rejected 状态可改），记录 human_edited。"""
    d = get_draft(db, draft_id)
    if d.status not in (DRAFT_STATUS_DRAFT, DRAFT_STATUS_REJECTED):
        raise OutreachError("只有 draft/rejected 状态的草稿可以编辑")
    changed = False
    if subject is not None and str(subject).strip() != d.subject:
        if not str(subject).strip():
            raise OutreachError("主题不能为空")
        d.subject = str(subject).strip()
        changed = True
    if body is not None and str(body).strip() != d.body_text:
        if not str(body).strip():
            raise OutreachError("正文不能为空")
        d.body_text = str(body).strip()
        changed = True
    if recipient_email is not None:
        d.recipient_email = normalize_recipient(recipient_email) or None
        changed = True
    if recipient_name is not None:
        d.recipient_name = recipient_name or None
        changed = True
    if changed:
        d.human_edited = 1
        d.updated_at = datetime.datetime.utcnow()
        db.commit()
    db.refresh(d)
    return draft_to_dict(d)


def submit_draft(db: Session, draft_id: int, user_id: Optional[int] = None) -> Dict:
    """draft/rejected → pending（提交人工审批；被拒草稿修改后可重新提交）"""
    d = get_draft(db, draft_id)
    if d.status not in (DRAFT_STATUS_DRAFT, DRAFT_STATUS_REJECTED):
        raise OutreachError(f"只有 draft/rejected 状态可提交审批（当前 {d.status}）")
    if not d.recipient_email:
        # 未指定收件人则取客户主邮箱
        email = get_customer_primary_email(db, d.customer_id)
        if not email:
            raise OutreachError("客户没有可用邮箱，请先在客户详情添加邮箱或手动填写收件人")
        d.recipient_email = email
    d.status = DRAFT_STATUS_PENDING
    d.updated_at = datetime.datetime.utcnow()
    db.commit()
    db.refresh(d)
    return draft_to_dict(d)


def approve_draft(
    db: Session,
    draft_id: int,
    *,
    sender_account_id: int,
    user_id: Optional[int] = None,
    note: Optional[str] = None,
    recipient_email: Optional[str] = None,
) -> Dict:
    """pending → approved（人工确认发信，绑定发件账户）"""
    d = get_draft(db, draft_id)
    if d.status != DRAFT_STATUS_PENDING:
        raise OutreachError(f"只有 pending 状态的草稿可审批（当前 {d.status}）")
    account = db.query(MailSenderAccount).filter(
        MailSenderAccount.id == sender_account_id,
        MailSenderAccount.user_id == user_id,
    ).first() if user_id else db.query(MailSenderAccount).filter(
        MailSenderAccount.id == sender_account_id).first()
    if account is None:
        raise OutreachError("发件账户不存在或无权使用", status_code=404)
    if not account.enabled or account.status != "active":
        raise OutreachError("发件账户未启用")

    # 审批前确定最终收件人并持久化（显式指定 > 草稿已填 > 客户主邮箱）
    recipient = normalize_recipient(
        recipient_email or d.recipient_email or ""
    ) or get_customer_primary_email(db, d.customer_id) or ""
    if not recipient:
        raise OutreachError("缺少收件邮箱：请为客户添加邮箱或手动填写收件人", status_code=400)
    d.recipient_email = recipient
    blocked = check_blacklist(db, recipient)
    if blocked:
        raise OutreachError(f"收件人被列入禁止联系名单（{blocked['reason']}），不能审批发送", status_code=409)

    d.status = DRAFT_STATUS_APPROVED
    d.sender_account_id = account.id
    d.reviewed_by_user_id = user_id
    d.reviewed_at = datetime.datetime.utcnow()
    d.approved_at = datetime.datetime.utcnow()
    if note is not None:
        d.review_note = note
    d.updated_at = datetime.datetime.utcnow()
    db.commit()
    db.refresh(d)
    return draft_to_dict(d)


def reject_draft(db: Session, draft_id: int, *, reason: str, user_id: Optional[int] = None) -> Dict:
    """pending → rejected"""
    d = get_draft(db, draft_id)
    if d.status != DRAFT_STATUS_PENDING:
        raise OutreachError(f"只有 pending 状态可拒绝（当前 {d.status}）")
    d.status = DRAFT_STATUS_REJECTED
    d.review_note = reason or d.review_note
    d.reviewed_by_user_id = user_id
    d.reviewed_at = datetime.datetime.utcnow()
    d.updated_at = datetime.datetime.utcnow()
    db.commit()
    db.refresh(d)
    return draft_to_dict(d)


def cancel_draft(db: Session, draft_id: int, user_id: Optional[int] = None) -> Dict:
    d = get_draft(db, draft_id)
    if d.status in (DRAFT_STATUS_SENT, DRAFT_STATUS_CANCELLED):
        raise OutreachError(f"草稿已 {d.status}，不能取消")
    d.status = DRAFT_STATUS_CANCELLED
    d.updated_at = datetime.datetime.utcnow()
    db.commit()
    db.refresh(d)
    return draft_to_dict(d)


def get_customer_primary_email(db: Session, customer_id: int) -> Optional[str]:
    from app.database import CustomerEmail
    row = (
        db.query(CustomerEmail)
        .filter(CustomerEmail.customer_id == customer_id)
        .order_by(CustomerEmail.is_primary.desc(), CustomerEmail.created_at.asc())
        .first()
    )
    if row:
        return row.email
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if customer and customer.emails:
        emails = _load_json(customer.emails, [])
        if isinstance(emails, list) and emails:
            return str(emails[0]).strip().lower()
    return None


# ── 全局退订名单（#9） ────────────────────────────────────────────

def check_blacklist(db: Session, email: str) -> Optional[Dict]:
    """发送前检查：精确邮箱 + 主域名是否命中黑名单。

    Returns: None=可发；dict=命中（含 reason）
    """
    email = normalize_recipient(email)
    if not email:
        return None
    domain = extract_domain(email)
    hit = (
        db.query(UnsubscribeBlacklist)
        .filter(
            (UnsubscribeBlacklist.email == email) |
            ((UnsubscribeBlacklist.email == None) & (UnsubscribeBlacklist.domain == domain))
        )
        .first()
    )
    if hit:
        return {"email": email, "domain": domain, "reason": hit.reason, "note": hit.note}
    return None


def add_blacklist(
    db: Session,
    *,
    email: Optional[str] = None,
    domain: Optional[str] = None,
    reason: str = "manual",
    source: Optional[str] = None,
    source_message_id: Optional[str] = None,
    note: Optional[str] = None,
    created_by_user_id: Optional[int] = None,
) -> Dict:
    email = normalize_recipient(email) if email else None
    domain = (domain or "").strip().lower().lstrip("www.") or None
    if not email and not domain:
        raise OutreachError("email 或 domain 至少填一项")
    if reason not in ("unsubscribe", "bounce", "complaint", "manual"):
        reason = "manual"
    # email 唯一；domain 幂等
    existing = None
    if email:
        existing = db.query(UnsubscribeBlacklist).filter(UnsubscribeBlacklist.email == email).first()
    if existing:
        existing.reason = reason
        existing.source = source or existing.source
        existing.note = note or existing.note
        existing.source_message_id = source_message_id or existing.source_message_id
    else:
        dup_domain = db.query(UnsubscribeBlacklist).filter(
            UnsubscribeBlacklist.domain == domain, UnsubscribeBlacklist.email == None
        ).first() if domain and not email else None
        if dup_domain and not email:
            dup_domain.reason = reason
            existing = dup_domain
        else:
            row = UnsubscribeBlacklist(
                email=email, domain=domain, reason=reason, source=source,
                source_message_id=source_message_id, note=note,
                created_by_user_id=created_by_user_id,
            )
            db.add(row)
    db.commit()
    return {"ok": True, "message": "已加入禁止联系名单"}


def list_blacklist(db: Session, page: int = 1, page_size: int = 50) -> Tuple[List[Dict], int]:
    q = db.query(UnsubscribeBlacklist)
    total = q.count()
    rows = q.order_by(UnsubscribeBlacklist.created_at.desc()).offset((max(1, page) - 1) * page_size).limit(page_size).all()
    return [
        {
            "id": r.id, "email": r.email, "domain": r.domain, "reason": r.reason,
            "source": r.source, "note": r.note,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ], total


def remove_blacklist(db: Session, entry_id: int) -> bool:
    row = db.query(UnsubscribeBlacklist).filter(UnsubscribeBlacklist.id == entry_id).first()
    if row is None:
        raise OutreachError("名单记录不存在", status_code=404)
    db.delete(row)
    db.commit()
    return True


# ── 幂等键与发送日志（#6/#7） ─────────────────────────────────────

def build_idempotency_key(draft_id: int, recipient_email: str, subject: str) -> str:
    """幂等键 = draft + recipient + subject 摘要（方案 8.2 思路的 Phase1 落地）。"""
    raw = f"draft:{draft_id}|to:{normalize_recipient(recipient_email)}|subj:{hashlib.sha256((subject or '').encode('utf-8')).hexdigest()[:16]}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def has_sent(db: Session, idempotency_key: str) -> bool:
    """幂等：是否已存在成功发送记录"""
    return (
        db.query(OutreachSendLog)
        .filter(
            OutreachSendLog.idempotency_key == idempotency_key,
            OutreachSendLog.status == SEND_LOG_SENT,
        )
        .first()
        is not None
    )


def count_sent_today(db: Session, sender_account_id: int) -> int:
    """当日已发送数（UTC 自然日），用于每日上限检查（#8）"""
    today_start = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.query(OutreachSendLog)
        .filter(
            OutreachSendLog.sender_account_id == sender_account_id,
            OutreachSendLog.status == SEND_LOG_SENT,
            OutreachSendLog.sent_at >= today_start,
        )
        .count()
    )


def check_daily_limit(db: Session, account: MailSenderAccount, extra: int = 1) -> None:
    """检查发件账户每日上限；超限抛 OutreachError。"""
    if not account.daily_limit or account.daily_limit <= 0:
        return
    sent_today = count_sent_today(db, account.id)
    if sent_today + extra > account.daily_limit:
        raise OutreachError(
            f"发件账户 {account.email_address} 今日已达发送上限（{account.daily_limit} 封），请明日再发或调高账户限额",
            status_code=429,
        )


def create_send_log(
    db: Session,
    *,
    draft: OutreachDraft,
    account: MailSenderAccount,
    to_address: str,
) -> OutreachSendLog:
    """创建 queued 发送日志（幂等键唯一，重复创建返回已存在/冲突）。"""
    to_address = normalize_recipient(to_address)
    idem_key = build_idempotency_key(draft.id, to_address, draft.subject)
    existing = db.query(OutreachSendLog).filter(
        OutreachSendLog.idempotency_key == idem_key
    ).first()
    if existing:
        raise OutreachError("该草稿已有发送记录（幂等键冲突），请勿重复发送", status_code=409)
    log = OutreachSendLog(
        draft_id=draft.id,
        customer_id=draft.customer_id,
        sender_account_id=account.id,
        idempotency_key=idem_key,
        provider="himalaya",
        from_address=account.email_address,
        to_address=to_address,
        subject=draft.subject,
        snippet=draft.body_text[:200],
        status=SEND_LOG_QUEUED,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def send_log_to_dict(log: OutreachSendLog) -> Dict:
    return {
        "id": log.id,
        "draft_id": log.draft_id,
        "customer_id": log.customer_id,
        "sender_account_id": log.sender_account_id,
        "idempotency_key": log.idempotency_key,
        "provider": log.provider,
        "provider_message_id": log.provider_message_id,
        "internet_message_id": log.internet_message_id,
        "thread_id": log.thread_id,
        "from_address": log.from_address,
        "to_address": log.to_address,
        "subject": log.subject,
        "snippet": log.snippet,
        "status": log.status,
        "attempts": log.attempts,
        "error_code": log.error_code,
        "error_message": log.error_message,
        "sent_at": log.sent_at.isoformat() if log.sent_at else None,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


def list_send_logs(
    db: Session,
    *,
    draft_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 25,
) -> Tuple[List[Dict], int]:
    q = db.query(OutreachSendLog)
    if draft_id:
        q = q.filter(OutreachSendLog.draft_id == draft_id)
    if customer_id:
        q = q.filter(OutreachSendLog.customer_id == customer_id)
    if status:
        q = q.filter(OutreachSendLog.status == status)
    total = q.count()
    rows = q.order_by(OutreachSendLog.id.desc()).offset((max(1, page) - 1) * page_size).limit(page_size).all()
    return [send_log_to_dict(r) for r in rows], total


# ── 审批后入队发送（Phase1：#6 幂等键 + Worker 任务） ─────────────

def enqueue_draft_send(
    db: Session,
    draft_id: int,
    *,
    user_id: Optional[int] = None,
    immediate: bool = True,
) -> Dict:
    """把已审批草稿入队发送任务（send_email）。

    - 幂等：任务级 idempotency_key = send:draft:{id}，已存在任务不重复入队；
    - immediate=True 时 Web 进程同步兜底执行（无独立 Worker 环境也能发信），
      否则只入队等待 Worker 消费。两种路径共用同一幂等/状态收口。
    """
    from app.services import task_service as ts
    from app.models.automation import TASK_SEND_EMAIL, TASK_QUEUED

    draft = get_draft(db, draft_id)
    # 幂等重放：草稿已发送 → 直接返回已存在发送任务，不重复入队（方案 8.2）
    if draft.status == DRAFT_STATUS_SENT:
        existing_task = None
        if draft.id:
            existing_task = (
                db.query(AutomationTask)
                .filter(AutomationTask.idempotency_key == f"send:draft:{draft.id}")
                .first()
            )
        return {
            "already_sent": True,
            "task": ts.task_to_dict(existing_task) if existing_task else None,
            "draft": draft_to_dict(draft),
        }
    if draft.status != DRAFT_STATUS_APPROVED:
        raise OutreachError(f"只有 approved 状态的草稿可发送（当前 {draft.status}）")

    task_idem = f"send:draft:{draft.id}"
    task = ts.enqueue_task(
        db,
        task_type=TASK_SEND_EMAIL,
        payload={"draft_id": draft.id, "recipient_email": draft.recipient_email},
        idempotency_key=task_idem,
        max_attempts=5,
        created_by_user_id=user_id,
    )

    if immediate and task.status == TASK_QUEUED:
        # 同步兜底：仅当本轮新入队（status=queued）才执行一次；
        # 已存在的任务（retry_wait/failed 等）交由任务状态机/Worker 处理。
        from app.workers.runner import execute_task
        try:
            execute_task(db, task)
        except Exception as e:  # noqa: BLE001 - 同步路径失败不阻断返回
            logger.warning("同步执行发送任务 %s 失败: %s", task.id, e)

    db.refresh(draft)
    db.refresh(task)
    return {
        "task": ts.task_to_dict(task),
        "draft": draft_to_dict(draft),
    }

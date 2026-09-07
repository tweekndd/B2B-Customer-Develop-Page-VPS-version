"""
任务处理器（Phase1 新增：方案 8.3 任务处理器）

task_type → handler 映射。handler 负责执行外部副作用动作，并在成功/失败时
推进任务状态；是否重试由 task_service.mark_failed 依据错误分类决定。

send_email handler 完整链路（方案 Phase1 交付标准）：
    approved 草稿 → 幂等检查 → 退订名单 → 每日上限 → Himalaya 发送
    → 保存发送日志(provider/internet message id) → 草稿 sent
    → 回写客户跟进状态与发信记录
"""
import datetime
import json
import logging
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.database import (
    AutomationTask,
    Customer,
    CustomerEmailActivity,
    MailSenderAccount,
    OutreachDraft,
    OutreachSendLog,
)
from app.models.outreach_send import (
    DRAFT_STATUS_SENT, DRAFT_STATUS_SENDING, DRAFT_STATUS_FAILED,
    SEND_LOG_SENDING, SEND_LOG_SENT, SEND_LOG_FAILED,
)
from app.services import (
    himalaya_service as hs,
    mail_sender_service as mss,
    outreach_service as osvc,
    task_service,
)

logger = logging.getLogger("task_handlers")


class RetryableTaskError(Exception):
    """可重试错误（网络/限流/SMTP 临时错误等）"""

    def __init__(self, message: str, code: str = "retryable", retry_after: int = 60):
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after


class FatalTaskError(Exception):
    """不可重试错误（认证失败/无效邮箱/黑名单等）"""

    def __init__(self, message: str, code: str = "fatal"):
        super().__init__(message)
        self.code = code


# ═══════════════════════════════════════════════════════════════
# send_email
# ═══════════════════════════════════════════════════════════════

def handle_send_email(db: Session, task: AutomationTask) -> Dict:
    """执行一封已审批草稿的发送（幂等）。任务失败抛出异常由 runner 捕获分类。"""
    payload = {}
    if task.payload_json:
        try:
            payload = json.loads(task.payload_json)
        except (json.JSONDecodeError, TypeError):
            payload = {}
    draft_id = payload.get("draft_id")
    if not draft_id:
        raise FatalTaskError("任务缺少 draft_id", code="bad_payload")

    draft = db.query(OutreachDraft).filter(OutreachDraft.id == draft_id).first()
    if draft is None:
        raise FatalTaskError(f"草稿 {draft_id} 不存在", code="draft_missing")

    # 幂等：已成功发送 → 直接成功结束（防重放）
    account = None
    if draft.sender_account_id:
        account = db.query(MailSenderAccount).filter(
            MailSenderAccount.id == draft.sender_account_id).first()
    if account is None:
        raise FatalTaskError("草稿未绑定可用的发件账户", code="no_sender_account")

    recipient = draft.recipient_email or osvc.get_customer_primary_email(db, draft.customer_id)
    if not recipient:
        raise FatalTaskError("缺少收件邮箱", code="no_recipient")

    idem_key = osvc.build_idempotency_key(draft.id, recipient, draft.subject)
    if osvc.has_sent(db, idem_key):
        # 已发送：把草稿与任务一并推进为成功（防重复发送的关键幂等检查）
        _finalize_sent(db, draft, account, recipient, idem_key,
                       provider_message_id=payload.get("provider_message_id"))
        return {"skipped": "already_sent", "draft_id": draft.id}

    # 退订名单检查（发送前最后一道闸）
    blocked = osvc.check_blacklist(db, recipient)
    if blocked:
        raise FatalTaskError(
            f"收件人被禁止联系（{blocked['reason']}）", code="blacklisted")

    # 每日上限
    osvc.check_daily_limit(db, account)

    # 生成 himalaya 配置（密码为空视为配置未完成）
    config_path = mss.ensure_himalaya_config(account)
    if config_path is None:
        raise FatalTaskError(
            f"发件账户 {account.email_address} 未配置 SMTP 密码，无法发送",
            code="account_no_password")

    if not hs.is_available():
        raise RetryableTaskError(
            "Himalaya CLI 不可用（未安装或不在 PATH）——请安装后重试；"
            "Docker 部署镜像已内置 himalaya", code="himalaya_unavailable")

    # 尝试前把草稿/日志置为 sending
    draft.status = DRAFT_STATUS_SENDING
    log = db.query(OutreachSendLog).filter(
        OutreachSendLog.idempotency_key == idem_key).first()
    if log is None:
        log = OutreachSendLog(
            draft_id=draft.id, customer_id=draft.customer_id,
            sender_account_id=account.id, idempotency_key=idem_key,
            provider="himalaya", from_address=account.email_address,
            to_address=recipient, subject=draft.subject,
            snippet=draft.body_text[:200], status=SEND_LOG_SENDING,
            attempts=1,
        )
        db.add(log)
    else:
        log.status = SEND_LOG_SENDING
        log.attempts = (log.attempts or 0) + 1
        log.error_message = None
    db.commit()
    db.refresh(draft)
    db.refresh(log)

    # 构造并发送
    try:
        result = hs.send_message(
            config_path=config_path,
            from_address=account.email_address,
            from_name=account.display_name or account.email_address,
            to_addresses=[recipient],
            subject=draft.subject,
            body_text=draft.body_text,
            timeout=120,
        )
    except hs.HimalayaUnavailableError as e:
        raise RetryableTaskError(str(e), code="himalaya_unavailable")
    except hs.HimalayaError as e:
        # 认证/连接类错误可重试；解析不了具体类型时按可重试处理（429 除外）
        msg = str(e)
        if "认证" in msg or "auth" in msg.lower() or "login" in msg.lower():
            mss.set_status(db, account, "error", msg)
            raise RetryableTaskError(msg, code="smtp_auth")
        raise RetryableTaskError(msg, code="smtp_error")

    # 发送成功：落日志 + 草稿 sent + 回写客户
    _finalize_sent(
        db, draft, account, recipient, idem_key,
        provider_message_id=result.get("provider_message_id"),
        internet_message_id=result.get("internet_message_id"),
        log=log,
    )
    return {"sent": True, "draft_id": draft.id,
            "provider_message_id": result.get("provider_message_id")}


def _finalize_sent(
    db: Session,
    draft: OutreachDraft,
    account: MailSenderAccount,
    recipient: str,
    idem_key: str,
    provider_message_id: Optional[str] = None,
    internet_message_id: Optional[str] = None,
    log: Optional[OutreachSendLog] = None,
):
    """发送成功后的状态收口（幂等可安全重复调用）。"""
    now = datetime.datetime.utcnow()
    if log is None:
        log = db.query(OutreachSendLog).filter(
            OutreachSendLog.idempotency_key == idem_key).first()
        if log is None:
            log = OutreachSendLog(
                draft_id=draft.id, customer_id=draft.customer_id,
                sender_account_id=account.id, idempotency_key=idem_key,
                provider="himalaya", from_address=account.email_address,
                to_address=recipient, subject=draft.subject,
                snippet=draft.body_text[:200],
            )
            db.add(log)
    log.status = SEND_LOG_SENT
    log.provider_message_id = provider_message_id or log.provider_message_id
    log.internet_message_id = internet_message_id or log.internet_message_id
    log.sent_at = now
    log.error_code = None
    log.error_message = None

    draft.status = DRAFT_STATUS_SENT
    draft.updated_at = now

    # 客户状态联动：已发邮件（不降级「已回复/成单」等更高状态）
    customer = db.query(Customer).filter(Customer.id == draft.customer_id).first()
    if customer:
        if customer.status not in ("已回复", "成单", "无效线索"):
            customer.status = "已发邮件"
        customer.last_email_sent_at = now

    # 发信记录（详情页发信记录 tab 复用 V5.2 CustomerEmailActivity）
    matched_domain = osvc.extract_domain(recipient)
    dup = db.query(CustomerEmailActivity).filter(
        CustomerEmailActivity.mail_account_id == account.id,
        CustomerEmailActivity.provider == "himalaya",
        CustomerEmailActivity.provider_message_id == (provider_message_id or internet_message_id or idem_key[:40]),
        CustomerEmailActivity.matched_domain == matched_domain,
    ).first()
    if dup is None:
        activity = CustomerEmailActivity(
            customer_id=draft.customer_id,
            mail_account_id=account.id,
            provider="himalaya",
            provider_message_id=provider_message_id or internet_message_id or idem_key[:40],
            internet_message_id=internet_message_id,
            from_address=account.email_address,
            to_addresses_json=json.dumps([recipient]),
            subject=draft.subject,
            sent_at=now,
            matched_domain=matched_domain,
            match_type="manual_email" if recipient else "exact_domain",
            snippet=draft.body_text[:200],
        )
        db.add(activity)
    db.commit()


# ═══════════════════════════════════════════════════════════════
# handler 注册表
# ═══════════════════════════════════════════════════════════════

HANDLERS = {
    "send_email": handle_send_email,
}


def get_handler(task_type: str):
    return HANDLERS.get(task_type)

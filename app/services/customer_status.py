"""客户状态机服务（Phase2 新增：方案 7.2）

规则：
- 人工（manual / approve）：任意流转，均留痕。
- 自动（mail_send / mail_reply / bounce / unsubscribe）：严格「只升不降」，
  无效线索为终态（人工可重新激活为 待联系）。
- 每次变更写入 customer_status_history 审计。
"""
import datetime
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.database import Customer, CustomerStatusHistory
from app.models.inbox import (
    STATUS_待联系, STATUS_已发邮件, STATUS_已回复, STATUS_无效线索, STATUS_成单,
    STATUS_AUTO_UPGRADE,
)

logger = logging.getLogger("customer_status")

# 自动升级合法触发源（人工触发器不校验）
_AUTO_TRIGGERS = {"mail_send", "mail_reply", "bounce", "unsubscribe"}

# 触发源申请的状态优先级（用于自动升级判定：只升不降）
_STATUS_LEVEL = {
    STATUS_待联系: 0,
    STATUS_已发邮件: 1,
    STATUS_已回复: 2,
    STATUS_成单: 3,
    STATUS_无效线索: -1,  # 终态
}

_HISTORY_BY_TRIGGER = {
    "mail_send": "系统检测到已发送开发信",
    "mail_reply": "收到客户回复",
    "bounce": "退信（邮箱无效/域名不存在）",
    "unsubscribe": "客户退订",
}

# 供 inbox_sync 等模块复用文案
TRIGGER_DEFAULT_NOTE = _HISTORY_BY_TRIGGER


class StatusTransitionError(Exception):
    """非法自动流转（例如 已回复 → 已发邮件 的不降级回退）"""


def _can_auto_transition(current: Optional[str], target: str) -> bool:
    """自动流转是否合法：只升不降；无效线索不进（终态仅人工处理）。"""
    if target == STATUS_无效线索:
        return False  # 自动不进终态
    if not current or current not in _STATUS_LEVEL:
        return True  # 未设状态：允许升级到任意非终态
    allowed = STATUS_AUTO_UPGRADE.get(current, set())
    if target not in allowed and target != current:
        return False
    return True


def record_history(
    db: Session,
    *,
    customer_id: int,
    old_status: Optional[str],
    new_status: str,
    trigger: str = "manual",
    source_message_id: Optional[int] = None,
    source_task_id: Optional[int] = None,
    note: Optional[str] = None,
    created_by_user_id: Optional[int] = None,
) -> CustomerStatusHistory:
    """写入状态变更审计记录（不校验，只留痕）。"""
    if note is None:
        note = _HISTORY_BY_TRIGGER.get(trigger)
    rec = CustomerStatusHistory(
        customer_id=customer_id,
        old_status=old_status,
        new_status=new_status,
        trigger=trigger,
        source_message_id=source_message_id,
        source_task_id=source_task_id,
        note=note,
        created_by_user_id=created_by_user_id,
        created_at=datetime.datetime.utcnow(),
    )
    db.add(rec)
    return rec


def transition_customer_status(
    db: Session,
    customer: Customer,
    new_status: str,
    *,
    trigger: str = "manual",
    source_message_id: Optional[int] = None,
    source_task_id: Optional[int] = None,
    note: Optional[str] = None,
    created_by_user_id: Optional[int] = None,
    force: bool = False,
) -> bool:
    """推进客户状态。

    返回 True 表示发生变更并留痕；False 表示无需变更（状态未变）。
    自动触发非法降级时抛 StatusTransitionError（调用方可捕获后改为仅留痕）。
    """
    old = customer.status or None
    if old == new_status:
        # 状态不变也记录一次「同状态确认」来源标记（便于审计多次回复）
        record_history(db, customer_id=customer.id, old_status=old, new_status=new_status,
                       trigger=trigger, source_message_id=source_message_id,
                       source_task_id=source_task_id, note=note, created_by_user_id=created_by_user_id)
        return False

    if not force and trigger in _AUTO_TRIGGERS:
        if not _can_auto_transition(old, new_status):
            raise StatusTransitionError(
                f"非法自动流转: {old} → {new_status}（不降级 + 无效线索仅人工）"
            )

    customer.status = new_status
    record_history(db, customer_id=customer.id, old_status=old, new_status=new_status,
                   trigger=trigger, source_message_id=source_message_id,
                   source_task_id=source_task_id, note=note, created_by_user_id=created_by_user_id)
    db.add(customer)
    return True


def revive_customer(db: Session, customer: Customer, *, created_by_user_id: Optional[int] = None) -> bool:
    """人工把 无效线索 重新激活为 待联系（force=True）。"""
    return transition_customer_status(
        db, customer, STATUS_待联系, trigger="manual", created_by_user_id=created_by_user_id,
        force=True,
    )
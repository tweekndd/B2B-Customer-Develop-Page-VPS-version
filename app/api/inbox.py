"""回复中心 API（Phase2 新增：方案 13.2 回复中心）

列表只读元数据（不加载正文），详情按需从对象存储读正文/附件。
同步接口触发 imaplib 收件同步（同步执行；生产建议由周期任务/手动按钮触发）。
"""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import (
    get_db,
    Customer,
    MailSenderAccount,
    MailMessage,
    MailAttachment,
    CustomerTodo,
)
from app.auth import require_user
from app.models.inbox import (
    TODO_OPEN, TODO_DONE, TODO_DISMISSED,
    TODO_RESPOND, TODO_FOLLOW_UP, TODO_QUOTE_REQUEST, TODO_COMPLAINT, TODO_OTHER,
)
from app.services import object_storage as objsv
from app.services import inbox_sync_service as iss

router = APIRouter(tags=["inbox"])

# 分类展示用中英映射（前端也维护一份）
INTENT_LABELS = {
    "rfq": "询价",
    "interest": "意向",
    "question": "提问",
    "not_interested": "婉拒",
    "unsubscribe": "退订",
    "out_of_office": "自动回复",
    "bounce": "退信",
    "complaint": "投诉",
    "other": "其他",
    None: "未分类",
}

TODO_LABELS = {
    TODO_RESPOND: "需回复",
    TODO_FOLLOW_UP: "跟进",
    TODO_QUOTE_REQUEST: "询价",
    TODO_COMPLAINT: "投诉",
    TODO_OTHER: "其他",
}


def _customer_map(db: Session, message_ids):
    """批量取客户名，减少 N+1"""
    result = {}
    for m in message_ids:
        if m and m not in result:
            c = db.query(Customer).filter(Customer.id == m).first()
            result[m] = c.company_name if c else None
    return result


def _message_to_dict(m: MailMessage, account_map=None, customer_map=None) -> dict:
    return {
        "id": m.id,
        "provider": m.provider or "imap",
        "thread_id": m.thread_id or "",
        "account_id": m.mail_account_id,
        "account_email": (account_map or {}).get(m.mail_account_id) or "",
        "customer_id": m.customer_id,
        "customer_name": (customer_map or {}).get(m.customer_id) or "",
        "direction": m.direction or "in",
        "from_address": m.from_address or "",
        "subject": m.subject or "",
        "snippet": m.snippet or "",
        "sent_at": m.sent_at.isoformat() if m.sent_at else None,
        "received_at": m.received_at.isoformat() if m.received_at else None,
        "classification": m.classification or None,
        "classification_label": INTENT_LABELS.get(m.classification, "未分类"),
        "classification_confidence": m.classification_confidence,
        "classification_json": m.classification_json,
        "matched_domain": m.matched_domain or "",
        "is_handled": bool(m.is_handled),
        "is_ignored": bool(m.is_ignored),
        "has_attachments": m.attachments_count if hasattr(m, "attachments_count") else False,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


@router.get("/inbox/messages")
def list_inbox_messages(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    classification: Optional[str] = Query(None, description="按意图筛选"),
    matched_only: bool = Query(False, description="只看已匹配客户"),
    handled: Optional[str] = Query(None, description="handled/unhandled/all"),
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """回复中心列表（默认未处理优先，时间倒序）。"""
    query = db.query(MailMessage).filter(MailMessage.direction == "in")
    if classification:
        query = query.filter(MailMessage.classification == classification)
    if matched_only:
        query = query.filter(MailMessage.customer_id.isnot(None))
    if handled == "handled":
        query = query.filter(MailMessage.is_handled == 1)
    elif handled == "unhandled":
        query = query.filter(MailMessage.is_handled == 0)
    total = query.count()
    rows = (
        query.order_by(MailMessage.received_at.desc().nullslast())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    account_ids = {m.mail_account_id for m in rows if m.mail_account_id}
    account_map = {}
    if account_ids:
        for a in db.query(MailSenderAccount).filter(MailSenderAccount.id.in_(account_ids)).all():
            account_map[a.id] = a.email_address
    customer_map = _customer_map(db, {m.customer_id for m in rows if m.customer_id})

    items = [_message_to_dict(m, account_map, customer_map) for m in rows]
    # 附件计数（一次性聚合，避免 N+1）
    att_count = {}
    if rows:
        att_rows = db.query(MailAttachment.message_id, MailAttachment.id) \
            .filter(MailAttachment.message_id.in_([m.id for m in rows])).all()
        for message_id, _ in att_rows:
            att_count[message_id] = att_count.get(message_id, 0) + 1
    for it in items:
        it["has_attachments"] = att_count.get(it["id"], 0) > 0

    return {
        "messages": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "intent_labels": INTENT_LABELS,
    }


@router.get("/inbox/messages/{message_id}")
def get_inbox_message(
    message_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """详情（按需从对象存储加载正文 + 附件元数据）。"""
    m = db.query(MailMessage).filter(MailMessage.id == message_id).first()
    if m is None:
        raise HTTPException(status_code=404, detail="邮件不存在")
    body = ""
    if m.body_text_object_id:
        body = objsv.get_object(db, m.body_text_object_id).decode("utf-8", errors="replace")
    attachments = db.query(MailAttachment).filter(MailAttachment.message_id == m.id).all()
    customer_name = ""
    if m.customer_id:
        c = db.query(Customer).filter(Customer.id == m.customer_id).first()
        customer_name = c.company_name if c else ""
    account_email = ""
    if m.mail_account_id:
        a = db.query(MailSenderAccount).filter(MailSenderAccount.id == m.mail_account_id).first()
        account_email = a.email_address if a else ""
    parsed_json = None
    if m.classification_json:
        try:
            parsed_json = json.loads(m.classification_json)
        except (json.JSONDecodeError, TypeError):
            parsed_json = None

    d = _message_to_dict(m, {m.mail_account_id: account_email}, {m.customer_id: customer_name})
    d["account_email"] = account_email
    d["body"] = body
    d.pop("attachments_count", None)
    d["attachments"] = [{
        "id": a.id,
        "filename": a.filename,
        "content_type": a.content_type,
        "size_bytes": a.size_bytes,
        "sha256": a.sha256,
    } for a in attachments]
    d["classification_payload"] = parsed_json
    return d


@router.get("/inbox/messages/{message_id}/attachments/{attachment_id}")
def download_attachment(
    message_id: int,
    attachment_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """下载附件（从对象存储读取）。"""
    att = db.query(MailAttachment).filter(
        MailAttachment.id == attachment_id,
        MailAttachment.message_id == message_id,
    ).first()
    if att is None or not att.object_id:
        raise HTTPException(status_code=404, detail="附件不存在")
    data = objsv.get_object(db, att.object_id)
    if not data:
        raise HTTPException(status_code=404, detail="附件内容缺失")
    filename = (att.filename or f"attachment_{attachment_id}").replace('"', "")
    return Response(
        content=data,
        media_type=att.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/inbox/messages/{message_id}/handle")
def mark_message_handled(
    message_id: int,
    handled: bool = Query(True),
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    m = db.query(MailMessage).filter(MailMessage.id == message_id).first()
    if m is None:
        raise HTTPException(status_code=404, detail="邮件不存在")
    m.is_handled = 1 if handled else 0
    db.commit()
    return {"message": "已标记" if handled else "已取消标记"}


@router.post("/inbox/messages/{message_id}/ignore")
def ignore_message(
    message_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    m = db.query(MailMessage).filter(MailMessage.id == message_id).first()
    if m is None:
        raise HTTPException(status_code=404, detail="邮件不存在")
    m.is_ignored = 1
    m.is_handled = 1
    db.commit()
    return {"message": "已忽略"}


@router.post("/inbox/sync")
def sync_inbox(
    account_id: Optional[int] = Query(None, description="指定账户；缺省=全部启用账户"),
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """手动触发收件同步（同步执行；建议间隔调用）。"""
    account = None
    if account_id:
        account = db.query(MailSenderAccount).filter(
            MailSenderAccount.id == account_id,
            MailSenderAccount.user_id == user.id,
        ).first()
        if account is None:
            raise HTTPException(status_code=404, detail="账户不存在")
    try:
        results = iss.sync_all_accounts(db, account_ids=[account_id] if account_id else None)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"同步失败: {exc}")
    total = sum(s.get("new", 0) for s in results.values())
    return {"message": f"同步完成（新增 {total} 封新回复）", "results": results}


# ═══════════════════════════════════════════════════════════════
# 待办（回复中心动作）
# ═══════════════════════════════════════════════════════════════

def _todo_to_dict(t: CustomerTodo, customer_map=None) -> dict:
    details = None
    if t.details_json:
        try:
            details = json.loads(t.details_json)
        except (json.JSONDecodeError, TypeError):
            details = None
    return {
        "id": t.id,
        "customer_id": t.customer_id,
        "customer_name": (customer_map or {}).get(t.customer_id) or "",
        "message_id": t.message_id,
        "todo_type": t.todo_type,
        "todo_label": TODO_LABELS.get(t.todo_type, t.todo_type),
        "summary": t.summary or "",
        "details": details,
        "priority": t.priority,
        "status": t.status,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "done_at": t.done_at.isoformat() if t.done_at else None,
    }


@router.get("/inbox/todos")
def list_todos(
    status: Optional[str] = Query(None, description="open/done/dismissed"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    query = db.query(CustomerTodo)
    if status:
        query = query.filter(CustomerTodo.status == status)
    else:
        query = query.filter(CustomerTodo.status == TODO_OPEN)
    total = query.count()
    rows = query.order_by(
        CustomerTodo.status.asc(),
        CustomerTodo.priority.asc(),
        CustomerTodo.created_at.desc(),
    ).offset((page - 1) * page_size).limit(page_size).all()
    customer_map = _customer_map(db, {t.customer_id for t in rows})
    items = [_todo_to_dict(t, customer_map) for t in rows]
    return {"todos": items, "total": total, "page": page, "page_size": page_size,
            "total_pages": max(1, (total + page_size - 1) // page_size),
            "todo_labels": TODO_LABELS}


@router.post("/inbox/todos/{todo_id}/done")
def complete_todo(todo_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    t = db.query(CustomerTodo).filter(CustomerTodo.id == todo_id).first()
    if t is None:
        raise HTTPException(status_code=404, detail="待办不存在")
    t.status = TODO_DONE
    import datetime
    t.done_at = datetime.datetime.utcnow()
    db.commit()
    return {"message": "待办已完成", "todo": _todo_to_dict(t)}


@router.post("/inbox/todos/{todo_id}/reopen")
def reopen_todo(todo_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    t = db.query(CustomerTodo).filter(CustomerTodo.id == todo_id).first()
    if t is None:
        raise HTTPException(status_code=404, detail="待办不存在")
    t.status = TODO_OPEN
    t.done_at = None
    db.commit()
    return {"message": "待办已重新打开", "todo": _todo_to_dict(t)}


@router.post("/inbox/todos/{todo_id}/dismiss")
def dismiss_todo(todo_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    t = db.query(CustomerTodo).filter(CustomerTodo.id == todo_id).first()
    if t is None:
        raise HTTPException(status_code=404, detail="待办不存在")
    t.status = TODO_DISMISSED
    db.commit()
    return {"message": "待办已忽略", "todo": _todo_to_dict(t)}
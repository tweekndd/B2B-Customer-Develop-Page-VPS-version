"""
客户发信记录 API（V5.2 新增）

详情页发信记录：列表（分页）/ 手动同步 / 忽略误匹配 / 删除。
"""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db, Customer, CustomerEmailActivity, MailAccount
from app.auth import require_user
from app.services import mail_account_service as mas
from app.services import mail_sync_service

router = APIRouter(tags=["mail_activities"])


def _activity_to_dict(a: CustomerEmailActivity) -> dict:
    try:
        to_list = json.loads(a.to_addresses_json) if a.to_addresses_json else []
    except (json.JSONDecodeError, TypeError):
        to_list = []
    try:
        cc_list = json.loads(a.cc_addresses_json) if a.cc_addresses_json else []
    except (json.JSONDecodeError, TypeError):
        cc_list = []
    return {
        "id": a.id,
        "customer_id": a.customer_id,
        "provider": a.provider or "gmail",
        "email_address": a.email_address if hasattr(a, "email_address") else None,
        "from_address": a.from_address or "",
        "to_addresses": to_list,
        "cc_addresses": cc_list,
        "subject": a.subject or "",
        "sent_at": a.sent_at.isoformat() if a.sent_at else None,
        "matched_domain": a.matched_domain or "",
        "match_type": a.match_type or "exact_domain",
        "snippet": a.snippet or "",
        "is_ignored": bool(a.is_ignored),
        "thread_id": a.thread_id or "",
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


@router.get("/customers/{customer_id}/email-activities")
def list_email_activities(
    customer_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    include_ignored: bool = Query(False, description="是否包含已忽略记录"),
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """获取客户发信记录（默认不含已忽略，分页 + 时间倒序）"""
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="客户不存在")

    query = db.query(CustomerEmailActivity).filter(
        CustomerEmailActivity.customer_id == customer_id
    )
    if not include_ignored:
        query = query.filter(CustomerEmailActivity.is_ignored == 0)

    total = query.count()
    activities = (
        query.order_by(CustomerEmailActivity.sent_at.desc().nullslast())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    # 附带发件邮箱地址
    account_ids = {a.mail_account_id for a in activities if a.mail_account_id}
    email_map = {}
    if account_ids:
        accounts = db.query(MailAccount).filter(MailAccount.id.in_(account_ids)).all()
        email_map = {a.id: a.email_address for a in accounts}

    items = []
    for a in activities:
        d = _activity_to_dict(a)
        d["email_address"] = email_map.get(a.mail_account_id)
        items.append(d)

    return {
        "activities": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    }


@router.post("/customers/{customer_id}/email-activities/sync")
def sync_email_activities(
    customer_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """仅同步当前用户绑定的邮箱后返回匹配结果"""
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="客户不存在")

    accounts = mas.list_accounts(db, user.id)
    if not accounts:
        raise HTTPException(status_code=400, detail="尚未绑定邮箱账户，请先到设置页连接 Gmail")

    results = {}
    for account in accounts:
        if account.status in ("active", "reauth_required"):
            results[account.email_address] = mail_sync_service.sync_account(db, account)
    return {"message": "同步完成", "results": results}


@router.post("/email-activities/{activity_id}/ignore")
def ignore_activity(
    activity_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """忽略误匹配记录"""
    activity = db.query(CustomerEmailActivity).filter(
        CustomerEmailActivity.id == activity_id
    ).first()
    if not activity:
        raise HTTPException(status_code=404, detail="发信记录不存在")
    activity.is_ignored = 1
    db.commit()
    return {"message": "已忽略该记录"}


@router.delete("/email-activities/{activity_id}")
def delete_activity(
    activity_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """删除本地活动记录（不删除第三方邮箱原邮件）"""
    activity = db.query(CustomerEmailActivity).filter(
        CustomerEmailActivity.id == activity_id
    ).first()
    if not activity:
        raise HTTPException(status_code=404, detail="发信记录不存在")
    db.delete(activity)
    db.commit()
    return {"message": "已删除"}

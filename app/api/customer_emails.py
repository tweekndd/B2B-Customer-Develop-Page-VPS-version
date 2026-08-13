"""
客户邮箱维护 API（V5.1 新增）
结构化邮箱管理：手动新增 / 编辑 / 删除 / 设主邮箱 / 旧数据合并
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db, Customer
from app.auth import require_user
from app.services.customer_email_service import (
    get_customer_emails,
    upsert_customer_email,
    update_customer_email,
    delete_customer_email,
    merge_legacy_emails,
)

router = APIRouter(tags=["customer_emails"])


class AddEmailRequest(BaseModel):
    email: str
    notes: Optional[str] = None
    is_primary: bool = False


class UpdateEmailRequest(BaseModel):
    email: Optional[str] = None
    notes: Optional[str] = None
    is_primary: Optional[bool] = None
    verification: Optional[str] = None


def _email_record_to_dict(record) -> dict:
    return {
        "id": record.id,
        "customer_id": record.customer_id,
        "email": record.email,
        "local_part": record.local_part,
        "domain": record.domain,
        "source": record.source or "manual",
        "source_detail": record.source_detail,
        "first_name": record.first_name or "",
        "last_name": record.last_name or "",
        "position": record.position or "",
        "department": record.department or "",
        "phone": record.phone or "",
        "linkedin": record.linkedin or "",
        "score": record.score or 0,
        "verification": record.verification or "unknown",
        "notes": record.notes or "",
        "is_primary": bool(record.is_primary),
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


def _get_customer_or_404(db: Session, customer_id: int) -> Customer:
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="客户不存在")
    return customer


@router.get("/customers/{customer_id}/emails")
def list_emails(
    customer_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """获取客户的结构化邮箱列表"""
    _get_customer_or_404(db, customer_id)
    records = get_customer_emails(db, customer_id)
    return {"emails": [_email_record_to_dict(r) for r in records], "total": len(records)}


@router.post("/customers/{customer_id}/emails")
def add_email(
    customer_id: int,
    req: AddEmailRequest,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """手动新增邮箱（来源固定为 manual，防伪造第三方来源）"""
    _get_customer_or_404(db, customer_id)
    try:
        record = upsert_customer_email(
            db,
            customer_id,
            req.email,
            source="manual",
            notes=req.notes,
            created_by_user_id=user.id,
            is_primary=req.is_primary,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return {"message": "邮箱已添加", "email": _email_record_to_dict(record)}


@router.put("/customer-emails/{email_id}")
def update_email(
    email_id: int,
    req: UpdateEmailRequest,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """编辑邮箱、备注、主邮箱标记或验证状态"""
    try:
        record = update_customer_email(
            db,
            email_id,
            email=req.email,
            notes=req.notes,
            is_primary=req.is_primary,
            verification=req.verification,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="邮箱记录不存在")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return {"message": "邮箱已更新", "email": _email_record_to_dict(record)}


@router.delete("/customer-emails/{email_id}")
def delete_email(
    email_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """删除邮箱记录（不影响第三方发现缓存）"""
    if not delete_customer_email(db, email_id):
        raise HTTPException(status_code=404, detail="邮箱记录不存在")
    db.commit()
    return {"message": "邮箱已删除"}


@router.post("/customers/{customer_id}/emails/merge-legacy")
def merge_legacy(
    customer_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """把 Customer.emails JSON 中的旧邮箱并入 CustomerEmail 表（幂等）"""
    _get_customer_or_404(db, customer_id)
    try:
        result = merge_legacy_emails(db, customer_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="客户不存在")
    db.commit()
    return {"message": f"已合并 {result['merged']} 个旧邮箱", **result}

"""
外联 API（Phase1 新增）

生成 → 草稿 → 审批 → 入队发送 → 发送日志/黑名单/任务中心，全部走
outreach_generation_service / outreach_service / task_service / worker。

关键权限：
- 生成草稿（消耗 AI）→ 任意登录用户（同 email-draft 权限 check_ai_analysis_permission）
- 提交/编辑/拒绝草稿 → 本人或管理员（草稿 creator 关联）
- 审批/发送（对外副作用）→ 人工确认动作默认需登录用户显式操作（require_user）
"""
import datetime
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import require_admin, require_user, get_current_user
from app.database import Customer, OutreachDraft, get_db
from app.services import outreach_generation_service as ogs
from app.services import outreach_service as osvc

router = APIRouter(tags=["outreach"])


def _err(e: Exception, default_code: int = 400):
    code = getattr(e, "status_code", default_code)
    return HTTPException(status_code=code, detail=str(e))


def _customer_or_404(db: Session, customer_id: int) -> Customer:
    c = db.query(Customer).filter(Customer.id == customer_id).first()
    if c is None:
        raise HTTPException(status_code=404, detail="客户不存在")
    return c


# ═══════════════════════════════════════════════════════════════
# 生成（客户维度）
# ═══════════════════════════════════════════════════════════════

@router.post("/customers/{customer_id}/outreach/generate")
async def generate_outreach(
    customer_id: int,
    body: dict = None,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """按 Prompt 模板为指定客户生成外联草稿（可溯源）。

    body: {template_id?, product_keywords?[], product_name?, sender_company?,
           sender_signature?, call_to_action?, language?, extra_context?, auto_submit?}
    """
    body = body or {}
    customer = _customer_or_404(db, customer_id)
    try:
        result = await ogs.generate_outreach_draft(
            db, customer,
            user_id=user.id,
            template_id=body.get("template_id"),
            product_keywords=body.get("product_keywords"),
            product_name=body.get("product_name") or "",
            sender_company=body.get("sender_company") or "",
            sender_signature=body.get("sender_signature") or "",
            call_to_action=body.get("call_to_action") or "",
            language=body.get("language") or "auto",
            extra_context=body.get("extra_context") or "",
            auto_submit=bool(body.get("auto_submit", False)),
        )
    except ogs.GenerationError as e:
        raise _err(e)
    except Exception as e:  # noqa: BLE001
        raise _err(e, 502)
    return {"message": "草稿已生成，请人工审核", **result}


# ═══════════════════════════════════════════════════════════════
# 草稿列表 / 详情 / 状态机
# ═══════════════════════════════════════════════════════════════

@router.get("/outreach/drafts")
def list_drafts(
    status: Optional[str] = Query(None, description="draft/pending/approved/sent/rejected/failed"),
    customer_id: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    rows, total = osvc.list_drafts(db, status=status, customer_id=customer_id,
                                   page=page, page_size=page_size)
    return {"drafts": rows, "total": total, "page": page, "page_size": page_size}


@router.get("/outreach/drafts/{draft_id}")
def get_draft_detail(draft_id: int, db: Session = Depends(get_db),
                     user=Depends(require_user)):
    try:
        d = osvc.get_draft(db, draft_id)
    except osvc.OutreachError as e:
        raise _err(e, 404)
    # 附带生成溯源（若有关联 generation_run）
    extra = {}
    if d.generation_run_id:
        from app.database import GenerationRun
        run = db.query(GenerationRun).filter(GenerationRun.id == d.generation_run_id).first()
        if run:
            extra["generation_run"] = ogs._run_to_dict(run)
    return {"draft": osvc.draft_to_dict(d), **extra}


@router.put("/outreach/drafts/{draft_id}")
def edit_draft_api(
    draft_id: int,
    body: dict,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    try:
        d = osvc.edit_draft(db, draft_id,
                            subject=body.get("subject"),
                            body=body.get("body"),
                            recipient_email=body.get("recipient_email"),
                            recipient_name=body.get("recipient_name"),
                            user_id=user.id)
    except osvc.OutreachError as e:
        raise _err(e)
    return {"message": "草稿已更新", "draft": d}


@router.post("/outreach/drafts/{draft_id}/submit")
def submit_draft_api(draft_id: int, db: Session = Depends(get_db),
                     user=Depends(require_user)):
    """draft → pending（提交人工审批）"""
    try:
        d = osvc.submit_draft(db, draft_id, user_id=user.id)
    except osvc.OutreachError as e:
        raise _err(e)
    return {"message": "已提交人工审批", "draft": d}


@router.post("/outreach/drafts/{draft_id}/approve")
def approve_draft_api(
    draft_id: int,
    body: dict,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """pending → approved（人工确认；绑定发件账户；发送前黑名单最终校验）"""
    try:
        d = osvc.approve_draft(
            db, draft_id,
            sender_account_id=body.get("sender_account_id"),
            user_id=user.id,
            note=body.get("note"),
            recipient_email=body.get("recipient_email"),
        )
    except osvc.OutreachError as e:
        raise _err(e)
    return {"message": "审批通过", "draft": d}


@router.post("/outreach/drafts/{draft_id}/reject")
def reject_draft_api(
    draft_id: int,
    body: dict = None,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    body = body or {}
    try:
        d = osvc.reject_draft(db, draft_id, reason=body.get("reason", "未说明"), user_id=user.id)
    except osvc.OutreachError as e:
        raise _err(e)
    return {"message": "已拒绝", "draft": d}


@router.post("/outreach/drafts/{draft_id}/cancel")
def cancel_draft_api(draft_id: int, db: Session = Depends(get_db),
                     user=Depends(require_user)):
    try:
        d = osvc.cancel_draft(db, draft_id, user_id=user.id)
    except osvc.OutreachError as e:
        raise _err(e)
    return {"message": "已取消", "draft": d}


@router.post("/outreach/drafts/{draft_id}/send")
def send_draft_api(
    draft_id: int,
    body: dict = None,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """审批通过后入队发送（幂等）。无独立 Worker 时同步兜底执行。

    body: {immediate: bool} — immediate 默认 true（Web 同步兜底）；
          部署独立 Worker 后可传 false 只入队。
    """
    body = body or {}
    immediate = bool(body.get("immediate", True))
    try:
        result = osvc.enqueue_draft_send(db, draft_id, user_id=user.id, immediate=immediate)
    except osvc.OutreachError as e:
        raise _err(e)
    return {"message": "已入队发送任务", **result}


# ═══════════════════════════════════════════════════════════════
# 发送日志
# ═══════════════════════════════════════════════════════════════

@router.get("/outreach/send-logs")
def list_send_logs_api(
    draft_id: Optional[int] = Query(None),
    customer_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None, description="queued/sending/sent/failed/bounced"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    rows, total = osvc.list_send_logs(db, draft_id=draft_id, customer_id=customer_id,
                                      status=status, page=page, page_size=page_size)
    return {"logs": rows, "total": total, "page": page, "page_size": page_size}


@router.get("/outreach/send-logs/{log_id}")
def send_log_detail(log_id: int, db: Session = Depends(get_db),
                    user=Depends(require_user)):
    from app.database import OutreachSendLog
    log = db.query(OutreachSendLog).filter(OutreachSendLog.id == log_id).first()
    if log is None:
        raise HTTPException(status_code=404, detail="发送日志不存在")
    return {"log": osvc.send_log_to_dict(log)}


# ═══════════════════════════════════════════════════════════════
# 全局退订/禁止联系名单（#9）
# ═══════════════════════════════════════════════════════════════

@router.get("/outreach/blacklist")
def list_blacklist_api(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    rows, total = osvc.list_blacklist(db, page=page, page_size=page_size)
    return {"entries": rows, "total": total, "page": page, "page_size": page_size}


@router.post("/outreach/blacklist")
def add_blacklist_api(
    body: dict,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """加入禁止联系名单。

    body: {email?|domain?, reason?（unsubscribe/bounce/complaint/manual）, note?, source?}
    """
    try:
        result = osvc.add_blacklist(
            db,
            email=body.get("email"),
            domain=body.get("domain"),
            reason=body.get("reason", "manual"),
            note=body.get("note"),
            source=body.get("source"),
            created_by_user_id=user.id,
        )
    except osvc.OutreachError as e:
        raise _err(e)
    return result


@router.delete("/outreach/blacklist/{entry_id}")
def remove_blacklist_api(entry_id: int, db: Session = Depends(get_db),
                         user=Depends(require_user)):
    try:
        osvc.remove_blacklist(db, entry_id)
    except osvc.OutreachError as e:
        raise _err(e, 404)
    return {"message": "已移出名单"}


# ═══════════════════════════════════════════════════════════════
# 任务中心（Worker 任务）
# ═══════════════════════════════════════════════════════════════

@router.get("/outreach/tasks")
def list_tasks_api(
    status: Optional[str] = Query(None),
    task_type: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    from app.services import task_service as ts
    rows, total = ts.list_tasks(db, status=status, task_type=task_type,
                                page=page, page_size=page_size)
    return {"tasks": rows, "total": total, "page": page, "page_size": page_size}


@router.post("/outreach/tasks/{task_id}/cancel")
def cancel_task_api(task_id: int, db: Session = Depends(get_db),
                    user=Depends(require_user)):
    from app.services import task_service as ts
    try:
        t = ts.cancel_task(db, task_id)
    except ts.TaskError as e:
        raise _err(e, 409)
    return {"message": "任务已取消", "task": ts.task_to_dict(t)}

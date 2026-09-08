"""
自动化任务服务（Phase1 新增：方案 8）

automation_tasks 的入队/抢占/状态推进。Worker（app/workers/runner.py）
与 Web 进程（同步执行兜底）共用本模块，保证「外部副作用任务只执行一次」：
- 幂等键唯一约束（任务级）
- 原子抢占（UPDATE ... WHERE status=queued AND locked_by IS NULL）
- 可重试（attempts < max_attempts → retry_wait + 指数退避 available_at）
"""
import datetime
import json
import os
import socket
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.database import AutomationTask, AutomationTaskEvent
from app.models.automation import (
    TASK_QUEUED, TASK_RUNNING, TASK_RETRY_WAIT, TASK_SUCCEEDED,
    TASK_FAILED, TASK_CANCELLED, TASK_SEND_EMAIL,
)

WORKER_ID = f"{socket.gethostname() or 'host'}-{uuid.uuid4().hex[:8]}"


class TaskError(Exception):
    pass


def worker_identity() -> str:
    return os.environ.get("WORKER_ID", WORKER_ID)


# ── 入队 ─────────────────────────────────────────────────────────

def enqueue_task(
    db: Session,
    *,
    task_type: str,
    payload: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
    priority: int = 0,
    max_attempts: int = 5,
    available_at: Optional[datetime.datetime] = None,
    created_by_user_id: Optional[int] = None,
) -> AutomationTask:
    """入队任务。幂等语义（外部副作用只执行一次）：

    - 已存在 succeeded 任务 → 原样返回（不重复入队）；
    - 已存在 queued/running/retry_wait 任务 → 原样返回（进行中）；
    - 已存在 failed/cancelled（最终态）任务 → 删除旧行重建（人工可重试）。
    """
    if idempotency_key:
        existing = db.query(AutomationTask).filter(
            AutomationTask.idempotency_key == idempotency_key
        ).first()
        if existing:
            if existing.status in (TASK_QUEUED, TASK_RUNNING, TASK_RETRY_WAIT, TASK_SUCCEEDED):
                return existing
            # 最终失败/取消：允许重新发起 → 删除旧记录重建
            db.delete(existing)
            db.commit()
    task = AutomationTask(
        task_type=task_type,
        status=TASK_QUEUED,
        priority=priority,
        payload_json=json.dumps(payload, ensure_ascii=False) if payload else None,
        idempotency_key=idempotency_key,
        max_attempts=max_attempts,
        available_at=available_at or datetime.datetime.utcnow(),
        created_by_user_id=created_by_user_id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def task_to_dict(t: AutomationTask) -> Dict:
    payload = None
    if t.payload_json:
        try:
            payload = json.loads(t.payload_json)
        except (json.JSONDecodeError, TypeError):
            payload = None
    last_event = None
    if t.last_event:
        try:
            last_event = json.loads(t.last_event)
        except (json.JSONDecodeError, TypeError):
            last_event = t.last_event
    return {
        "id": t.id,
        "task_type": t.task_type,
        "status": t.status,
        "priority": t.priority,
        "payload": payload,
        "idempotency_key": t.idempotency_key,
        "attempts": t.attempts,
        "max_attempts": t.max_attempts,
        "available_at": t.available_at.isoformat() if t.available_at else None,
        "locked_by": t.locked_by,
        "started_at": t.started_at.isoformat() if t.started_at else None,
        "finished_at": t.finished_at.isoformat() if t.finished_at else None,
        "error_code": t.error_code,
        "error_message": t.error_message,
        "last_event": last_event,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


def list_tasks(
    db: Session,
    *,
    status: Optional[str] = None,
    task_type: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> (List[Dict], int):
    q = db.query(AutomationTask)
    if status:
        q = q.filter(AutomationTask.status == status)
    if task_type:
        q = q.filter(AutomationTask.task_type == task_type)
    total = q.count()
    rows = q.order_by(AutomationTask.id.desc()).offset((max(1, page) - 1) * page_size).limit(page_size).all()
    return [task_to_dict(r) for r in rows], total


def get_task(db: Session, task_id: int) -> AutomationTask:
    t = db.query(AutomationTask).filter(AutomationTask.id == task_id).first()
    if t is None:
        raise TaskError("任务不存在")
    return t


def record_task_event(
    db: Session,
    task: AutomationTask,
    event_type: str,
    message: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> AutomationTaskEvent:
    """记录任务事件流水（Phase2：automation_task_events）。"""
    ev = AutomationTaskEvent(
        task_id=task.id,
        event_type=event_type,
        event_message=message,
        payload_json=json.dumps(payload, ensure_ascii=False) if payload else None,
        created_at=datetime.datetime.utcnow(),
    )
    db.add(ev)
    db.flush()
    return ev


def list_task_events(db: Session, task_id: int, limit: int = 50) -> List[Dict]:
    rows = (
        db.query(AutomationTaskEvent)
        .filter(AutomationTaskEvent.task_id == task_id)
        .order_by(AutomationTaskEvent.id.desc())
        .limit(limit)
        .all()
    )
    result = []
    for r in reversed(rows):
        payload = None
        if r.payload_json:
            try:
                payload = json.loads(r.payload_json)
            except (json.JSONDecodeError, TypeError):
                payload = r.payload_json
        result.append({
            "id": r.id,
            "event_type": r.event_type,
            "event_message": r.event_message,
            "payload": payload,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return result


def rerun_task(db: Session, task_id: int, *, created_by_user_id: Optional[int] = None) -> AutomationTask:
    """人工重跑终态任务（Phase2：方案 8.4 失败重试 + 人工重跑）。

    仅 failed / cancelled 任务可重跑（保持外部副作用只执行一次的语义：
    成功/进行中的任务不重复入队）。重跑后 attempts 归零、错误清空、立即可用。
    """
    t = get_task(db, task_id)
    if t.status in (TASK_QUEUED, TASK_RUNNING, TASK_RETRY_WAIT):
        raise TaskError("任务正在进行中，不能重跑")
    if t.status == TASK_SUCCEEDED:
        raise TaskError("任务已成功完成，不能重跑")
    t.status = TASK_QUEUED
    t.attempts = 0
    t.error_code = None
    t.error_message = None
    t.locked_at = None
    t.locked_by = None
    t.finished_at = None
    t.available_at = datetime.datetime.utcnow()
    t.last_event = None
    record_task_event(db, t, "rerun", "人工触发重新执行",
                      payload={"by_user": created_by_user_id, "prior_status": t.status})
    db.commit()
    db.refresh(t)
    return t


def cancel_task(db: Session, task_id: int) -> AutomationTask:
    t = get_task(db, task_id)
    if t.status in (TASK_RUNNING,):
        raise TaskError("任务正在执行中，无法取消")
    if t.status in (TASK_SUCCEEDED, TASK_FAILED, TASK_CANCELLED):
        return t
    t.status = TASK_CANCELLED
    t.finished_at = datetime.datetime.utcnow()
    record_task_event(db, t, "cancelled", "任务被取消")
    db.commit()
    db.refresh(t)
    return t


# ── 抢占与状态推进（Worker 核心） ────────────────────────────────

def claim_next_task(db: Session, worker_id: str, task_types: Optional[List[str]] = None) -> Optional[AutomationTask]:
    """原子抢占一条可执行任务（queued + available_at<=now）。

    用单条 UPDATE ... WHERE 保证并发 Worker 不会抢到同一条
    （PostgreSQL/SQLite 均行级保证；配合 status 条件）。
    """
    now = datetime.datetime.utcnow()
    q = db.query(AutomationTask).filter(
        AutomationTask.status == TASK_QUEUED,
        AutomationTask.available_at <= now,
    )
    if task_types:
        q = q.filter(AutomationTask.task_type.in_(task_types))
    task = q.order_by(AutomationTask.priority.asc(), AutomationTask.id.asc()).first()
    if task is None:
        return None

    # 原子更新（模拟 SELECT FOR UPDATE SKIP LOCKED）
    from sqlalchemy import update
    result = db.execute(
        update(AutomationTask)
        .where(
            AutomationTask.id == task.id,
            AutomationTask.status == TASK_QUEUED,
        )
        .values(
            status=TASK_RUNNING,
            locked_at=now,
            locked_by=worker_id,
            started_at=now,
            attempts=AutomationTask.attempts + 1,
        )
    )
    db.commit()
    if result.rowcount == 0:
        return None  # 被其它 worker 抢先
    db.refresh(task)
    return task


def mark_succeeded(db: Session, task: AutomationTask, event: Optional[Dict] = None) -> AutomationTask:
    task.status = TASK_SUCCEEDED
    task.finished_at = datetime.datetime.utcnow()
    task.locked_at = None
    if event is not None:
        task.last_event = json.dumps(event, ensure_ascii=False)
    record_task_event(db, task, "succeeded",
                      message=str((event or {}).get("message") or "任务执行成功"),
                      payload=event)
    db.commit()
    db.refresh(task)
    return task


def mark_failed(
    db: Session,
    task: AutomationTask,
    *,
    error_code: str,
    error_message: str,
    retryable: bool = True,
    retry_after_seconds: int = 60,
) -> AutomationTask:
    """任务失败推进：可重试 → retry_wait + 退避；否则最终 failed。"""
    if retryable and task.attempts < task.max_attempts:
        task.status = TASK_RETRY_WAIT
        backoff = min(retry_after_seconds * (2 ** max(0, task.attempts - 1)), 3600)
        task.available_at = datetime.datetime.utcnow() + datetime.timedelta(seconds=backoff)
        task.error_code = error_code
        task.error_message = error_message
        task.locked_at = None
        task.last_event = json.dumps(
            {"attempt": task.attempts, "status": "retry_wait",
             "error_code": error_code, "retry_in_s": backoff},
            ensure_ascii=False,
        )
        record_task_event(db, task, "retry_wait",
                          message=f"第 {task.attempts} 次尝试失败，{backoff}s 后重试",
                          payload={"error_code": error_code, "retry_in_s": backoff,
                                   "attempt": task.attempts})
    else:
        task.status = TASK_FAILED
        task.finished_at = datetime.datetime.utcnow()
        task.locked_at = None
        task.error_code = error_code
        task.error_message = error_message
        task.last_event = json.dumps(
            {"attempt": task.attempts, "status": "failed", "error_code": error_code},
            ensure_ascii=False,
        )
        record_task_event(db, task, "failed",
                          message=f"任务最终失败: {error_message[:200]}",
                          payload={"error_code": error_code, "attempt": task.attempts})
    db.commit()
    db.refresh(task)
    return task

"""
Worker 执行器（Phase1 新增：方案 4.2 / 8）

独立进程入口：python -m app.workers.runner
职责：周期抢占 automation_tasks → 分派 task_handlers → 推进状态/重试。

同时暴露 run_task_once(task_id) / process_queued_tasks() 供 Web 进程在
「未单独部署 Worker」的环境同步执行（无 Worker 时也能完成审批后发送）。
"""
import json
import logging
import os
import time

from sqlalchemy.orm import Session

from app.database import get_db, AutomationTask
from app.models.automation import TASK_QUEUED, TASK_RETRY_WAIT
from app.services import task_service
from app.workers import task_handlers

logger = logging.getLogger("worker.runner")

_POLL_SECONDS = float(os.environ.get("WORKER_POLL_SECONDS", "3"))
_MAX_RUN_SECONDS = float(os.environ.get("WORKER_MAX_RUN_SECONDS", "0"))  # 0=无限


def execute_task(db: Session, task: AutomationTask) -> None:
    """执行单个任务并推进状态。异常由调用方（worker 循环）处理分类。"""
    handler = task_handlers.get_handler(task.task_type)
    if handler is None:
        task_service.mark_failed(
            db, task, error_code="no_handler",
            error_message=f"未注册任务处理器: {task.task_type}", retryable=False,
        )
        return
    try:
        event = handler(db, task)
        task_service.mark_succeeded(db, task, event)
        logger.info("任务 %s(%s) 成功", task.id, task.task_type)
    except task_handlers.FatalTaskError as e:
        task_service.mark_failed(
            db, task, error_code=e.code, error_message=str(e), retryable=False)
        logger.warning("任务 %s(%s) 不可重试失败: %s", task.id, task.task_type, e)
    except task_handlers.RetryableTaskError as e:
        task_service.mark_failed(
            db, task, error_code=e.code, error_message=str(e),
            retryable=True, retry_after_seconds=getattr(e, "retry_after", 60))
        logger.warning("任务 %s(%s) 可重试失败: %s", task.id, task.task_type, e)
    except Exception as e:  # noqa: BLE001 - Worker 兜底
        task_service.mark_failed(
            db, task, error_code="unexpected", error_message=str(e)[:500],
            retryable=True, retry_after_seconds=60)
        logger.exception("任务 %s(%s) 异常", task.id, task.task_type)


def process_queued_tasks(db: Session, limit: int = 10) -> int:
    """抢占并执行一批可执行任务，返回处理条数。供 Worker 循环与 Web 同步兜底共用。"""
    worker_id = task_service.worker_identity()
    processed = 0
    for _ in range(limit):
        task = task_service.claim_next_task(
            db, worker_id,
            task_types=list(task_handlers.HANDLERS.keys()),
        )
        if task is None:
            break
        execute_task(db, task)
        processed += 1
    return processed


def worker_loop() -> None:
    """常驻 Worker 主循环。"""
    logger.info("Worker 启动: %s（轮询 %.0fs）", task_service.worker_identity(), _POLL_SECONDS)
    started = time.monotonic()
    idle_count = 0
    while True:
        if _MAX_RUN_SECONDS and (time.monotonic() - started) > _MAX_RUN_SECONDS:
            logger.info("达到 WORKER_MAX_RUN_SECONDS，退出")
            break
        db = next(get_db())
        try:
            n = process_queued_tasks(db, limit=20)
            if n == 0:
                idle_count += 1
                time.sleep(_POLL_SECONDS)
            else:
                idle_count = 0
        except Exception as e:  # noqa: BLE001
            logger.exception("Worker 轮询异常: %s", e)
            time.sleep(_POLL_SECONDS)
        finally:
            db.close()
        # 心跳防呆：长时间空闲也定期执行（无需额外定时器，轮询已足够）


async def inapp_worker_loop() -> None:
    """Web 进程内的轻量 Worker（无独立 worker 进程时的兜底）。

    周期在线程池中执行 process_queued_tasks；任务抢占原子，与独立
    Worker 共存时也不会重复执行同一任务。轮询间隔 WORKER_POLL_SECONDS。
    """
    import asyncio

    logger.info("应用内 Worker 循环启动（轮询 %.0fs）", _POLL_SECONDS)
    while True:
        try:
            await asyncio.to_thread(_process_queued_once)
        except Exception as e:  # noqa: BLE001
            logger.warning("应用内 Worker 轮询异常: %s", e)
        await asyncio.sleep(_POLL_SECONDS)


def _process_queued_once() -> None:
    db = next(get_db())
    try:
        process_queued_tasks(db, limit=20)
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    worker_loop()

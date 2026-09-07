"""自动化任务模型（Phase1 新增：方案 8 自动化任务系统）

automation_tasks — 独立 Worker 消费的持久化任务队列。

任务状态机（方案 8.1）：
    queued → running → succeeded
                     ↘ retry_wait → running
                     ↘ failed
                     ↘ cancelled
"""
import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, Index

from app.core.database import Base

# 任务状态
TASK_QUEUED = "queued"
TASK_RUNNING = "running"
TASK_RETRY_WAIT = "retry_wait"
TASK_SUCCEEDED = "succeeded"
TASK_FAILED = "failed"
TASK_CANCELLED = "cancelled"

# 任务类型（Phase1：发送邮件；后续扩展搜索/同步/分类等）
TASK_SEND_EMAIL = "send_email"


class AutomationTask(Base):
    """自动化任务（Phase1：由 Worker 消费；Web 进程也可同步执行以支持无 Worker 环境）"""
    __tablename__ = "automation_tasks"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    task_type = Column(String(50), nullable=False, index=True, comment="任务类型: send_email/...")
    status = Column(String(20), nullable=False, default=TASK_QUEUED, index=True,
                    comment="queued/running/retry_wait/succeeded/failed/cancelled")
    priority = Column(Integer, nullable=False, default=0, comment="优先级（数字小=先执行）")
    payload_json = Column(Text, nullable=True, comment="任务载荷 JSON（如 draft_id/account_id）")
    idempotency_key = Column(String(255), nullable=True, unique=True, index=True,
                             comment="可选幂等键（防止重复入队同一外部副作用操作）")
    attempts = Column(Integer, nullable=False, default=0, comment="已尝试次数")
    max_attempts = Column(Integer, nullable=False, default=5, comment="最大尝试次数")
    available_at = Column(DateTime, nullable=False, index=True,
                          default=datetime.datetime.utcnow, comment="最早可执行时间（重试退避用）")
    locked_at = Column(DateTime, nullable=True, comment="被 Worker 抢占时间")
    locked_by = Column(String(100), nullable=True, comment="抢占者标识（worker id/host）")
    started_at = Column(DateTime, nullable=True, comment="最近一次开始执行时间")
    finished_at = Column(DateTime, nullable=True, comment="结束时间（成功/最终失败/取消）")
    error_code = Column(String(100), nullable=True)
    error_message = Column(Text, nullable=True)
    # 任务执行事件（Phase2 拆 automation_task_events，Phase1 先内联最近一次事件摘要）
    last_event = Column(Text, nullable=True, comment="最近事件摘要 JSON")
    created_by_user_id = Column(Integer, nullable=True, comment="创建人（users.id，可为后台自动）")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)

    __table_args__ = (
        Index("idx_tasks_pick", "status", "available_at", "priority"),
        Index("idx_tasks_type_status", "task_type", "status"),
    )

"""外联发送模型（Phase1 邮件外联基础新增）

按总体开发方案 Phase1 与 5.4/8 节落地：
- mail_sender_accounts — 发件邮箱账户（Himalaya SMTP，凭据 Fernet 加密，用户级）
- outreach_drafts      — 发信草稿 + 审批状态机（人工确认边界）
- outreach_send_logs   — 发送日志（幂等键/provider message id/发送结果）
- unsubscribe_blacklist — 全局退订与禁止联系名单
"""
import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint

from app.core.database import Base

# 发件账户状态
SENDER_ACCOUNT_ACTIVE = "active"
SENDER_ACCOUNT_ERROR = "error"
SENDER_ACCOUNT_DISABLED = "disabled"

# 草稿状态机（方案 9.2/9.6）
DRAFT_STATUS_DRAFT = "draft"          # 生成后可编辑
DRAFT_STATUS_PENDING = "pending"      # 已提交待人工审批
DRAFT_STATUS_APPROVED = "approved"    # 审批通过，等待发送（或可取消）
DRAFT_STATUS_SENDING = "sending"      # 已进入发送任务
DRAFT_STATUS_SENT = "sent"            # 发送成功
DRAFT_STATUS_REJECTED = "rejected"    # 审批拒绝（可修改后重新提交）
DRAFT_STATUS_FAILED = "failed"        # 发送失败
DRAFT_STATUS_CANCELLED = "cancelled"  # 已取消

# 发送日志状态
SEND_LOG_QUEUED = "queued"
SEND_LOG_SENDING = "sending"
SEND_LOG_SENT = "sent"
SEND_LOG_FAILED = "failed"
SEND_LOG_BOUNCED = "bounced"


class MailSenderAccount(Base):
    """发件邮箱账户（Phase1，Himalaya SMTP 通道）

    与 V5.2 的 MailAccount（Gmail 只读发信检测）不同：本表面向「系统主动发信」，
    用户在设置页填写 SMTP 主机/端口/加密/登录名/密码（Fernet 加密）与可选 IMAP，
    系统为每个账户生成 Himalaya config.toml 后调用 himalaya 发送。
    """
    __tablename__ = "mail_sender_accounts"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True, comment="归属用户（users.id）")
    name = Column(String(100), nullable=True, comment="账户备注名（默认取 email）")
    email_address = Column(String(255), nullable=False, comment="发件地址")
    display_name = Column(String(200), nullable=True, comment="显示名（From 姓名）")
    provider = Column(String(30), nullable=False, default="himalaya", comment="通道: himalaya")

    # SMTP（发送）
    smtp_host = Column(String(255), nullable=False, comment="SMTP 主机")
    smtp_port = Column(Integer, nullable=False, default=587, comment="SMTP 端口")
    smtp_encryption = Column(String(20), nullable=False, default="starttls",
                             comment="starttls/tls/none（none=587 明文升级/465 隐式 TLS）")
    smtp_login = Column(String(255), nullable=True, comment="SMTP 登录名（默认=email_address）")
    smtp_password_encrypted = Column(Text, nullable=True, comment="SMTP 密码（Fernet 加密）")

    # IMAP（收件，Phase2 回复同步预留；Phase1 可为空）
    imap_host = Column(String(255), nullable=True, comment="IMAP 主机（可空）")
    imap_port = Column(Integer, nullable=True, default=993, comment="IMAP 端口")
    imap_encryption = Column(String(20), nullable=True, default="tls", comment="tls/starttls/none")
    imap_login = Column(String(255), nullable=True)
    imap_password_encrypted = Column(Text, nullable=True, comment="IMAP 密码（Fernet 加密，可空=复用 SMTP 密码）")

    # 发送控制（方案 8：每日上限、幂等由 send_logs 支撑）
    daily_limit = Column(Integer, nullable=False, default=30, comment="每日发送上限（0=不限制）")
    enabled = Column(Integer, nullable=False, default=1, comment="是否启用发送: 1/0")
    status = Column(String(20), nullable=False, default=SENDER_ACCOUNT_ACTIVE,
                    comment="active/error/disabled")
    last_test_at = Column(DateTime, nullable=True, comment="最近一次测试连接时间")
    last_test_ok = Column(Integer, nullable=True, comment="最近测试是否成功 1/0")
    last_error = Column(Text, nullable=True, comment="最近错误（测试/发送时回写）")

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow,
                        onupdate=datetime.datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "email_address", name="uq_sender_account_email"),
    )


class OutreachDraft(Base):
    """发信草稿与审批（Phase1）

    状态机：
      draft → pending → approved → (send task) → sending → sent
                    ↘ rejected →（修改）→ pending
      approved → cancelled
      sending/failed →（重试或取消）

    审批通过后创建 automation_tasks(send_email) 由 Worker/Himalaya 发送。
    草稿保存 subject/body 快照，并记录生成溯源（generation_run_id / prompt 版本）。
    """
    __tablename__ = "outreach_drafts"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    sender_account_id = Column(Integer, ForeignKey("mail_sender_accounts.id"),
                               nullable=True, index=True, comment="发送账户（审批时选择）")
    recipient_email = Column(String(255), nullable=True, index=True,
                             comment="收件邮箱（空=发送时取客户主邮箱）")
    recipient_name = Column(String(255), nullable=True, comment="收件人姓名（可空）")

    subject = Column(Text, nullable=False, comment="主题")
    body_text = Column(Text, nullable=False, comment="正文（纯文本，发送时转 MIME）")
    language = Column(String(10), nullable=True, default="en", comment="语言代码")
    tone = Column(String(30), nullable=True, comment="语气（生成记录）")

    # 溯源（方案 9.4：必须可回答用了哪个 Prompt/版本/哪些事实/哪个模型）
    generation_run_id = Column(Integer, ForeignKey("generation_runs.id"), nullable=True, index=True)
    prompt_template_id = Column(Integer, ForeignKey("prompt_templates.id"), nullable=True)
    prompt_version_id = Column(Integer, ForeignKey("prompt_versions.id"), nullable=True)
    model = Column(String(100), nullable=True, comment="生成模型（快照，防模板更换后失联）")
    facts_used_json = Column(Text, nullable=True, comment="生成时使用的客户事实（JSON，溯源展示）")
    risk_flags_json = Column(Text, nullable=True, comment="生成时风险标记（JSON）")

    # 审批状态机
    status = Column(String(20), nullable=False, default=DRAFT_STATUS_DRAFT, index=True,
                    comment="draft/pending/approved/sending/sent/rejected/failed/cancelled")
    human_edited = Column(Integer, nullable=False, default=0, comment="审批前是否被人为修改 1/0")
    review_note = Column(Text, nullable=True, comment="审批备注/拒绝原因")
    reviewed_by_user_id = Column(Integer, nullable=True, comment="审批人（users.id）")
    reviewed_at = Column(DateTime, nullable=True, comment="审批时间")
    approved_at = Column(DateTime, nullable=True, comment="通过时间")

    created_by_user_id = Column(Integer, nullable=True, comment="创建人（users.id）")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow,
                        onupdate=datetime.datetime.utcnow)


class OutreachSendLog(Base):
    """外联发送日志（Phase1：方案「发送日志和 provider message ID」「幂等键」）

    每次发送尝试一条记录。幂等键唯一约束保证：
    - 同一草稿不会被重复发送（审批重试/Worker 重跑安全）；
    - 发送后回填 provider message id / RFC Message-ID / thread id。
    """
    __tablename__ = "outreach_send_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    draft_id = Column(Integer, ForeignKey("outreach_drafts.id"), nullable=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    sender_account_id = Column(Integer, ForeignKey("mail_sender_accounts.id"),
                               nullable=True, index=True)
    # 幂等键：draft_id + recipient_email + subject_hash（方案 8.2）
    idempotency_key = Column(String(255), nullable=False, unique=True, index=True,
                             comment="幂等键，唯一约束防重复发送")

    provider = Column(String(30), nullable=False, default="himalaya", comment="发信通道")
    provider_message_id = Column(String(255), nullable=True, comment="第三方消息 ID（himalaya 输出）")
    internet_message_id = Column(String(255), nullable=True, comment="RFC Message-ID（我方生成）")
    thread_id = Column(String(255), nullable=True, comment="thread/会话引用 ID")

    from_address = Column(String(255), nullable=True)
    to_address = Column(String(255), nullable=False, comment="收件人")
    subject = Column(Text, nullable=True)
    snippet = Column(Text, nullable=True, comment="正文短预览")

    status = Column(String(20), nullable=False, default=SEND_LOG_QUEUED, index=True,
                    comment="queued/sending/sent/failed/bounced")
    attempts = Column(Integer, nullable=False, default=0, comment="已尝试次数")
    error_code = Column(String(50), nullable=True)
    error_message = Column(Text, nullable=True)
    sent_at = Column(DateTime, nullable=True, index=True, comment="实际发送时间")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)


class UnsubscribeBlacklist(Base):
    """全局退订与禁止联系名单（Phase1：方案 9.3 合规）

    发送前必须检查收件人地址与主域名是否命中（unsubscribe / bounce / complaint / manual）。
    """
    __tablename__ = "unsubscribe_blacklist"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    email = Column(String(255), nullable=True, index=True, comment="退订邮箱（小写；email 与 domain 至少一项）")
    domain = Column(String(255), nullable=True, index=True, comment="退订域名（命中全部该域邮箱）")
    reason = Column(String(30), nullable=False, default="manual",
                    comment="unsubscribe/bounce/complaint/manual")
    source = Column(String(50), nullable=True, comment="来源（回信/发送失败/人工添加）")
    source_message_id = Column(String(255), nullable=True, comment="触发退订的原始消息 ID")
    note = Column(Text, nullable=True)
    created_by_user_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        # 同一地址只允许一个原因（email 行唯一）；domain 行唯一性由 db_migrations/alembic 部分唯一索引保证
        UniqueConstraint("email", name="uq_blacklist_email"),
    )

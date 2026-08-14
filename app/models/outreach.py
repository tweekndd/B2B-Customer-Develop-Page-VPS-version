"""外联与邮件模型：MailAccount / CustomerEmailActivity（V5.3 拆分）"""
import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, UniqueConstraint, ForeignKey

from app.core.database import Base


class MailAccount(Base):
    """自有邮箱账户（V5.2 新增，Gmail 发信检测）

    用户把自有邮箱授权给系统后，系统读取其已发送邮件（只读 scope），
    按收件人域名匹配客户并生成发信活动记录。
    """
    __tablename__ = "mail_accounts"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True, comment="系统用户ID（关联 users.id）")
    provider = Column(String(30), default="gmail", comment="邮箱平台: gmail")
    email_address = Column(String(255), nullable=False, comment="已授权邮箱地址")
    provider_user_id = Column(String(255), nullable=True, comment="Google subject（sub）")
    access_token_encrypted = Column(Text, nullable=True, comment="access token（Fernet 加密存储）")
    refresh_token_encrypted = Column(Text, nullable=True, comment="refresh token（Fernet 加密存储）")
    token_expires_at = Column(DateTime, nullable=True, comment="access token 过期时间")
    scopes = Column(Text, nullable=True, comment="OAuth scope JSON")
    sync_cursor = Column(String(255), nullable=True, comment="Gmail historyId 或 delta 游标")
    subscription_id = Column(String(255), nullable=True, comment="订阅/推送 ID，可为空")
    watch_expiration_at = Column(DateTime, nullable=True, comment="Gmail watch 到期时间")
    last_synced_at = Column(DateTime, nullable=True, comment="最近同步时间")
    status = Column(String(30), default="active", comment="状态: active/reauth_required/error/disabled")
    last_error = Column(Text, nullable=True, comment="最近错误信息")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, comment="更新时间")


class CustomerEmailActivity(Base):
    """客户发信记录（V5.2 新增）

    匹配规则：收件人域名与客户 website 主域名一致（严格主域匹配），
    或收件人在客户 customer_emails 表中（manual_email）。
    """
    __tablename__ = "customer_email_activities"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True, comment="匹配到的客户ID")
    mail_account_id = Column(Integer, nullable=True, index=True, comment="哪个自有邮箱检测到")
    provider = Column(String(30), default="gmail", comment="邮箱平台")
    provider_message_id = Column(String(255), nullable=False, comment="第三方消息 ID")
    internet_message_id = Column(String(255), nullable=True, comment="RFC Message-ID")
    thread_id = Column(String(255), nullable=True, comment="Gmail threadId")
    from_address = Column(String(255), nullable=True, comment="发件人")
    to_addresses_json = Column(Text, nullable=True, comment="To 收件人列表 JSON")
    cc_addresses_json = Column(Text, nullable=True, comment="CC 列表 JSON")
    subject = Column(Text, nullable=True, comment="邮件标题")
    sent_at = Column(DateTime, nullable=True, index=True, comment="发出时间（UTC）")
    matched_domain = Column(String(255), nullable=True, comment="用于匹配的收件人域名")
    match_type = Column(String(30), default="exact_domain", comment="exact_domain/manual_email")
    snippet = Column(Text, nullable=True, comment="短预览（默认不保存正文）")
    raw_metadata_json = Column(Text, nullable=True, comment="必要元数据 JSON")
    is_ignored = Column(Integer, default=0, comment="用户忽略误匹配: 1是/0否")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, comment="入库时间")

    __table_args__ = (
        UniqueConstraint("mail_account_id", "provider", "provider_message_id",
                         "matched_domain", name="uq_mail_activity"),
    )

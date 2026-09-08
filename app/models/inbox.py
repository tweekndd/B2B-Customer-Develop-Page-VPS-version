"""回复收件箱模型（Phase2 回复同步与 AI 分类）

按《AI 客户开发与邮件外联系统总体开发方案》5.4「邮件数据模型」落地：
  mail_messages     — 邮件元数据 + 正文外置（body_text_object_id / raw_mime_object_id）
  mail_attachments  — 附件元数据 + 对象外置（object_id / sha256）
  customer_todos    — 回复动作待办（Phase2 交付「创建待办」，RFQ 落库留 Phase3）

外置对象统一走 app.services.object_storage（storage_objects 索引）。
列表只读元数据，详情按需经对象存储加载正文/附件（方案 10.2 大字段按需加载）。
"""
import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, Float, UniqueConstraint, ForeignKey

from app.core.database import Base


class MailMessage(Base):
    """邮件消息（Phase2：收件箱同步落库，正文/原文外置到对象存储）

    direction: in（收到回复）/ out（已发出，Phase2 以 in 为主）
    provider: imap（Himalaya 收发同箱）/ gmail
    provider_message_id: 邮件 Message-ID 头（回信去重键）；无 Message-ID 时回退 IMAP UID
    thread_id: 线程标识（References[0] / In-Reply-To[0]，用于把回复归组到原开发信线程）
    classification: 回复意图（方案 9.1）：rfq/interest/question/not_interested/
                    unsubscribe/out_of_office/bounce/complaint/other
    """
    __tablename__ = "mail_messages"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    provider = Column(String(30), nullable=False, default="imap", comment="通道: imap/gmail")
    provider_message_id = Column(String(255), nullable=False, comment="Message-ID 头（或回退 UID）")
    thread_id = Column(String(255), nullable=True, comment="线程标识（Reply/References 归组）")
    mail_account_id = Column(Integer, nullable=True, index=True, comment="关联发件账户 mail_sender_accounts.id")
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True, comment="匹配到的客户ID（可为空=未匹配）")
    direction = Column(String(10), nullable=False, default="in", comment="in/out")
    from_address = Column(String(255), nullable=True, comment="发件人")
    to_addresses_json = Column(Text, nullable=True, comment="To 收件人 JSON")
    subject = Column(Text, nullable=True, comment="主题")
    sent_at = Column(DateTime, nullable=True, index=True, comment="发件时间（UTC）")
    received_at = Column(DateTime, nullable=True, index=True, comment="接收时间（UTC）")
    snippet = Column(Text, nullable=True, comment="短预览（默认不加载正文）")
    # 大字段外置（方案 5.3/L3 对象存储）
    body_text_object_id = Column(String(128), nullable=True, comment="正文纯文本对象键（storage_objects）")
    raw_mime_object_id = Column(String(128), nullable=True, comment="原始 MIME 对象键（storage_objects）")
    # 回复分类（方案 9.1 结构化输出）
    classification = Column(String(50), nullable=True, index=True, comment="回复意图分类")
    classification_confidence = Column(Float, nullable=True, comment="分类置信度 0-1")
    classification_json = Column(Text, nullable=True, comment="分类完整 JSON（产品/数量/缺失字段等）")
    matched_domain = Column(String(255), nullable=True, comment="匹配客户用的域名")
    is_handled = Column(Integer, default=0, comment="是否已查看处理: 1/0")
    is_ignored = Column(Integer, default=0, comment="用户忽略（误匹配）: 1/0")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True, comment="入库时间")

    __table_args__ = (
        UniqueConstraint("provider", "mail_account_id", "provider_message_id",
                         name="uq_mail_message"),
    )


class MailAttachment(Base):
    """邮件附件（Phase2：元数据入库，文件外置到对象存储）"""
    __tablename__ = "mail_attachments"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    message_id = Column(Integer, ForeignKey("mail_messages.id"), nullable=False, index=True, comment="关联邮件")
    filename = Column(String(255), nullable=True, comment="原始文件名")
    content_type = Column(String(128), nullable=True, comment="MIME 类型")
    size_bytes = Column(Integer, nullable=False, default=0, comment="字节大小")
    object_id = Column(String(128), nullable=True, comment="文件对象键（storage_objects）")
    sha256 = Column(String(64), nullable=True, comment="文件 sha256（校验/去重）")
    extracted_text_object_id = Column(String(128), nullable=True, comment="附件解析文本对象键（可选）")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, comment="入库时间")


# 客户跟进状态（与 crm.Customer.status 注释一致）
STATUS_待联系 = "待联系"
STATUS_已发邮件 = "已发邮件"
STATUS_已回复 = "已回复"
STATUS_无效线索 = "无效线索"
STATUS_成单 = "成单"

# 状态机拓扑（Phase2 方案 7.2）：人工可任意流转；自动流转严格按「只升不降」。
# before → after 定义自动升级路径；无效线索是终态（auto 不进入）。
STATUS_AUTO_UPGRADE = {
    STATUS_待联系: {STATUS_已发邮件, STATUS_已回复},
    STATUS_已发邮件: {STATUS_已回复},
    STATUS_已回复: {STATUS_成单},
    STATUS_成单: set(),
    STATUS_无效线索: set(),
}


class CustomerStatusHistory(Base):
    """客户状态变更审计（Phase2：客户状态机留痕）"""
    __tablename__ = "customer_status_history"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    old_status = Column(String(20), nullable=True, comment="变更前状态（None=首次）")
    new_status = Column(String(20), nullable=False, comment="变更后状态")
    trigger = Column(String(30), nullable=False, default="manual",
                     comment="触发源: mail_send/mail_reply/bounce/unsubscribe/manual")
    source_message_id = Column(Integer, nullable=True, comment="触发邮件 mail_messages.id")
    source_task_id = Column(Integer, nullable=True, comment="触发任务 automation_tasks.id")
    note = Column(Text, nullable=True, comment="备注（如退信原因/分类）")
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, comment="人工操作人")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)


# 待办类型（方案 13.2 回复中心：回复意图 → 创建动作待办）
TODO_RESPOND = "respond"           # 需人工回复该线索
TODO_FOLLOW_UP = "follow_up"       # 一般跟进
TODO_QUOTE_REQUEST = "quote_request"   # 询价/索样（RFQ 雏形，Phase3 落库 RFQ）
TODO_COMPLAINT = "complaint"       # 投诉
TODO_OTHER = "other"

# 待办状态
TODO_OPEN = "open"
TODO_DONE = "done"
TODO_DISMISSED = "dismissed"


class CustomerTodo(Base):
    """回复动作待办（Phase2：分类后创建，供回复中心人工处理）"""
    __tablename__ = "customer_todos"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    message_id = Column(Integer, ForeignKey("mail_messages.id"), nullable=True, index=True,
                        comment="来源回复邮件")
    todo_type = Column(String(30), nullable=False, default=TODO_RESPOND, index=True)
    summary = Column(Text, nullable=True, comment="AI 事件摘要/下一步建议")
    details_json = Column(Text, nullable=True, comment="分类细节 JSON（产品/数量/目的地等）")
    priority = Column(Integer, nullable=False, default=2, comment="优先级 1高/2中/3低")
    status = Column(String(20), nullable=False, default=TODO_OPEN, index=True)
    assignee_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    done_at = Column(DateTime, nullable=True)
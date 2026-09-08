"""Phase2 inbox schema - 回复收件箱/附件/客户状态历史/动作待办/任务事件

Revision ID: 003_phase2_inbox
Revises: 002_phase1_outreach
Create Date: 2026-09-08

按《AI 客户开发与邮件外联系统总体开发方案》Phase2 新增 5 张表：
  mail_messages / mail_attachments（方案 5.4 邮件数据模型，正文/附件外置对象存储）
  customer_status_history（方案 7.2 客户状态机留痕）
  customer_todos（回复动作待办，Phase3 RFQ 再落库）
  automation_task_events（从 automation_tasks.last_event 拆出的事件流水）

并给 mail_sender_accounts 增加 IMAP 增量同步游标三列。

与 app/models/{inbox,automation}.py 及 outreach_send.MailSenderAccount 保持一致。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision: str = '003_phase2_inbox'
down_revision: Union[str, None] = '002_phase1_outreach'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── mail_messages（回复收件箱） ──────────────────────────────────
    op.create_table('mail_messages',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('provider', sa.String(length=30), nullable=False),
        sa.Column('provider_message_id', sa.String(length=255), nullable=False),
        sa.Column('thread_id', sa.String(length=255), nullable=True),
        sa.Column('mail_account_id', sa.Integer(), nullable=True),
        sa.Column('customer_id', sa.Integer(), nullable=True),
        sa.Column('direction', sa.String(length=10), nullable=False),
        sa.Column('from_address', sa.String(length=255), nullable=True),
        sa.Column('to_addresses_json', sa.Text(), nullable=True),
        sa.Column('subject', sa.Text(), nullable=True),
        sa.Column('sent_at', sa.DateTime(), nullable=True),
        sa.Column('received_at', sa.DateTime(), nullable=True),
        sa.Column('snippet', sa.Text(), nullable=True),
        sa.Column('body_text_object_id', sa.String(length=128), nullable=True),
        sa.Column('raw_mime_object_id', sa.String(length=128), nullable=True),
        sa.Column('classification', sa.String(length=50), nullable=True),
        sa.Column('classification_confidence', sa.Float(), nullable=True),
        sa.Column('classification_json', sa.Text(), nullable=True),
        sa.Column('matched_domain', sa.String(length=255), nullable=True),
        sa.Column('is_handled', sa.Integer(), nullable=True),
        sa.Column('is_ignored', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider', 'mail_account_id', 'provider_message_id',
                            name='uq_mail_message'),
    )
    with op.batch_alter_table('mail_messages', schema=None) as batch_op:
        batch_op.create_index('ix_mail_messages_id', ['id'], unique=False)
        batch_op.create_index('ix_mail_messages_customer_id', ['customer_id'], unique=False)
        batch_op.create_index('ix_mail_messages_mail_account_id', ['mail_account_id'], unique=False)
        batch_op.create_index('ix_mail_messages_classification', ['classification'], unique=False)
        batch_op.create_index('ix_mail_messages_created_at', ['created_at'], unique=False)
        batch_op.create_index('ix_mail_messages_sent_at', ['sent_at'], unique=False)
        batch_op.create_index('ix_mail_messages_received_at', ['received_at'], unique=False)

    # ── mail_attachments（附件） ─────────────────────────────────────
    op.create_table('mail_attachments',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('message_id', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=True),
        sa.Column('content_type', sa.String(length=128), nullable=True),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('object_id', sa.String(length=128), nullable=True),
        sa.Column('sha256', sa.String(length=64), nullable=True),
        sa.Column('extracted_text_object_id', sa.String(length=128), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['message_id'], ['mail_messages.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('mail_attachments', schema=None) as batch_op:
        batch_op.create_index('ix_mail_attachments_id', ['id'], unique=False)
        batch_op.create_index('ix_mail_attachments_message_id', ['message_id'], unique=False)

    # ── customer_status_history（状态机留痕） ────────────────────────
    op.create_table('customer_status_history',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('old_status', sa.String(length=20), nullable=True),
        sa.Column('new_status', sa.String(length=20), nullable=False),
        sa.Column('trigger', sa.String(length=30), nullable=False),
        sa.Column('source_message_id', sa.Integer(), nullable=True),
        sa.Column('source_task_id', sa.Integer(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id']),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('customer_status_history', schema=None) as batch_op:
        batch_op.create_index('ix_customer_status_history_id', ['id'], unique=False)
        batch_op.create_index('ix_customer_status_history_customer_id', ['customer_id'], unique=False)
        batch_op.create_index('ix_customer_status_history_created_at', ['created_at'], unique=False)

    # ── customer_todos（回复动作待办） ───────────────────────────────
    op.create_table('customer_todos',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('message_id', sa.Integer(), nullable=True),
        sa.Column('todo_type', sa.String(length=30), nullable=False),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('details_json', sa.Text(), nullable=True),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('assignee_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('done_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['assignee_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id']),
        sa.ForeignKeyConstraint(['message_id'], ['mail_messages.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('customer_todos', schema=None) as batch_op:
        batch_op.create_index('ix_customer_todos_id', ['id'], unique=False)
        batch_op.create_index('ix_customer_todos_customer_id', ['customer_id'], unique=False)
        batch_op.create_index('ix_customer_todos_message_id', ['message_id'], unique=False)
        batch_op.create_index('ix_customer_todos_todo_type', ['todo_type'], unique=False)
        batch_op.create_index('ix_customer_todos_status', ['status'], unique=False)
        batch_op.create_index('ix_customer_todos_created_at', ['created_at'], unique=False)

    # ── automation_task_events（任务事件流水） ───────────────────────
    op.create_table('automation_task_events',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('event_type', sa.String(length=30), nullable=False),
        sa.Column('event_message', sa.Text(), nullable=True),
        sa.Column('payload_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('automation_task_events', schema=None) as batch_op:
        batch_op.create_index('ix_automation_task_events_id', ['id'], unique=False)
        batch_op.create_index('ix_automation_task_events_task_id', ['task_id'], unique=False)
        batch_op.create_index('ix_automation_task_events_event_type', ['event_type'], unique=False)
        batch_op.create_index('ix_automation_task_events_created_at', ['created_at'], unique=False)

    # ── mail_sender_accounts 增加 IMAP 增量同步游标 ──────────────────
    with op.batch_alter_table('mail_sender_accounts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('last_inbox_sync_at', sa.DateTime(), nullable=True,
                                      comment='最近一次收件同步时间'))
        batch_op.add_column(sa.Column('last_inbox_uid_validity', sa.String(length=40), nullable=True,
                                      comment='最近一次同步的 UIDVALIDITY'))
        batch_op.add_column(sa.Column('last_inbox_uid', sa.String(length=20), nullable=True,
                                      comment='最近一次同步到的最大 UID'))


def downgrade() -> None:
    with op.batch_alter_table('mail_sender_accounts', schema=None) as batch_op:
        batch_op.drop_column('last_inbox_uid')
        batch_op.drop_column('last_inbox_uid_validity')
        batch_op.drop_column('last_inbox_sync_at')

    op.drop_table('automation_task_events')
    op.drop_table('customer_todos')
    op.drop_table('customer_status_history')
    op.drop_table('mail_attachments')
    op.drop_table('mail_messages')
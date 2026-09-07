"""Phase1 email outreach schema - Prompt 模板/生成溯源/外联草稿/发送账户与日志/自动化任务/退订名单

Revision ID: 002_phase1_outreach
Revises: 001_initial
Create Date: 2026-09-07

按《AI 客户开发与邮件外联系统总体开发方案》Phase1 新增 7 张表：
  prompt_templates / prompt_versions / generation_runs（方案 9.4 Prompt 管理）
  mail_sender_accounts / outreach_drafts / outreach_send_logs（Phase1 发信闭环）
  unsubscribe_blacklist（9.3 全局退订）
  automation_tasks（方案 8 Worker 任务表）

与 app/models/{prompts,outreach_send,automation}.py 保持一致。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision: str = '002_phase1_outreach'
down_revision: Union[str, None] = '001_initial'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── prompt_templates ─────────────────────────────────────────────
    op.create_table('prompt_templates',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False, comment='Prompt 名称'),
        sa.Column('purpose', sa.String(length=50), nullable=False),
        sa.Column('language', sa.String(length=20), nullable=False),
        sa.Column('customer_segment', sa.String(length=200), nullable=True),
        sa.Column('product_name', sa.String(length=200), nullable=True),
        sa.Column('sender_company', sa.String(length=200), nullable=True),
        sa.Column('sender_signature', sa.Text(), nullable=True),
        sa.Column('system_prompt', sa.Text(), nullable=False),
        sa.Column('user_prompt_template', sa.Text(), nullable=False),
        sa.Column('variables_schema_json', sa.Text(), nullable=True),
        sa.Column('model_config_json', sa.Text(), nullable=True),
        sa.Column('freedom_level', sa.String(length=4), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('current_version', sa.Integer(), nullable=False),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_prompt_template_name'),
    )
    with op.batch_alter_table('prompt_templates', schema=None) as batch_op:
        batch_op.create_index('ix_prompt_templates_id', ['id'], unique=False)
        batch_op.create_index('ix_prompt_templates_name', ['name'], unique=True)

    # ── prompt_versions ──────────────────────────────────────────────
    op.create_table('prompt_versions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('template_id', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('system_prompt', sa.Text(), nullable=False),
        sa.Column('user_prompt_template', sa.Text(), nullable=False),
        sa.Column('variables_schema_json', sa.Text(), nullable=True),
        sa.Column('model_config_json', sa.Text(), nullable=True),
        sa.Column('freedom_level', sa.String(length=4), nullable=False),
        sa.Column('change_summary', sa.Text(), nullable=True),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['template_id'], ['prompt_templates.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('template_id', 'version', name='uq_prompt_version'),
    )
    with op.batch_alter_table('prompt_versions', schema=None) as batch_op:
        batch_op.create_index('ix_prompt_versions_id', ['id'], unique=False)
        batch_op.create_index('ix_prompt_versions_template_id', ['template_id'], unique=False)

    # ── generation_runs ──────────────────────────────────────────────
    op.create_table('generation_runs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=True),
        sa.Column('prompt_template_id', sa.Integer(), nullable=True),
        sa.Column('prompt_version_id', sa.Integer(), nullable=True),
        sa.Column('freedom_level', sa.String(length=4), nullable=False),
        sa.Column('provider', sa.String(length=50), nullable=True),
        sa.Column('model', sa.String(length=100), nullable=True),
        sa.Column('model_config_json', sa.Text(), nullable=True),
        sa.Column('input_snapshot_json', sa.Text(), nullable=True),
        sa.Column('rendered_prompt_hash', sa.String(length=64), nullable=True),
        sa.Column('raw_output_text', sa.Text(), nullable=True),
        sa.Column('output_json', sa.Text(), nullable=True),
        sa.Column('checks_json', sa.Text(), nullable=True),
        sa.Column('risk_flags_json', sa.Text(), nullable=True),
        sa.Column('result_status', sa.String(length=20), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('human_edit_distance', sa.Integer(), nullable=True),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id']),
        sa.ForeignKeyConstraint(['prompt_template_id'], ['prompt_templates.id']),
        sa.ForeignKeyConstraint(['prompt_version_id'], ['prompt_versions.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('generation_runs', schema=None) as batch_op:
        batch_op.create_index('ix_generation_runs_id', ['id'], unique=False)
        batch_op.create_index('ix_generation_runs_customer_id', ['customer_id'], unique=False)
        batch_op.create_index('ix_generation_runs_prompt_template_id', ['prompt_template_id'], unique=False)
        batch_op.create_index('ix_generation_runs_prompt_version_id', ['prompt_version_id'], unique=False)
        batch_op.create_index('ix_generation_runs_created_at', ['created_at'], unique=False)

    # ── mail_sender_accounts（发件账户） ─────────────────────────────
    op.create_table('mail_sender_accounts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=True),
        sa.Column('email_address', sa.String(length=255), nullable=False),
        sa.Column('display_name', sa.String(length=200), nullable=True),
        sa.Column('provider', sa.String(length=30), nullable=False),
        sa.Column('smtp_host', sa.String(length=255), nullable=False),
        sa.Column('smtp_port', sa.Integer(), nullable=False),
        sa.Column('smtp_encryption', sa.String(length=20), nullable=False),
        sa.Column('smtp_login', sa.String(length=255), nullable=True),
        sa.Column('smtp_password_encrypted', sa.Text(), nullable=True),
        sa.Column('imap_host', sa.String(length=255), nullable=True),
        sa.Column('imap_port', sa.Integer(), nullable=True),
        sa.Column('imap_encryption', sa.String(length=20), nullable=True),
        sa.Column('imap_login', sa.String(length=255), nullable=True),
        sa.Column('imap_password_encrypted', sa.Text(), nullable=True),
        sa.Column('daily_limit', sa.Integer(), nullable=False),
        sa.Column('enabled', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('last_test_at', sa.DateTime(), nullable=True),
        sa.Column('last_test_ok', sa.Integer(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'email_address', name='uq_sender_account_email'),
    )
    with op.batch_alter_table('mail_sender_accounts', schema=None) as batch_op:
        batch_op.create_index('ix_mail_sender_accounts_id', ['id'], unique=False)
        batch_op.create_index('ix_mail_sender_accounts_user_id', ['user_id'], unique=False)

    # ── outreach_drafts（草稿 + 审批） ───────────────────────────────
    op.create_table('outreach_drafts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('sender_account_id', sa.Integer(), nullable=True),
        sa.Column('recipient_email', sa.String(length=255), nullable=True),
        sa.Column('recipient_name', sa.String(length=255), nullable=True),
        sa.Column('subject', sa.Text(), nullable=False),
        sa.Column('body_text', sa.Text(), nullable=False),
        sa.Column('language', sa.String(length=10), nullable=True),
        sa.Column('tone', sa.String(length=30), nullable=True),
        sa.Column('generation_run_id', sa.Integer(), nullable=True),
        sa.Column('prompt_template_id', sa.Integer(), nullable=True),
        sa.Column('prompt_version_id', sa.Integer(), nullable=True),
        sa.Column('model', sa.String(length=100), nullable=True),
        sa.Column('facts_used_json', sa.Text(), nullable=True),
        sa.Column('risk_flags_json', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('human_edited', sa.Integer(), nullable=False),
        sa.Column('review_note', sa.Text(), nullable=True),
        sa.Column('reviewed_by_user_id', sa.Integer(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id']),
        sa.ForeignKeyConstraint(['generation_run_id'], ['generation_runs.id']),
        sa.ForeignKeyConstraint(['prompt_template_id'], ['prompt_templates.id']),
        sa.ForeignKeyConstraint(['prompt_version_id'], ['prompt_versions.id']),
        sa.ForeignKeyConstraint(['sender_account_id'], ['mail_sender_accounts.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('outreach_drafts', schema=None) as batch_op:
        batch_op.create_index('ix_outreach_drafts_id', ['id'], unique=False)
        batch_op.create_index('ix_outreach_drafts_customer_id', ['customer_id'], unique=False)
        batch_op.create_index('ix_outreach_drafts_sender_account_id', ['sender_account_id'], unique=False)
        batch_op.create_index('ix_outreach_drafts_recipient_email', ['recipient_email'], unique=False)
        batch_op.create_index('ix_outreach_drafts_generation_run_id', ['generation_run_id'], unique=False)
        batch_op.create_index('ix_outreach_drafts_status', ['status'], unique=False)
        batch_op.create_index('ix_outreach_drafts_created_at', ['created_at'], unique=False)

    # ── outreach_send_logs（发送日志 + 幂等） ────────────────────────
    op.create_table('outreach_send_logs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('draft_id', sa.Integer(), nullable=True),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('sender_account_id', sa.Integer(), nullable=True),
        sa.Column('idempotency_key', sa.String(length=255), nullable=False),
        sa.Column('provider', sa.String(length=30), nullable=False),
        sa.Column('provider_message_id', sa.String(length=255), nullable=True),
        sa.Column('internet_message_id', sa.String(length=255), nullable=True),
        sa.Column('thread_id', sa.String(length=255), nullable=True),
        sa.Column('from_address', sa.String(length=255), nullable=True),
        sa.Column('to_address', sa.String(length=255), nullable=False),
        sa.Column('subject', sa.Text(), nullable=True),
        sa.Column('snippet', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('error_code', sa.String(length=50), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('sent_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id']),
        sa.ForeignKeyConstraint(['draft_id'], ['outreach_drafts.id']),
        sa.ForeignKeyConstraint(['sender_account_id'], ['mail_sender_accounts.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key', name='uq_send_log_idempotency'),
    )
    with op.batch_alter_table('outreach_send_logs', schema=None) as batch_op:
        batch_op.create_index('ix_outreach_send_logs_id', ['id'], unique=False)
        batch_op.create_index('ix_outreach_send_logs_draft_id', ['draft_id'], unique=False)
        batch_op.create_index('ix_outreach_send_logs_customer_id', ['customer_id'], unique=False)
        batch_op.create_index('ix_outreach_send_logs_sender_account_id', ['sender_account_id'], unique=False)
        batch_op.create_index('ix_outreach_send_logs_idempotency_key', ['idempotency_key'], unique=True)
        batch_op.create_index('ix_outreach_send_logs_status', ['status'], unique=False)
        batch_op.create_index('ix_outreach_send_logs_sent_at', ['sent_at'], unique=False)
        batch_op.create_index('ix_outreach_send_logs_created_at', ['created_at'], unique=False)

    # ── unsubscribe_blacklist（全局退订名单） ────────────────────────
    op.create_table('unsubscribe_blacklist',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('domain', sa.String(length=255), nullable=True),
        sa.Column('reason', sa.String(length=30), nullable=False),
        sa.Column('source', sa.String(length=50), nullable=True),
        sa.Column('source_message_id', sa.String(length=255), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email', name='uq_blacklist_email'),
    )
    with op.batch_alter_table('unsubscribe_blacklist', schema=None) as batch_op:
        batch_op.create_index('ix_unsubscribe_blacklist_id', ['id'], unique=False)
        batch_op.create_index('ix_unsubscribe_blacklist_email', ['email'], unique=False)
        batch_op.create_index('ix_unsubscribe_blacklist_domain', ['domain'], unique=False)

    # ── automation_tasks（Worker 任务表） ────────────────────────────
    op.create_table('automation_tasks',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('task_type', sa.String(length=50), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.Column('payload_json', sa.Text(), nullable=True),
        sa.Column('idempotency_key', sa.String(length=255), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('max_attempts', sa.Integer(), nullable=False),
        sa.Column('available_at', sa.DateTime(), nullable=False),
        sa.Column('locked_at', sa.DateTime(), nullable=True),
        sa.Column('locked_by', sa.String(length=100), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('error_code', sa.String(length=100), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('last_event', sa.Text(), nullable=True),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key', name='uq_automation_task_idempotency'),
    )
    with op.batch_alter_table('automation_tasks', schema=None) as batch_op:
        batch_op.create_index('ix_automation_tasks_id', ['id'], unique=False)
        batch_op.create_index('ix_automation_tasks_task_type', ['task_type'], unique=False)
        batch_op.create_index('ix_automation_tasks_status', ['status'], unique=False)
        batch_op.create_index('ix_automation_tasks_idempotency_key', ['idempotency_key'], unique=True)
        batch_op.create_index('ix_automation_tasks_available_at', ['available_at'], unique=False)
        batch_op.create_index('ix_automation_tasks_created_at', ['created_at'], unique=False)
        batch_op.create_index('idx_tasks_pick', ['status', 'available_at', 'priority'], unique=False)
        batch_op.create_index('idx_tasks_type_status', ['task_type', 'status'], unique=False)


def downgrade() -> None:
    op.drop_table('automation_tasks')
    op.drop_table('unsubscribe_blacklist')
    op.drop_table('outreach_send_logs')
    op.drop_table('outreach_drafts')
    op.drop_table('mail_sender_accounts')
    op.drop_table('generation_runs')
    op.drop_table('prompt_versions')
    op.drop_table('prompt_templates')

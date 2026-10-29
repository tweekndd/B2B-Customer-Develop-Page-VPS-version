"""Robotics Lead Qualification schema

Revision ID: 004_robotics_lead
Revises: 003_phase2_inbox
Create Date: 2026-09-23

将系统扩展为 Robotics / Automation / AMR / AGV 行业客户挖掘与 AI Lead Qualification，
为 customers / analysis_runs / score_snapshots 增加 7 维评分与结构化输出字段：

  customers:
    industry, customer_type,
    product_fit_score, application_fit_score, customer_type_score,
    commercial_probability_score, technical_fit_score, geographic_score, credibility_score,
    lead_grade, recommended_products, likely_use_cases, evidence, sales_angle,
    negative_flags, confidence, recommended_action
  analysis_runs:
    customer_type, industry, total_score, lead_grade, recommended_action,
    7 维分数, recommended_products, likely_use_cases, evidence, sales_angle,
    negative_flags, confidence
  score_snapshots:
    7 维分数, lead_grade, recommended_action, negative_flags

向后兼容：全部为 ADD COLUMN，旧数据保持不变。与 app/models/{crm,intelligence}.py 一致。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision: str = '004_robotics_lead'
down_revision: Union[str, None] = '003_phase2_inbox'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CUSTOMER_COLUMNS = [
    ("industry", sa.String(length=100)),
    ("customer_type", sa.String(length=100)),
    ("product_fit_score", sa.Integer()),
    ("application_fit_score", sa.Integer()),
    ("customer_type_score", sa.Integer()),
    ("commercial_probability_score", sa.Integer()),
    ("technical_fit_score", sa.Integer()),
    ("geographic_score", sa.Integer()),
    ("credibility_score", sa.Integer()),
    ("lead_grade", sa.String(length=10)),
    ("recommended_products", sa.Text()),
    ("likely_use_cases", sa.Text()),
    ("evidence", sa.Text()),
    ("sales_angle", sa.Text()),
    ("negative_flags", sa.Text()),
    ("confidence", sa.Float()),
    ("recommended_action", sa.String(length=30)),
]

_ANALYSIS_RUN_COLUMNS = [
    ("customer_type", sa.String(length=100)),
    ("industry", sa.String(length=100)),
    ("total_score", sa.Integer()),
    ("lead_grade", sa.String(length=10)),
    ("recommended_action", sa.String(length=30)),
    ("product_fit_score", sa.Integer()),
    ("application_fit_score", sa.Integer()),
    ("customer_type_score", sa.Integer()),
    ("commercial_probability_score", sa.Integer()),
    ("technical_fit_score", sa.Integer()),
    ("geographic_score", sa.Integer()),
    ("credibility_score", sa.Integer()),
    ("recommended_products", sa.Text()),
    ("likely_use_cases", sa.Text()),
    ("evidence", sa.Text()),
    ("sales_angle", sa.Text()),
    ("negative_flags", sa.Text()),
    ("confidence", sa.Float()),
]

_SCORE_SNAPSHOT_COLUMNS = [
    ("product_fit_score", sa.Integer()),
    ("application_fit_score", sa.Integer()),
    ("customer_type_score", sa.Integer()),
    ("commercial_probability_score", sa.Integer()),
    ("technical_fit_score", sa.Integer()),
    ("geographic_score", sa.Integer()),
    ("credibility_score", sa.Integer()),
    ("lead_grade", sa.String(length=10)),
    ("recommended_action", sa.String(length=30)),
    ("negative_flags", sa.Text()),
]


def _add_columns(table: str, columns) -> None:
    with op.batch_alter_table(table, schema=None) as batch_op:
        for name, coltype in columns:
            batch_op.add_column(sa.Column(name, coltype, nullable=True))


def upgrade() -> None:
    _add_columns("customers", _CUSTOMER_COLUMNS)
    _add_columns("analysis_runs", _ANALYSIS_RUN_COLUMNS)
    _add_columns("score_snapshots", _SCORE_SNAPSHOT_COLUMNS)

    with op.batch_alter_table("customers", schema=None) as batch_op:
        batch_op.create_index("idx_customers_lead_grade", ["lead_grade"], unique=False)
        batch_op.create_index("idx_customers_customer_type", ["customer_type"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("customers", schema=None) as batch_op:
        batch_op.drop_index("idx_customers_customer_type")
        batch_op.drop_index("idx_customers_lead_grade")

    for table, columns in (
        ("score_snapshots", _SCORE_SNAPSHOT_COLUMNS),
        ("analysis_runs", _ANALYSIS_RUN_COLUMNS),
        ("customers", _CUSTOMER_COLUMNS),
    ):
        with op.batch_alter_table(table, schema=None) as batch_op:
            for name, _ in columns:
                batch_op.drop_column(name)

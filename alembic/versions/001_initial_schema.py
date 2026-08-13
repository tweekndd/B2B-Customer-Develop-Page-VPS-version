"""Initial schema baseline - all existing tables + customer_emails

Revision ID: 001_initial
Revises:
Create Date: 2026-08-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # customers
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("company_name", sa.String(255), nullable=False),
        sa.Column("website", sa.String(500), nullable=True),
        sa.Column("country", sa.String(100), nullable=True),
        sa.Column("discovery_source", sa.String(50), nullable=True),
        sa.Column("discovery_keyword", sa.String(200), nullable=True),
        sa.Column("first_found_at", sa.DateTime(), nullable=True),
        sa.Column("emails", sa.Text(), nullable=True),
        sa.Column("website_text", sa.Text(), nullable=True),
        sa.Column("positive_keywords", sa.Text(), nullable=True),
        sa.Column("negative_keywords", sa.Text(), nullable=True),
        sa.Column("industry_score", sa.Integer(), nullable=True),
        sa.Column("project_score", sa.Integer(), nullable=True),
        sa.Column("company_type_score", sa.Integer(), nullable=True),
        sa.Column("country_score", sa.Integer(), nullable=True),
        sa.Column("contact_score", sa.Integer(), nullable=True),
        sa.Column("total_score", sa.Integer(), nullable=True),
        sa.Column("priority", sa.String(1), nullable=True),
        sa.Column("company_type", sa.String(50), nullable=True),
        sa.Column("ai_summary", sa.Text(), nullable=True),
        sa.Column("sales_hook", sa.Text(), nullable=True),
        sa.Column("target_position", sa.Text(), nullable=True),
        sa.Column("identified_projects", sa.Text(), nullable=True),
        sa.Column("ai_raw_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("analyzed_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(20), nullable=True),
        sa.Column("follow_up_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("scrape_status", sa.String(20), nullable=True),
        sa.Column("ai_status", sa.String(20), nullable=True),
        sa.Column("fail_reason", sa.String(500), nullable=True),
        sa.Column("star_rating", sa.Integer(), nullable=True),
        sa.Column("city", sa.String(200), nullable=True),
        sa.Column("buyer_intent_score", sa.Integer(), nullable=True),
        sa.Column("is_price_inquiry", sa.Integer(), nullable=True),
        sa.Column("email_draft", sa.Text(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("geocode_status", sa.String(20), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_customers_company_name", "customers", ["company_name"])
    op.create_index("idx_customers_country", "customers", ["country"])
    op.create_index("idx_customers_priority", "customers", ["priority"])
    op.create_index("idx_customers_status", "customers", ["status"])
    op.create_index("idx_customers_total_score", "customers", ["total_score"])
    op.create_index("idx_customers_discovery_source", "customers", ["discovery_source"])
    op.create_index("idx_customers_analyzed_at", "customers", ["analyzed_at"])
    op.create_index("idx_customers_geocode_status", "customers", ["geocode_status"])
    op.create_index("idx_customers_map_query", "customers", ["geocode_status", "country"])

    # users
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(50), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=True),
        sa.Column("is_active", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("search_depth_limit", sa.Integer(), nullable=True),
        sa.Column("search_quota", sa.Integer(), nullable=True),
        sa.Column("searches_used", sa.Integer(), nullable=True),
        sa.Column("ai_analysis_enabled", sa.Integer(), nullable=True),
        sa.Column("email_finding_enabled", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_index("idx_users_username", "users", ["username"])

    # search_tasks
    op.create_table(
        "search_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("country", sa.String(100), nullable=False),
        sa.Column("keyword", sa.String(200), nullable=False),
        sa.Column("expanded_keywords", sa.Text(), nullable=True),
        sa.Column("search_depth", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=True),
        sa.Column("found_websites", sa.Integer(), nullable=True),
        sa.Column("analyzed_companies", sa.Integer(), nullable=True),
        sa.Column("new_companies", sa.Integer(), nullable=True),
        sa.Column("current_keyword_index", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("task_log", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_search_tasks_user_id", "search_tasks", ["user_id"])

    # search_cache
    op.create_table(
        "search_cache",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("keyword", sa.String(200), nullable=False),
        sa.Column("country", sa.String(100), nullable=False),
        sa.Column("website", sa.String(500), nullable=False),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_search_cache_keyword", "search_cache", ["keyword"])
    op.create_index("idx_search_cache_country", "search_cache", ["country"])
    op.create_index("idx_search_cache_lookup", "search_cache", ["keyword", "country", "created_at"])
    op.create_index("idx_cache_expiry_search", "search_cache", ["created_at"])

    # website_cache
    op.create_table(
        "website_cache",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("website", sa.String(500), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("last_crawled", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("website"),
    )
    op.create_index("idx_website_cache_website", "website_cache", ["website"])
    op.create_index("idx_website_cache_lookup", "website_cache", ["website", "last_crawled"])
    op.create_index("idx_cache_expiry_website", "website_cache", ["last_crawled"])

    # analysis_cache
    op.create_table(
        "analysis_cache",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("website", sa.String(500), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("company_type", sa.String(50), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("sales_hook", sa.Text(), nullable=True),
        sa.Column("target_position", sa.Text(), nullable=True),
        sa.Column("analysis_reason", sa.Text(), nullable=True),
        sa.Column("identified_projects", sa.Text(), nullable=True),
        sa.Column("raw_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_analysis_cache_website", "analysis_cache", ["website"])
    op.create_index("idx_analysis_cache_lookup", "analysis_cache", ["website", "content_hash"])
    op.create_index("idx_cache_expiry_analysis", "analysis_cache", ["created_at"])

    # hunter_cache
    op.create_table(
        "hunter_cache",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("cache_key", sa.String(500), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("query_type", sa.String(30), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cache_key"),
    )
    op.create_index("idx_hunter_cache_cache_key", "hunter_cache", ["cache_key"])
    op.create_index("idx_hunter_cache_domain", "hunter_cache", ["domain"])
    op.create_index("idx_cache_expiry_hunter", "hunter_cache", ["created_at"])

    # tomba_cache
    op.create_table(
        "tomba_cache",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("cache_key", sa.String(500), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("query_type", sa.String(30), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cache_key"),
    )
    op.create_index("idx_tomba_cache_cache_key", "tomba_cache", ["cache_key"])
    op.create_index("idx_tomba_cache_domain", "tomba_cache", ["domain"])
    op.create_index("idx_cache_expiry_tomba", "tomba_cache", ["created_at"])

    # prospeo_cache
    op.create_table(
        "prospeo_cache",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("cache_key", sa.String(500), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("query_type", sa.String(30), nullable=False),
        sa.Column("person_id", sa.String(100), nullable=True),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cache_key"),
    )
    op.create_index("idx_prospeo_cache_cache_key", "prospeo_cache", ["cache_key"])
    op.create_index("idx_prospeo_cache_domain", "prospeo_cache", ["domain", "query_type"])
    op.create_index("idx_cache_expiry_prospeo", "prospeo_cache", ["created_at"])

    # email_quota_log
    op.create_table(
        "email_quota_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("query_type", sa.String(30), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=True),
        sa.Column("credits_consumed", sa.Integer(), nullable=True),
        sa.Column("success", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_cache_expiry_quota", "email_quota_log", ["created_at"])

    # geocode_cache
    op.create_table(
        "geocode_cache",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("query_key", sa.String(500), nullable=False),
        sa.Column("country", sa.String(100), nullable=True),
        sa.Column("city", sa.String(200), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("display_name", sa.String(500), nullable=True),
        sa.Column("hits", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("query_key"),
    )
    op.create_index("idx_geocode_cache_query_key", "geocode_cache", ["query_key"])

    # user_api_config
    op.create_table(
        "user_api_config",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("service", sa.String(30), nullable=False),
        sa.Column("provider", sa.String(50), nullable=True),
        sa.Column("api_key", sa.Text(), nullable=True),
        sa.Column("api_secret", sa.Text(), nullable=True),
        sa.Column("base_url", sa.String(500), nullable=True),
        sa.Column("default_model", sa.String(100), nullable=True),
        sa.Column("fallback_models", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "service", name="uq_user_api_config"),
    )
    op.create_index("idx_user_api_config_user_id", "user_api_config", ["user_id"])
    op.create_index("idx_user_api_config_service", "user_api_config", ["service"])

    # customer_emails (V5.0 - email normalization)
    op.create_table(
        "customer_emails",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("source", sa.String(30), nullable=True),
        sa.Column("first_name", sa.String(100), nullable=True),
        sa.Column("last_name", sa.String(100), nullable=True),
        sa.Column("position", sa.String(200), nullable=True),
        sa.Column("department", sa.String(100), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("linkedin", sa.String(500), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("verification", sa.String(30), nullable=True),
        sa.Column("is_primary", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.UniqueConstraint("customer_id", "email", name="uq_customer_email"),
    )
    op.create_index("idx_customer_emails_customer_id", "customer_emails", ["customer_id"])
    op.create_index("idx_customer_emails_email", "customer_emails", ["email"])


def downgrade() -> None:
    op.drop_table("customer_emails")
    op.drop_table("user_api_config")
    op.drop_table("geocode_cache")
    op.drop_table("email_quota_log")
    op.drop_table("prospeo_cache")
    op.drop_table("tomba_cache")
    op.drop_table("hunter_cache")
    op.drop_table("analysis_cache")
    op.drop_table("website_cache")
    op.drop_table("search_cache")
    op.drop_table("search_tasks")
    op.drop_table("users")
    op.drop_table("customers")

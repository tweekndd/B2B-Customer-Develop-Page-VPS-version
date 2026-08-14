"""
数据库初始化与自动迁移（V5.3 重构：从 database.py 拆出）

init_db() 负责开发/生产环境启动初始化：
- Base.metadata.create_all 建表
- 索引 DDL
- 手写 ALTER TABLE ADD COLUMN 自动迁移（兼容历史 SQLite 库）
"""
import datetime

from app.core.database import Base, engine


def _ensure_indexes(engine):
    """确保关键查询字段存在索引（兼容已有数据库）"""
    import sqlalchemy as sa
    indexes = {
        "idx_customers_country": "CREATE INDEX IF NOT EXISTS idx_customers_country ON customers(country)",
        "idx_customers_priority": "CREATE INDEX IF NOT EXISTS idx_customers_priority ON customers(priority)",
        "idx_customers_status": "CREATE INDEX IF NOT EXISTS idx_customers_status ON customers(status)",
        "idx_customers_total_score": "CREATE INDEX IF NOT EXISTS idx_customers_total_score ON customers(total_score)",
        "idx_customers_discovery_source": "CREATE INDEX IF NOT EXISTS idx_customers_discovery_source ON customers(discovery_source)",
        "idx_customers_analyzed_at": "CREATE INDEX IF NOT EXISTS idx_customers_analyzed_at ON customers(analyzed_at)",
        "idx_search_cache_lookup": "CREATE INDEX IF NOT EXISTS idx_search_cache_lookup ON search_cache(keyword, country, created_at)",
        "idx_website_cache_lookup": "CREATE INDEX IF NOT EXISTS idx_website_cache_lookup ON website_cache(website, last_crawled)",
        "idx_analysis_cache_lookup": "CREATE INDEX IF NOT EXISTS idx_analysis_cache_lookup ON analysis_cache(website, content_hash)",
        "idx_cache_expiry_search": "CREATE INDEX IF NOT EXISTS idx_cache_expiry_search ON search_cache(created_at)",
        "idx_cache_expiry_website": "CREATE INDEX IF NOT EXISTS idx_cache_expiry_website ON website_cache(last_crawled)",
        "idx_cache_expiry_analysis": "CREATE INDEX IF NOT EXISTS idx_cache_expiry_analysis ON analysis_cache(created_at)",
        "idx_cache_expiry_hunter": "CREATE INDEX IF NOT EXISTS idx_cache_expiry_hunter ON hunter_cache(created_at)",
        "idx_cache_expiry_tomba": "CREATE INDEX IF NOT EXISTS idx_cache_expiry_tomba ON tomba_cache(created_at)",
        "idx_cache_expiry_quota": "CREATE INDEX IF NOT EXISTS idx_cache_expiry_quota ON email_quota_log(created_at)",
        "idx_cache_expiry_prospeo": "CREATE INDEX IF NOT EXISTS idx_cache_expiry_prospeo ON prospeo_cache(created_at)",
        "idx_prospeo_cache_domain": "CREATE INDEX IF NOT EXISTS idx_prospeo_cache_domain ON prospeo_cache(domain, query_type)",
        "idx_customers_geocode_status": "CREATE INDEX IF NOT EXISTS idx_customers_geocode_status ON customers(geocode_status)",
        "idx_geocode_cache_key": "CREATE INDEX IF NOT EXISTS idx_geocode_cache_key ON geocode_cache(query_key)",
        "idx_customers_map_query": "CREATE INDEX IF NOT EXISTS idx_customers_map_query ON customers(geocode_status, country)",
        "idx_customers_last_email_sent": "CREATE INDEX IF NOT EXISTS idx_customers_last_email_sent ON customers(last_email_sent_at)",
        "idx_customer_emails_customer_id": "CREATE INDEX IF NOT EXISTS idx_customer_emails_customer_id ON customer_emails(customer_id)",
        "idx_customer_emails_email": "CREATE INDEX IF NOT EXISTS idx_customer_emails_email ON customer_emails(email)",
        "idx_customer_emails_domain": "CREATE INDEX IF NOT EXISTS idx_customer_emails_domain ON customer_emails(domain)",
        "idx_social_profiles_customer": "CREATE INDEX IF NOT EXISTS idx_social_profiles_customer ON customer_social_profiles(customer_id, platform)",
        "idx_mail_accounts_user": "CREATE INDEX IF NOT EXISTS idx_mail_accounts_user ON mail_accounts(user_id)",
        "idx_mail_activities_customer": "CREATE INDEX IF NOT EXISTS idx_mail_activities_customer ON customer_email_activities(customer_id, sent_at)",
        "idx_mail_activities_message": "CREATE INDEX IF NOT EXISTS idx_mail_activities_message ON customer_email_activities(provider_message_id)",
        # V5.3 阶段3：列表常用筛选/排序组合索引
        "idx_customers_filter_combo": "CREATE INDEX IF NOT EXISTS idx_customers_filter_combo ON customers(country, priority, status)",
        "idx_customers_score_sort": "CREATE INDEX IF NOT EXISTS idx_customers_score_sort ON customers(total_score DESC, id)",
        "idx_customers_created_sort": "CREATE INDEX IF NOT EXISTS idx_customers_created_sort ON customers(created_at, id)",
    }
    with engine.connect() as conn:
        for name, ddl in indexes.items():
            try:
                conn.execute(sa.text(ddl))
            except Exception as e:
                print(f"  索引创建跳过 {name}: {e}")
        conn.commit()


def _migrate_add_column(engine, table: str, column: str, col_type: str):
    """
    检查表是否存在某列，不存在则添加
    这是为了兼容已有数据库文件，无需手动执行迁移
    """
    import sqlalchemy as sa
    try:
        with engine.connect() as conn:
            # 检查列是否存在
            inspector = sa.inspect(engine)
            columns = [c["name"] for c in inspector.get_columns(table)]
            if column not in columns:
                conn.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                conn.commit()
                print(f"  数据库迁移: {table}.{column} 列已添加")
    except Exception as e:
        print(f"  数据库迁移跳过 {table}.{column}: {e}")


def _migrate_email_normalization(engine):
    """
    V5.0 迁移：将 customers.emails JSON 规范化到 customer_emails 独立表
    只在 customer_emails 表为空或不存在时执行（幂等）
    """
    import json as _json
    import sqlalchemy as sa
    try:
        inspector = sa.inspect(engine)
        table_names = inspector.get_table_names()
        if "customer_emails" not in table_names:
            return  # 表还未创建，跳过（由 Base.metadata.create_all 处理）

        with engine.connect() as conn:
            # 检查是否已有数据（避免重复迁移）
            count = conn.execute(sa.text("SELECT COUNT(*) FROM customer_emails")).scalar()
            if count > 0:
                return  # 已迁移过

            # 迁移现有 JSON 邮箱数据
            rows = conn.execute(sa.text("SELECT id, emails FROM customers WHERE emails IS NOT NULL AND emails != ''")).fetchall()
            migrated = 0
            for customer_id, emails_json in rows:
                try:
                    email_list = _json.loads(emails_json)
                    if not isinstance(email_list, list):
                        continue
                    for email_addr in email_list:
                        if not email_addr or not isinstance(email_addr, str):
                            continue
                        email_addr = email_addr.strip().lower()
                        if not email_addr or "@" not in email_addr:
                            continue
                        conn.execute(
                            sa.text(
                                "INSERT INTO customer_emails (customer_id, email, source, is_primary, created_at) "
                                "VALUES (:cid, :email, :source, :primary, :now)"
                            ),
                            {
                                "cid": customer_id,
                                "email": email_addr,
                                "source": "migrated",
                                "primary": 1 if email_list.index(email_addr) == 0 else 0,
                                "now": datetime.datetime.utcnow(),
                            },
                        )
                        migrated += 1
                except (_json.JSONDecodeError, TypeError):
                    continue

            conn.commit()
            if migrated > 0:
                print(f"  数据库迁移: 已迁移 {migrated} 个邮箱到 customer_emails 表")
    except Exception as e:
        print(f"  数据库迁移跳过 email_normalization: {e}")


def init_db():
    """初始化数据库：创建所有表 + 自动迁移新增列（V2.2 支持）

    注意：调用前必须已导入 app.models（确保 Base.metadata 注册全部表）。
    """
    Base.metadata.create_all(bind=engine)
    _ensure_indexes(engine)

    # ── 自动迁移：检查并添加缺失的列（SQLite 不支持 DROP COLUMN，但支持 ADD COLUMN）──
    _migrate_add_column(engine, "customers", "status", "VARCHAR(20) DEFAULT '待联系'")
    _migrate_add_column(engine, "customers", "follow_up_date", "DATE")
    _migrate_add_column(engine, "customers", "notes", "TEXT")
    _migrate_add_column(engine, "customers", "scrape_status", "VARCHAR(20)")
    _migrate_add_column(engine, "customers", "ai_status", "VARCHAR(20)")
    _migrate_add_column(engine, "customers", "fail_reason", "VARCHAR(500)")
    _migrate_add_column(engine, "customers", "star_rating", "INTEGER DEFAULT 0")
    # V3.2.4 新增：Geocoding 字段
    _migrate_add_column(engine, "customers", "latitude", "FLOAT")
    _migrate_add_column(engine, "customers", "longitude", "FLOAT")
    _migrate_add_column(engine, "customers", "geocode_status", "VARCHAR(20) DEFAULT 'pending'")
    # V3.2.5 新增：city 字段（V3.2.6 +Firecrawl 降级）
    _migrate_add_column(engine, "customers", "city", "VARCHAR(200)")
    # V4.6 新增：买家意向评分 / 价格询盘 / 开发信草稿
    _migrate_add_column(engine, "customers", "buyer_intent_score", "INTEGER")
    _migrate_add_column(engine, "customers", "is_price_inquiry", "INTEGER DEFAULT 0")
    _migrate_add_column(engine, "customers", "email_draft", "TEXT")
    # V5.2 新增：最近发信时间（Gmail 发信检测自动回填）
    _migrate_add_column(engine, "customers", "last_email_sent_at", "DATETIME")
    # 搜索任务表字段
    _migrate_add_column(engine, "search_tasks", "task_log", "TEXT")
    # Round 3 新增：搜索任务关联用户
    _migrate_add_column(engine, "search_tasks", "user_id", "INTEGER")
    # Hunter 缓存表字段
    _migrate_add_column(engine, "hunter_cache", "hits", "INTEGER DEFAULT 1")
    # V4.1 新增：用户权限字段
    _migrate_add_column(engine, "users", "search_depth_limit", "INTEGER DEFAULT 50")
    _migrate_add_column(engine, "users", "search_quota", "INTEGER DEFAULT 100")
    _migrate_add_column(engine, "users", "searches_used", "INTEGER DEFAULT 0")
    _migrate_add_column(engine, "users", "ai_analysis_enabled", "INTEGER DEFAULT 1")
    _migrate_add_column(engine, "users", "email_finding_enabled", "INTEGER DEFAULT 1")

    # V5.0 新增：邮箱规范化迁移（从 JSON 到独立表）
    _migrate_email_normalization(engine)

    # V5.1 新增：CustomerEmail 结构化字段（本地部分/域名/备注/来源详情等）
    _migrate_add_column(engine, "customer_emails", "local_part", "VARCHAR(255)")
    _migrate_add_column(engine, "customer_emails", "domain", "VARCHAR(255)")
    _migrate_add_column(engine, "customer_emails", "source_detail", "VARCHAR(255)")
    _migrate_add_column(engine, "customer_emails", "notes", "TEXT")
    _migrate_add_column(engine, "customer_emails", "created_by_user_id", "INTEGER")
    _migrate_add_column(engine, "customer_emails", "updated_at", "DATETIME")

    # V5.1 新增：CustomerSocialProfile 结构化字段（LinkedIn 公司主页）
    _migrate_add_column(engine, "customer_social_profiles", "platform", "VARCHAR(30) DEFAULT 'linkedin'")
    _migrate_add_column(engine, "customer_social_profiles", "profile_type", "VARCHAR(30) DEFAULT 'company'")
    _migrate_add_column(engine, "customer_social_profiles", "profile_url", "VARCHAR(500)")
    _migrate_add_column(engine, "customer_social_profiles", "vanity_name", "VARCHAR(200)")
    _migrate_add_column(engine, "customer_social_profiles", "external_id", "VARCHAR(200)")
    _migrate_add_column(engine, "customer_social_profiles", "display_name", "VARCHAR(300)")
    _migrate_add_column(engine, "customer_social_profiles", "website_url", "VARCHAR(500)")
    _migrate_add_column(engine, "customer_social_profiles", "logo_url", "VARCHAR(500)")
    _migrate_add_column(engine, "customer_social_profiles", "location_json", "TEXT")
    _migrate_add_column(engine, "customer_social_profiles", "staff_count_range", "VARCHAR(50)")
    _migrate_add_column(engine, "customer_social_profiles", "source", "VARCHAR(30) DEFAULT 'search'")
    _migrate_add_column(engine, "customer_social_profiles", "confidence", "FLOAT DEFAULT 0")
    _migrate_add_column(engine, "customer_social_profiles", "is_verified", "INTEGER DEFAULT 0")
    _migrate_add_column(engine, "customer_social_profiles", "last_fetched_at", "DATETIME")
    _migrate_add_column(engine, "customer_social_profiles", "raw_json", "TEXT")
    _migrate_add_column(engine, "customer_social_profiles", "created_by_user_id", "INTEGER")
    _migrate_add_column(engine, "customer_social_profiles", "updated_at", "DATETIME")

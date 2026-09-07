"""Initial schema baseline - 与 V5.3 全部业务模型一致（含 Phase0 storage_objects）

Revision ID: 001_initial
Revises:
Create Date: 2026-09-07

由 `alembic revision --autogenerate` 基于当前全部模型生成，
保证在全新 PostgreSQL 上执行 `alembic upgrade head` 即可得到与
Base.metadata.create_all 完全一致的 schema（含 V5.1-V5.3 新表）。

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision: str = '001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('analysis_cache',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('website', sa.String(length=500), nullable=False, comment='网站域名'),
    sa.Column('content_hash', sa.String(length=64), nullable=True, comment='对应官网内容的哈希值'),
    sa.Column('company_type', sa.String(length=50), nullable=True, comment='公司类型'),
    sa.Column('summary', sa.Text(), nullable=True, comment='英文摘要'),
    sa.Column('sales_hook', sa.Text(), nullable=True, comment='开发切入点'),
    sa.Column('target_position', sa.Text(), nullable=True, comment='推荐联系职位'),
    sa.Column('analysis_reason', sa.Text(), nullable=True, comment='分析原因'),
    sa.Column('identified_projects', sa.Text(), nullable=True, comment='识别的项目信息'),
    sa.Column('raw_json', sa.Text(), nullable=True, comment='AI返回的原始JSON'),
    sa.Column('created_at', sa.DateTime(), nullable=True, comment='缓存时间'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('analysis_cache', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_analysis_cache_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_analysis_cache_website'), ['website'], unique=False)

    op.create_table('customers',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('company_name', sa.String(length=255), nullable=False, comment='公司名称'),
    sa.Column('website', sa.String(length=500), nullable=True, comment='公司官网'),
    sa.Column('country', sa.String(length=100), nullable=True, comment='国家'),
    sa.Column('discovery_source', sa.String(length=50), nullable=True, comment='发现来源（Google / Manual Import）'),
    sa.Column('discovery_keyword', sa.String(length=200), nullable=True, comment='发现时使用的关键词'),
    sa.Column('first_found_at', sa.DateTime(), nullable=True, comment='首次发现时间'),
    sa.Column('emails', sa.Text(), nullable=True, comment='提取的邮箱列表（JSON格式，V5.1 起为兼容视图）'),
    sa.Column('website_text', sa.Text(), nullable=True, comment='官网爬取的纯文本内容'),
    sa.Column('positive_keywords', sa.Text(), nullable=True, comment='命中的正向关键词及次数（JSON格式）'),
    sa.Column('negative_keywords', sa.Text(), nullable=True, comment='命中的负向关键词及次数（JSON格式）'),
    sa.Column('industry_score', sa.Integer(), nullable=True, comment='行业匹配度 0-30'),
    sa.Column('project_score', sa.Integer(), nullable=True, comment='项目匹配度 0-25'),
    sa.Column('company_type_score', sa.Integer(), nullable=True, comment='公司类型 0-20'),
    sa.Column('country_score', sa.Integer(), nullable=True, comment='国家优先级 0-15'),
    sa.Column('contact_score', sa.Integer(), nullable=True, comment='联系方式完整度 0-10'),
    sa.Column('total_score', sa.Integer(), nullable=True, comment='总分 0-100'),
    sa.Column('priority', sa.String(length=1), nullable=True, comment='优先级 A/B/C/D'),
    sa.Column('company_type', sa.String(length=50), nullable=True, comment='AI分析的公司类型'),
    sa.Column('ai_summary', sa.Text(), nullable=True, comment='AI生成的150字以内摘要（英文）'),
    sa.Column('sales_hook', sa.Text(), nullable=True, comment='推荐开发切入点（中文）'),
    sa.Column('target_position', sa.Text(), nullable=True, comment='推荐联系职位（中文）'),
    sa.Column('identified_projects', sa.Text(), nullable=True, comment='AI识别的项目信息（JSON格式）'),
    sa.Column('ai_raw_json', sa.Text(), nullable=True, comment='AI返回的原始JSON数据'),
    sa.Column('created_at', sa.DateTime(), nullable=True, comment='创建时间'),
    sa.Column('analyzed_at', sa.DateTime(), nullable=True, comment='分析完成时间'),
    sa.Column('status', sa.String(length=20), nullable=True, comment='跟进状态: 待联系/已发邮件/已回复/无效线索/成单'),
    sa.Column('follow_up_date', sa.Date(), nullable=True, comment='下次跟进日期'),
    sa.Column('notes', sa.Text(), nullable=True, comment='跟进备注'),
    sa.Column('last_email_sent_at', sa.DateTime(), nullable=True, comment='最近发信时间（Gmail 检测到发信后自动更新）'),
    sa.Column('scrape_status', sa.String(length=20), nullable=True, comment='官网抓取状态: success/failed/partial/skipped'),
    sa.Column('ai_status', sa.String(length=20), nullable=True, comment='AI分析状态: success/failed/skipped'),
    sa.Column('fail_reason', sa.String(length=500), nullable=True, comment='失败原因描述'),
    sa.Column('star_rating', sa.Integer(), nullable=True, comment='客户评级: 0未评级/1-5星'),
    sa.Column('city', sa.String(length=200), nullable=True, comment='城市'),
    sa.Column('buyer_intent_score', sa.Integer(), nullable=True, comment='AI买家意向评分 0-10'),
    sa.Column('is_price_inquiry', sa.Integer(), nullable=True, comment='是否价格询盘: 1是/0否'),
    sa.Column('email_draft', sa.Text(), nullable=True, comment='AI生成的开发信草稿（JSON格式）'),
    sa.Column('latitude', sa.Float(), nullable=True, comment='纬度'),
    sa.Column('longitude', sa.Float(), nullable=True, comment='经度'),
    sa.Column('geocode_status', sa.String(length=20), nullable=True, comment='地理编码状态: pending/done/failed'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('customers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_customers_analyzed_at'), ['analyzed_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_customers_company_name'), ['company_name'], unique=False)
        batch_op.create_index(batch_op.f('ix_customers_discovery_source'), ['discovery_source'], unique=False)
        batch_op.create_index(batch_op.f('ix_customers_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_customers_last_email_sent_at'), ['last_email_sent_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_customers_priority'), ['priority'], unique=False)
        batch_op.create_index(batch_op.f('ix_customers_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_customers_total_score'), ['total_score'], unique=False)

    op.create_table('email_quota_log',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('source', sa.String(length=30), nullable=False, comment='数据源: hunter/tomba/scraped'),
    sa.Column('query_type', sa.String(length=30), nullable=False, comment='查询类型'),
    sa.Column('domain', sa.String(length=255), nullable=False, comment='查询的域名'),
    sa.Column('result_count', sa.Integer(), nullable=True, comment='返回结果数量'),
    sa.Column('credits_consumed', sa.Integer(), nullable=True, comment='消耗的配额次数'),
    sa.Column('success', sa.Integer(), nullable=True, comment='是否成功: 1成功/0失败'),
    sa.Column('error_message', sa.String(length=500), nullable=True, comment='错误信息'),
    sa.Column('created_at', sa.DateTime(), nullable=True, comment='记录时间'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('email_quota_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_email_quota_log_id'), ['id'], unique=False)

    op.create_table('geocode_cache',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('query_key', sa.String(length=500), nullable=False, comment='查询键: city|Country 或 Country'),
    sa.Column('country', sa.String(length=100), nullable=True, comment='国家'),
    sa.Column('city', sa.String(length=200), nullable=True, comment='城市'),
    sa.Column('latitude', sa.Float(), nullable=False, comment='纬度'),
    sa.Column('longitude', sa.Float(), nullable=False, comment='经度'),
    sa.Column('display_name', sa.String(length=500), nullable=True, comment='Nominatim 返回的完整地址名'),
    sa.Column('hits', sa.Integer(), nullable=True, comment='命中次数'),
    sa.Column('created_at', sa.DateTime(), nullable=True, comment='缓存时间'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('geocode_cache', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_geocode_cache_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_geocode_cache_query_key'), ['query_key'], unique=True)

    op.create_table('hunter_cache',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('cache_key', sa.String(length=500), nullable=False, comment='缓存唯一键: domain|type|params'),
    sa.Column('domain', sa.String(length=255), nullable=False, comment='公司域名'),
    sa.Column('query_type', sa.String(length=30), nullable=False, comment='查询类型: email_count/domain_search/email_finder/email_verifier'),
    sa.Column('result', sa.Text(), nullable=False, comment='API 返回结果 (JSON)'),
    sa.Column('hits', sa.Integer(), nullable=True, comment='缓存命中次数（辅助统计）'),
    sa.Column('created_at', sa.DateTime(), nullable=True, comment='创建时间'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('hunter_cache', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_hunter_cache_cache_key'), ['cache_key'], unique=True)
        batch_op.create_index(batch_op.f('ix_hunter_cache_domain'), ['domain'], unique=False)
        batch_op.create_index(batch_op.f('ix_hunter_cache_id'), ['id'], unique=False)

    op.create_table('linkedin_oauth_tokens',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False, comment='用户ID（关联 users.id）'),
    sa.Column('access_token_encrypted', sa.Text(), nullable=False, comment='access token（Fernet 加密存储）'),
    sa.Column('scope', sa.String(length=255), nullable=True, comment='授权 scope'),
    sa.Column('expires_at', sa.DateTime(), nullable=True, comment='token 过期时间'),
    sa.Column('created_at', sa.DateTime(), nullable=True, comment='创建时间'),
    sa.Column('updated_at', sa.DateTime(), nullable=True, comment='更新时间'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('linkedin_oauth_tokens', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_linkedin_oauth_tokens_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_linkedin_oauth_tokens_user_id'), ['user_id'], unique=True)

    op.create_table('mail_accounts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False, comment='系统用户ID（关联 users.id）'),
    sa.Column('provider', sa.String(length=30), nullable=True, comment='邮箱平台: gmail'),
    sa.Column('email_address', sa.String(length=255), nullable=False, comment='已授权邮箱地址'),
    sa.Column('provider_user_id', sa.String(length=255), nullable=True, comment='Google subject（sub）'),
    sa.Column('access_token_encrypted', sa.Text(), nullable=True, comment='access token（Fernet 加密存储）'),
    sa.Column('refresh_token_encrypted', sa.Text(), nullable=True, comment='refresh token（Fernet 加密存储）'),
    sa.Column('token_expires_at', sa.DateTime(), nullable=True, comment='access token 过期时间'),
    sa.Column('scopes', sa.Text(), nullable=True, comment='OAuth scope JSON'),
    sa.Column('sync_cursor', sa.String(length=255), nullable=True, comment='Gmail historyId 或 delta 游标'),
    sa.Column('subscription_id', sa.String(length=255), nullable=True, comment='订阅/推送 ID，可为空'),
    sa.Column('watch_expiration_at', sa.DateTime(), nullable=True, comment='Gmail watch 到期时间'),
    sa.Column('last_synced_at', sa.DateTime(), nullable=True, comment='最近同步时间'),
    sa.Column('status', sa.String(length=30), nullable=True, comment='状态: active/reauth_required/error/disabled'),
    sa.Column('last_error', sa.Text(), nullable=True, comment='最近错误信息'),
    sa.Column('created_at', sa.DateTime(), nullable=True, comment='创建时间'),
    sa.Column('updated_at', sa.DateTime(), nullable=True, comment='更新时间'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('mail_accounts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_mail_accounts_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_mail_accounts_user_id'), ['user_id'], unique=False)

    op.create_table('prospeo_cache',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('cache_key', sa.String(length=500), nullable=False, comment='缓存唯一键: domain|type|params'),
    sa.Column('domain', sa.String(length=255), nullable=False, comment='公司域名'),
    sa.Column('query_type', sa.String(length=30), nullable=False, comment='查询类型: search_person/enrich_person'),
    sa.Column('person_id', sa.String(length=100), nullable=True, comment='Enrich 时对应的人员 ID'),
    sa.Column('result', sa.Text(), nullable=False, comment='API 返回结果 (JSON)'),
    sa.Column('hits', sa.Integer(), nullable=True, comment='缓存命中次数（辅助统计）'),
    sa.Column('created_at', sa.DateTime(), nullable=True, comment='创建时间'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('prospeo_cache', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_prospeo_cache_cache_key'), ['cache_key'], unique=True)
        batch_op.create_index(batch_op.f('ix_prospeo_cache_domain'), ['domain'], unique=False)
        batch_op.create_index(batch_op.f('ix_prospeo_cache_id'), ['id'], unique=False)

    op.create_table('search_cache',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('keyword', sa.String(length=200), nullable=False, comment='搜索关键词'),
    sa.Column('country', sa.String(length=100), nullable=False, comment='搜索国家'),
    sa.Column('website', sa.String(length=500), nullable=False, comment='发现的企业官网'),
    sa.Column('title', sa.String(length=500), nullable=True, comment='搜索结果标题'),
    sa.Column('snippet', sa.Text(), nullable=True, comment='搜索结果摘要'),
    sa.Column('created_at', sa.DateTime(), nullable=True, comment='缓存时间'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('search_cache', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_search_cache_country'), ['country'], unique=False)
        batch_op.create_index(batch_op.f('ix_search_cache_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_search_cache_keyword'), ['keyword'], unique=False)

    op.create_table('search_tasks',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('country', sa.String(length=100), nullable=False, comment='搜索国家'),
    sa.Column('keyword', sa.String(length=200), nullable=False, comment='原始关键词'),
    sa.Column('expanded_keywords', sa.Text(), nullable=True, comment='AI扩展的关键词列表（JSON数组）'),
    sa.Column('search_depth', sa.Integer(), nullable=True, comment='每个关键词期望搜索数量'),
    sa.Column('user_id', sa.Integer(), nullable=True, comment='创建任务的用户ID'),
    sa.Column('status', sa.String(length=20), nullable=True, comment='任务状态: Pending/Running/Completed/Failed/Paused'),
    sa.Column('found_websites', sa.Integer(), nullable=True, comment='发现的网站数量'),
    sa.Column('analyzed_companies', sa.Integer(), nullable=True, comment='已分析的公司数量'),
    sa.Column('new_companies', sa.Integer(), nullable=True, comment='新增公司数量'),
    sa.Column('current_keyword_index', sa.Integer(), nullable=True, comment='当前处理到第几个扩展关键词（断点续跑）'),
    sa.Column('error_message', sa.Text(), nullable=True, comment='失败时的错误信息'),
    sa.Column('created_at', sa.DateTime(), nullable=True, comment='创建时间'),
    sa.Column('finished_at', sa.DateTime(), nullable=True, comment='完成时间'),
    sa.Column('task_log', sa.Text(), nullable=True, comment='任务运行日志'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('search_tasks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_search_tasks_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_search_tasks_user_id'), ['user_id'], unique=False)

    op.create_table('storage_objects',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('object_key', sa.String(length=128), nullable=False, comment='对象键（默认内容哈希）'),
    sa.Column('content_hash', sa.String(length=64), nullable=False, comment='sha256 完整哈希'),
    sa.Column('content_type', sa.String(length=128), nullable=True, comment='MIME 类型'),
    sa.Column('size_bytes', sa.Integer(), nullable=False, comment='字节大小'),
    sa.Column('storage_provider', sa.String(length=30), nullable=True, comment='存储 Provider: local/s3/...'),
    sa.Column('retention_until', sa.DateTime(), nullable=True, comment='保留截止时间，空=长期保留'),
    sa.Column('created_at', sa.DateTime(), nullable=True, comment='创建时间'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('storage_objects', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_storage_objects_content_hash'), ['content_hash'], unique=False)
        batch_op.create_index(batch_op.f('ix_storage_objects_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_storage_objects_object_key'), ['object_key'], unique=True)

    op.create_table('tomba_cache',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('cache_key', sa.String(length=500), nullable=False, comment='缓存唯一键: domain|type|params'),
    sa.Column('domain', sa.String(length=255), nullable=False, comment='公司域名'),
    sa.Column('query_type', sa.String(length=30), nullable=False, comment='查询类型: domain_search/email_finder/email_verifier'),
    sa.Column('result', sa.Text(), nullable=False, comment='API 返回结果 (JSON)'),
    sa.Column('hits', sa.Integer(), nullable=True, comment='缓存命中次数（辅助统计）'),
    sa.Column('created_at', sa.DateTime(), nullable=True, comment='创建时间'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('tomba_cache', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tomba_cache_cache_key'), ['cache_key'], unique=True)
        batch_op.create_index(batch_op.f('ix_tomba_cache_domain'), ['domain'], unique=False)
        batch_op.create_index(batch_op.f('ix_tomba_cache_id'), ['id'], unique=False)

    op.create_table('user_api_config',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False, comment='用户ID（关联 users.id）'),
    sa.Column('service', sa.String(length=30), nullable=False, comment='服务: llm/hunter/tomba/prospeo/tavily/serpapi/searxng/firecrawl/search_engine'),
    sa.Column('provider', sa.String(length=50), nullable=True, comment='LLM Provider 名称（glm/openai-compatible/deepseek/qwen/custom）'),
    sa.Column('api_key', sa.Text(), nullable=True, comment='API Key（Fernet 加密存储）'),
    sa.Column('api_secret', sa.Text(), nullable=True, comment='API Secret（Fernet 加密存储，如 Tomba）'),
    sa.Column('base_url', sa.String(length=500), nullable=True, comment='Base URL / SearXNG URL / Reader URL / 偏好的搜索引擎名'),
    sa.Column('default_model', sa.String(length=100), nullable=True, comment='默认模型（LLM）'),
    sa.Column('fallback_models', sa.Text(), nullable=True, comment='备用模型列表（JSON数组，LLM）'),
    sa.Column('enabled', sa.Integer(), nullable=True, comment='是否启用: 1启用/0禁用'),
    sa.Column('created_at', sa.DateTime(), nullable=True, comment='创建时间'),
    sa.Column('updated_at', sa.DateTime(), nullable=True, comment='更新时间'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'service', name='uq_user_api_config')
    )
    with op.batch_alter_table('user_api_config', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_api_config_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_api_config_service'), ['service'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_api_config_user_id'), ['user_id'], unique=False)

    op.create_table('users',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('username', sa.String(length=50), nullable=False, comment='用户名'),
    sa.Column('password_hash', sa.String(length=255), nullable=False, comment='密码哈希（bcrypt）'),
    sa.Column('role', sa.String(length=20), nullable=True, comment='角色: admin/user'),
    sa.Column('is_active', sa.Integer(), nullable=True, comment='是否激活: 1激活/0禁用'),
    sa.Column('created_at', sa.DateTime(), nullable=True, comment='创建时间'),
    sa.Column('updated_at', sa.DateTime(), nullable=True, comment='更新时间'),
    sa.Column('search_depth_limit', sa.Integer(), nullable=True, comment='每次搜索允许的最大结果数'),
    sa.Column('search_quota', sa.Integer(), nullable=True, comment='搜索次数总配额'),
    sa.Column('searches_used', sa.Integer(), nullable=True, comment='已使用的搜索次数'),
    sa.Column('ai_analysis_enabled', sa.Integer(), nullable=True, comment='是否允许 AI 分析: 1允许/0禁止'),
    sa.Column('email_finding_enabled', sa.Integer(), nullable=True, comment='是否允许邮箱查找: 1允许/0禁止'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_users_username'), ['username'], unique=True)

    op.create_table('website_cache',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('website', sa.String(length=500), nullable=False, comment='网站域名'),
    sa.Column('content', sa.Text(), nullable=True, comment='抓取的纯文本内容'),
    sa.Column('content_hash', sa.String(length=64), nullable=True, comment='内容哈希值，用于判断内容是否变化'),
    sa.Column('last_crawled', sa.DateTime(), nullable=True, comment='上次抓取时间'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('website_cache', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_website_cache_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_website_cache_website'), ['website'], unique=True)

    op.create_table('analysis_runs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('customer_id', sa.Integer(), nullable=False, comment='关联客户ID'),
    sa.Column('website_snapshot_id', sa.Integer(), nullable=True, comment='基于哪次抓取快照'),
    sa.Column('content_hash', sa.String(length=64), nullable=True, comment='分析内容的哈希'),
    sa.Column('provider', sa.String(length=50), nullable=True, comment='LLM Provider（glm/deepseek/...）'),
    sa.Column('model', sa.String(length=100), nullable=True, comment='使用的模型'),
    sa.Column('status', sa.String(length=20), nullable=True, comment='success/failed'),
    sa.Column('company_type', sa.String(length=50), nullable=True, comment='公司类型'),
    sa.Column('summary', sa.Text(), nullable=True, comment='英文摘要'),
    sa.Column('sales_hook', sa.Text(), nullable=True, comment='开发切入点'),
    sa.Column('target_position', sa.Text(), nullable=True, comment='推荐联系职位'),
    sa.Column('identified_projects', sa.Text(), nullable=True, comment='项目信息（JSON）'),
    sa.Column('analysis_reason', sa.Text(), nullable=True, comment='分析原因'),
    sa.Column('buyer_intent_score', sa.Integer(), nullable=True, comment='买家意向评分 0-10'),
    sa.Column('is_price_inquiry', sa.Integer(), nullable=True, comment='是否价格询盘'),
    sa.Column('address_city', sa.String(length=200), nullable=True, comment='AI 识别城市'),
    sa.Column('needs_identified', sa.Text(), nullable=True, comment='客户需求清单（JSON）'),
    sa.Column('product_match', sa.String(length=500), nullable=True, comment='产品匹配关键词'),
    sa.Column('raw_json', sa.Text(), nullable=True, comment='AI 原始返回 JSON'),
    sa.Column('error_message', sa.Text(), nullable=True, comment='失败原因'),
    sa.Column('created_at', sa.DateTime(), nullable=True, comment='分析时间'),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('analysis_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_analysis_runs_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_analysis_runs_customer_id'), ['customer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_analysis_runs_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_analysis_runs_website_snapshot_id'), ['website_snapshot_id'], unique=False)

    op.create_table('customer_email_activities',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('customer_id', sa.Integer(), nullable=False, comment='匹配到的客户ID'),
    sa.Column('mail_account_id', sa.Integer(), nullable=True, comment='哪个自有邮箱检测到'),
    sa.Column('provider', sa.String(length=30), nullable=True, comment='邮箱平台'),
    sa.Column('provider_message_id', sa.String(length=255), nullable=False, comment='第三方消息 ID'),
    sa.Column('internet_message_id', sa.String(length=255), nullable=True, comment='RFC Message-ID'),
    sa.Column('thread_id', sa.String(length=255), nullable=True, comment='Gmail threadId'),
    sa.Column('from_address', sa.String(length=255), nullable=True, comment='发件人'),
    sa.Column('to_addresses_json', sa.Text(), nullable=True, comment='To 收件人列表 JSON'),
    sa.Column('cc_addresses_json', sa.Text(), nullable=True, comment='CC 列表 JSON'),
    sa.Column('subject', sa.Text(), nullable=True, comment='邮件标题'),
    sa.Column('sent_at', sa.DateTime(), nullable=True, comment='发出时间（UTC）'),
    sa.Column('matched_domain', sa.String(length=255), nullable=True, comment='用于匹配的收件人域名'),
    sa.Column('match_type', sa.String(length=30), nullable=True, comment='exact_domain/manual_email'),
    sa.Column('snippet', sa.Text(), nullable=True, comment='短预览（默认不保存正文）'),
    sa.Column('raw_metadata_json', sa.Text(), nullable=True, comment='必要元数据 JSON'),
    sa.Column('is_ignored', sa.Integer(), nullable=True, comment='用户忽略误匹配: 1是/0否'),
    sa.Column('created_at', sa.DateTime(), nullable=True, comment='入库时间'),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('mail_account_id', 'provider', 'provider_message_id', 'matched_domain', name='uq_mail_activity')
    )
    with op.batch_alter_table('customer_email_activities', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_customer_email_activities_customer_id'), ['customer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_customer_email_activities_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_customer_email_activities_mail_account_id'), ['mail_account_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_customer_email_activities_sent_at'), ['sent_at'], unique=False)

    op.create_table('customer_emails',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('customer_id', sa.Integer(), nullable=False, comment='关联客户ID'),
    sa.Column('email', sa.String(length=255), nullable=False, comment='邮箱地址'),
    sa.Column('local_part', sa.String(length=255), nullable=True, comment='邮箱用户名部分'),
    sa.Column('domain', sa.String(length=255), nullable=True, comment='邮箱域名'),
    sa.Column('source', sa.String(length=30), nullable=True, comment='来源: website/hunter/tomba/prospeo/manual/legacy'),
    sa.Column('source_detail', sa.String(length=255), nullable=True, comment='来源详情（搜索任务ID/第三方查询类型/用户备注）'),
    sa.Column('first_name', sa.String(length=100), nullable=True, comment='名'),
    sa.Column('last_name', sa.String(length=100), nullable=True, comment='姓'),
    sa.Column('position', sa.String(length=200), nullable=True, comment='职位'),
    sa.Column('department', sa.String(length=100), nullable=True, comment='部门'),
    sa.Column('phone', sa.String(length=50), nullable=True, comment='电话'),
    sa.Column('linkedin', sa.String(length=500), nullable=True, comment='LinkedIn URL'),
    sa.Column('score', sa.Integer(), nullable=True, comment='置信度分数 0-100'),
    sa.Column('verification', sa.String(length=30), nullable=True, comment='验证状态: valid/invalid/unknown'),
    sa.Column('notes', sa.Text(), nullable=True, comment='用户备注'),
    sa.Column('created_by_user_id', sa.Integer(), nullable=True, comment='手动新增者用户ID'),
    sa.Column('is_primary', sa.Integer(), nullable=True, comment='是否主要联系邮箱: 1是/0否'),
    sa.Column('created_at', sa.DateTime(), nullable=True, comment='创建时间'),
    sa.Column('updated_at', sa.DateTime(), nullable=True, comment='更新时间'),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('customer_id', 'email', name='uq_customer_email')
    )
    with op.batch_alter_table('customer_emails', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_customer_emails_customer_id'), ['customer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_customer_emails_domain'), ['domain'], unique=False)
        batch_op.create_index(batch_op.f('ix_customer_emails_email'), ['email'], unique=False)
        batch_op.create_index(batch_op.f('ix_customer_emails_id'), ['id'], unique=False)

    op.create_table('customer_social_profiles',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('customer_id', sa.Integer(), nullable=False, comment='关联客户ID'),
    sa.Column('platform', sa.String(length=30), nullable=True, comment='平台: linkedin'),
    sa.Column('profile_type', sa.String(length=30), nullable=True, comment='主页类型: company'),
    sa.Column('profile_url', sa.String(length=500), nullable=False, comment='标准化后的公开 URL'),
    sa.Column('vanity_name', sa.String(length=200), nullable=True, comment='LinkedIn vanity name'),
    sa.Column('external_id', sa.String(length=200), nullable=True, comment='平台组织ID/URN（官方 API 时）'),
    sa.Column('display_name', sa.String(length=300), nullable=True, comment='平台显示名称'),
    sa.Column('website_url', sa.String(length=500), nullable=True, comment='平台返回的官网'),
    sa.Column('logo_url', sa.String(length=500), nullable=True, comment='Logo 地址，可选'),
    sa.Column('location_json', sa.Text(), nullable=True, comment='地点 JSON'),
    sa.Column('staff_count_range', sa.String(length=50), nullable=True, comment='员工规模范围，可选'),
    sa.Column('source', sa.String(length=30), nullable=True, comment='来源: search/manual/official_api'),
    sa.Column('confidence', sa.Float(), nullable=True, comment='候选置信度 0-100'),
    sa.Column('is_verified', sa.Integer(), nullable=True, comment='用户是否确认: 1是/0否'),
    sa.Column('last_fetched_at', sa.DateTime(), nullable=True, comment='最近抓取时间'),
    sa.Column('raw_json', sa.Text(), nullable=True, comment='原始 API 结果（脱敏后）'),
    sa.Column('created_by_user_id', sa.Integer(), nullable=True, comment='手动新增者用户ID'),
    sa.Column('created_at', sa.DateTime(), nullable=True, comment='创建时间'),
    sa.Column('updated_at', sa.DateTime(), nullable=True, comment='更新时间'),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('customer_id', 'platform', 'profile_type', 'profile_url', name='uq_customer_social_profile')
    )
    with op.batch_alter_table('customer_social_profiles', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_customer_social_profiles_customer_id'), ['customer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_customer_social_profiles_id'), ['id'], unique=False)

    op.create_table('score_snapshots',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('customer_id', sa.Integer(), nullable=False, comment='关联客户ID'),
    sa.Column('analysis_run_id', sa.Integer(), nullable=True, comment='关联的分析运行（可选）'),
    sa.Column('industry_score', sa.Integer(), nullable=True, comment='行业匹配度 0-30'),
    sa.Column('project_score', sa.Integer(), nullable=True, comment='项目匹配度 0-25'),
    sa.Column('company_type_score', sa.Integer(), nullable=True, comment='公司类型 0-20'),
    sa.Column('country_score', sa.Integer(), nullable=True, comment='国家优先级 0-15'),
    sa.Column('contact_score', sa.Integer(), nullable=True, comment='联系方式 0-10'),
    sa.Column('total_score', sa.Integer(), nullable=True, comment='总分 0-100'),
    sa.Column('priority', sa.String(length=1), nullable=True, comment='优先级 A/B/C/D'),
    sa.Column('created_at', sa.DateTime(), nullable=True, comment='评分时间'),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('score_snapshots', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_score_snapshots_analysis_run_id'), ['analysis_run_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_score_snapshots_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_score_snapshots_customer_id'), ['customer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_score_snapshots_id'), ['id'], unique=False)

    op.create_table('website_snapshots',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('customer_id', sa.Integer(), nullable=False, comment='关联客户ID'),
    sa.Column('website', sa.String(length=500), nullable=True, comment='抓取的域名'),
    sa.Column('content', sa.Text(), nullable=True, comment='官网纯文本内容'),
    sa.Column('content_hash', sa.String(length=64), nullable=True, comment='内容哈希（去重用）'),
    sa.Column('scrape_status', sa.String(length=20), nullable=True, comment='抓取状态: success/failed/partial/skipped'),
    sa.Column('source', sa.String(length=30), nullable=True, comment='来源: pipeline/manual/backfill'),
    sa.Column('created_at', sa.DateTime(), nullable=True, comment='抓取时间'),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('website_snapshots', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_website_snapshots_content_hash'), ['content_hash'], unique=False)
        batch_op.create_index(batch_op.f('ix_website_snapshots_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_website_snapshots_customer_id'), ['customer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_website_snapshots_id'), ['id'], unique=False)

    


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('website_snapshots', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_website_snapshots_id'))
        batch_op.drop_index(batch_op.f('ix_website_snapshots_customer_id'))
        batch_op.drop_index(batch_op.f('ix_website_snapshots_created_at'))
        batch_op.drop_index(batch_op.f('ix_website_snapshots_content_hash'))

    op.drop_table('website_snapshots')
    with op.batch_alter_table('score_snapshots', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_score_snapshots_id'))
        batch_op.drop_index(batch_op.f('ix_score_snapshots_customer_id'))
        batch_op.drop_index(batch_op.f('ix_score_snapshots_created_at'))
        batch_op.drop_index(batch_op.f('ix_score_snapshots_analysis_run_id'))

    op.drop_table('score_snapshots')
    with op.batch_alter_table('customer_social_profiles', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_customer_social_profiles_id'))
        batch_op.drop_index(batch_op.f('ix_customer_social_profiles_customer_id'))

    op.drop_table('customer_social_profiles')
    with op.batch_alter_table('customer_emails', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_customer_emails_id'))
        batch_op.drop_index(batch_op.f('ix_customer_emails_email'))
        batch_op.drop_index(batch_op.f('ix_customer_emails_domain'))
        batch_op.drop_index(batch_op.f('ix_customer_emails_customer_id'))

    op.drop_table('customer_emails')
    with op.batch_alter_table('customer_email_activities', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_customer_email_activities_sent_at'))
        batch_op.drop_index(batch_op.f('ix_customer_email_activities_mail_account_id'))
        batch_op.drop_index(batch_op.f('ix_customer_email_activities_id'))
        batch_op.drop_index(batch_op.f('ix_customer_email_activities_customer_id'))

    op.drop_table('customer_email_activities')
    with op.batch_alter_table('analysis_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_analysis_runs_website_snapshot_id'))
        batch_op.drop_index(batch_op.f('ix_analysis_runs_id'))
        batch_op.drop_index(batch_op.f('ix_analysis_runs_customer_id'))
        batch_op.drop_index(batch_op.f('ix_analysis_runs_created_at'))

    op.drop_table('analysis_runs')
    with op.batch_alter_table('website_cache', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_website_cache_website'))
        batch_op.drop_index(batch_op.f('ix_website_cache_id'))

    op.drop_table('website_cache')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_username'))
        batch_op.drop_index(batch_op.f('ix_users_id'))

    op.drop_table('users')
    with op.batch_alter_table('user_api_config', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_api_config_user_id'))
        batch_op.drop_index(batch_op.f('ix_user_api_config_service'))
        batch_op.drop_index(batch_op.f('ix_user_api_config_id'))

    op.drop_table('user_api_config')
    with op.batch_alter_table('tomba_cache', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tomba_cache_id'))
        batch_op.drop_index(batch_op.f('ix_tomba_cache_domain'))
        batch_op.drop_index(batch_op.f('ix_tomba_cache_cache_key'))

    op.drop_table('tomba_cache')
    with op.batch_alter_table('storage_objects', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_storage_objects_object_key'))
        batch_op.drop_index(batch_op.f('ix_storage_objects_id'))
        batch_op.drop_index(batch_op.f('ix_storage_objects_content_hash'))

    op.drop_table('storage_objects')
    with op.batch_alter_table('search_tasks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_search_tasks_user_id'))
        batch_op.drop_index(batch_op.f('ix_search_tasks_id'))

    op.drop_table('search_tasks')
    with op.batch_alter_table('search_cache', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_search_cache_keyword'))
        batch_op.drop_index(batch_op.f('ix_search_cache_id'))
        batch_op.drop_index(batch_op.f('ix_search_cache_country'))

    op.drop_table('search_cache')
    with op.batch_alter_table('prospeo_cache', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_prospeo_cache_id'))
        batch_op.drop_index(batch_op.f('ix_prospeo_cache_domain'))
        batch_op.drop_index(batch_op.f('ix_prospeo_cache_cache_key'))

    op.drop_table('prospeo_cache')
    with op.batch_alter_table('mail_accounts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_mail_accounts_user_id'))
        batch_op.drop_index(batch_op.f('ix_mail_accounts_id'))

    op.drop_table('mail_accounts')
    with op.batch_alter_table('linkedin_oauth_tokens', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_linkedin_oauth_tokens_user_id'))
        batch_op.drop_index(batch_op.f('ix_linkedin_oauth_tokens_id'))

    op.drop_table('linkedin_oauth_tokens')
    with op.batch_alter_table('hunter_cache', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_hunter_cache_id'))
        batch_op.drop_index(batch_op.f('ix_hunter_cache_domain'))
        batch_op.drop_index(batch_op.f('ix_hunter_cache_cache_key'))

    op.drop_table('hunter_cache')
    with op.batch_alter_table('geocode_cache', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_geocode_cache_query_key'))
        batch_op.drop_index(batch_op.f('ix_geocode_cache_id'))

    op.drop_table('geocode_cache')
    with op.batch_alter_table('email_quota_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_email_quota_log_id'))

    op.drop_table('email_quota_log')
    with op.batch_alter_table('customers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_customers_total_score'))
        batch_op.drop_index(batch_op.f('ix_customers_status'))
        batch_op.drop_index(batch_op.f('ix_customers_priority'))
        batch_op.drop_index(batch_op.f('ix_customers_last_email_sent_at'))
        batch_op.drop_index(batch_op.f('ix_customers_id'))
        batch_op.drop_index(batch_op.f('ix_customers_discovery_source'))
        batch_op.drop_index(batch_op.f('ix_customers_company_name'))
        batch_op.drop_index(batch_op.f('ix_customers_analyzed_at'))

    op.drop_table('customers')
    with op.batch_alter_table('analysis_cache', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_analysis_cache_website'))
        batch_op.drop_index(batch_op.f('ix_analysis_cache_id'))

    op.drop_table('analysis_cache')
    

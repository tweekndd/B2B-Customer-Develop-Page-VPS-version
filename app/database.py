"""
数据库配置与模型定义
V2.2：Customer 新增跟进状态（status/follow_up_date/notes）
       新增抓取/分析状态字段（scrape_status/ai_status/fail_reason）
V2.6：支持 PostgreSQL 通过 DATABASE_URL 环境变量切换
"""
import os
import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Date, Float, UniqueConstraint, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# 数据库连接：优先使用环境变量 DATABASE_URL，否则回退到 SQLite
_DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if _DATABASE_URL:
    # PostgreSQL 或其他外部数据库（由环境变量控制）
    DATABASE_URL = _DATABASE_URL
    _engine_kwargs = {"pool_pre_ping": True}
else:
    # SQLite 本地文件（默认）
    DATABASE_URL = "sqlite:///./app/customers.db"
    _engine_kwargs = {"connect_args": {"check_same_thread": False}}

engine = create_engine(DATABASE_URL, **_engine_kwargs)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Customer(Base):
    """客户数据模型（V2.0 新增发现来源字段）"""
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    company_name = Column(String(255), nullable=False, index=True, comment="公司名称")
    website = Column(String(500), nullable=True, comment="公司官网")
    country = Column(String(100), nullable=True, comment="国家")

    # V2.0 新增：发现来源记录
    discovery_source = Column(String(50), nullable=True, index=True, comment="发现来源（Google / Manual Import）")
    discovery_keyword = Column(String(200), nullable=True, comment="发现时使用的关键词")
    first_found_at = Column(DateTime, nullable=True, comment="首次发现时间")

    # 邮箱与官网内容
    emails = Column(Text, nullable=True, comment="提取的邮箱列表（JSON格式）")
    website_text = Column(Text, nullable=True, comment="官网爬取的纯文本内容")
    positive_keywords = Column(Text, nullable=True, comment="命中的正向关键词及次数（JSON格式）")
    negative_keywords = Column(Text, nullable=True, comment="命中的负向关键词及次数（JSON格式）")

    # 规则评分引擎字段
    industry_score = Column(Integer, nullable=True, comment="行业匹配度 0-30")
    project_score = Column(Integer, nullable=True, comment="项目匹配度 0-25")
    company_type_score = Column(Integer, nullable=True, comment="公司类型 0-20")
    country_score = Column(Integer, nullable=True, comment="国家优先级 0-15")
    contact_score = Column(Integer, nullable=True, comment="联系方式完整度 0-10")
    total_score = Column(Integer, nullable=True, index=True, comment="总分 0-100")
    priority = Column(String(1), nullable=True, index=True, comment="优先级 A/B/C/D")

    # AI分析字段
    company_type = Column(String(50), nullable=True, comment="AI分析的公司类型")
    ai_summary = Column(Text, nullable=True, comment="AI生成的150字以内摘要（英文）")
    sales_hook = Column(Text, nullable=True, comment="推荐开发切入点（中文）")
    target_position = Column(Text, nullable=True, comment="推荐联系职位（中文）")
    identified_projects = Column(Text, nullable=True, comment="AI识别的项目信息（JSON格式）")
    ai_raw_json = Column(Text, nullable=True, comment="AI返回的原始JSON数据")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, comment="创建时间")
    analyzed_at = Column(DateTime, nullable=True, index=True, comment="分析完成时间")

    # V2.2 新增：客户跟进状态
    status = Column(String(20), default="待联系", index=True, comment="跟进状态: 待联系/已发邮件/已回复/无效线索/成单")
    follow_up_date = Column(Date, nullable=True, comment="下次跟进日期")
    notes = Column(Text, nullable=True, comment="跟进备注")

    # V2.2 新增：抓取/分析状态（用于失败可视化）
    scrape_status = Column(String(20), nullable=True, comment="官网抓取状态: success/failed/partial/skipped")
    ai_status = Column(String(20), nullable=True, comment="AI分析状态: success/failed/skipped")
    fail_reason = Column(String(500), nullable=True, comment="失败原因描述")

    # V2.2 新增：客户自定义评级（1-5星，0=未评级）
    star_rating = Column(Integer, default=0, comment="客户评级: 0未评级/1-5星")

    # V3.2.5 新增：城市字段（V3.2.6 Firecrawl 降级集成）
    city = Column(String(200), nullable=True, comment="城市")

    # V4.6 新增：买家意向评分（AI返回 0-10）与价格询盘标记（评分分级移植）
    buyer_intent_score = Column(Integer, nullable=True, comment="AI买家意向评分 0-10")
    is_price_inquiry = Column(Integer, nullable=True, default=0, comment="是否价格询盘: 1是/0否")
    # V4.6 新增：AI 生成的开发信草稿（JSON: subject/body/language）
    email_draft = Column(Text, nullable=True, comment="AI生成的开发信草稿（JSON格式）")
    # V3.2.4 新增：Geocoding 地理编码字段
    latitude = Column(Float, nullable=True, default=None, comment="纬度")
    longitude = Column(Float, nullable=True, default=None, comment="经度")
    geocode_status = Column(String(20), default="pending", comment="地理编码状态: pending/done/failed")

    # V5.0 新增：邮箱关系（规范化到独立表）
    email_records = relationship("CustomerEmail", back_populates="customer", cascade="all, delete-orphan")


class CustomerEmail(Base):
    """客户邮箱（V5.0 新增，从 customers.emails JSON 规范化为独立表）"""
    __tablename__ = "customer_emails"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True, comment="关联客户ID")
    email = Column(String(255), nullable=False, index=True, comment="邮箱地址")
    local_part = Column(String(255), nullable=True, comment="邮箱用户名部分")
    domain = Column(String(255), nullable=True, index=True, comment="邮箱域名")
    source = Column(String(30), nullable=True, comment="来源: website/hunter/tomba/prospeo/manual/legacy")
    source_detail = Column(String(255), nullable=True, comment="来源详情（搜索任务ID/第三方查询类型/用户备注）")
    first_name = Column(String(100), nullable=True, comment="名")
    last_name = Column(String(100), nullable=True, comment="姓")
    position = Column(String(200), nullable=True, comment="职位")
    department = Column(String(100), nullable=True, comment="部门")
    phone = Column(String(50), nullable=True, comment="电话")
    linkedin = Column(String(500), nullable=True, comment="LinkedIn URL")
    score = Column(Integer, default=0, comment="置信度分数 0-100")
    verification = Column(String(30), nullable=True, comment="验证状态: valid/invalid/unknown")
    notes = Column(Text, nullable=True, comment="用户备注")
    created_by_user_id = Column(Integer, nullable=True, comment="手动新增者用户ID")
    is_primary = Column(Integer, default=0, comment="是否主要联系邮箱: 1是/0否")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, comment="更新时间")

    customer = relationship("Customer", back_populates="email_records")

    __table_args__ = (
        UniqueConstraint("customer_id", "email", name="uq_customer_email"),
    )


class CustomerSocialProfile(Base):
    """客户社交主页（V5.1 新增，LinkedIn 公司主页候选与确认）

    同一客户可保留多个候选，但最多一个 is_verified=1 的主页。
    """
    __tablename__ = "customer_social_profiles"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True, comment="关联客户ID")
    platform = Column(String(30), default="linkedin", comment="平台: linkedin")
    profile_type = Column(String(30), default="company", comment="主页类型: company")
    profile_url = Column(String(500), nullable=False, comment="标准化后的公开 URL")
    vanity_name = Column(String(200), nullable=True, comment="LinkedIn vanity name")
    external_id = Column(String(200), nullable=True, comment="平台组织ID/URN（官方 API 时）")
    display_name = Column(String(300), nullable=True, comment="平台显示名称")
    website_url = Column(String(500), nullable=True, comment="平台返回的官网")
    logo_url = Column(String(500), nullable=True, comment="Logo 地址，可选")
    location_json = Column(Text, nullable=True, comment="地点 JSON")
    staff_count_range = Column(String(50), nullable=True, comment="员工规模范围，可选")
    source = Column(String(30), default="search", comment="来源: search/manual/official_api")
    confidence = Column(Float, default=0.0, comment="候选置信度 0-100")
    is_verified = Column(Integer, default=0, comment="用户是否确认: 1是/0否")
    last_fetched_at = Column(DateTime, nullable=True, comment="最近抓取时间")
    raw_json = Column(Text, nullable=True, comment="原始 API 结果（脱敏后）")
    created_by_user_id = Column(Integer, nullable=True, comment="手动新增者用户ID")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, comment="更新时间")

    __table_args__ = (
        UniqueConstraint("customer_id", "platform", "profile_type", "profile_url",
                         name="uq_customer_social_profile"),
    )


class User(Base):
    """用户认证（V4.0 新增 / V4.1 新增权限字段）"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True, comment="用户名")
    password_hash = Column(String(255), nullable=False, comment="密码哈希（bcrypt）")
    role = Column(String(20), default="user", comment="角色: admin/user")
    is_active = Column(Integer, default=1, comment="是否激活: 1激活/0禁用")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, comment="更新时间")

    # V4.1 新增：用户功能权限（管理员在用户管理页可逐用户配置）
    search_depth_limit = Column(Integer, default=50, comment="每次搜索允许的最大结果数")
    search_quota = Column(Integer, default=100, comment="搜索次数总配额")
    searches_used = Column(Integer, default=0, comment="已使用的搜索次数")
    ai_analysis_enabled = Column(Integer, default=1, comment="是否允许 AI 分析: 1允许/0禁止")
    email_finding_enabled = Column(Integer, default=1, comment="是否允许邮箱查找: 1允许/0禁止")


class SearchTask(Base):
    """搜索任务（V2.0 新增）"""
    __tablename__ = "search_tasks"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    country = Column(String(100), nullable=False, comment="搜索国家")
    keyword = Column(String(200), nullable=False, comment="原始关键词")
    expanded_keywords = Column(Text, nullable=True, comment="AI扩展的关键词列表（JSON数组）")
    search_depth = Column(Integer, default=50, comment="每个关键词期望搜索数量")
    # Round 3 新增：创建任务的用户（后台任务按此解析用户自己的 API Key）
    user_id = Column(Integer, nullable=True, index=True, comment="创建任务的用户ID")
    status = Column(String(20), default="Pending", comment="任务状态: Pending/Running/Completed/Failed/Paused")
    found_websites = Column(Integer, default=0, comment="发现的网站数量")
    analyzed_companies = Column(Integer, default=0, comment="已分析的公司数量")
    new_companies = Column(Integer, default=0, comment="新增公司数量")
    current_keyword_index = Column(Integer, default=0, comment="当前处理到第几个扩展关键词（断点续跑）")
    error_message = Column(Text, nullable=True, comment="失败时的错误信息")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, comment="创建时间")
    finished_at = Column(DateTime, nullable=True, comment="完成时间")
    task_log = Column(Text, nullable=True, comment="任务运行日志")


class SearchCache(Base):
    """搜索结果缓存（V2.0 新增，避免重复搜索相同关键词）"""
    __tablename__ = "search_cache"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    keyword = Column(String(200), nullable=False, index=True, comment="搜索关键词")
    country = Column(String(100), nullable=False, index=True, comment="搜索国家")
    website = Column(String(500), nullable=False, comment="发现的企业官网")
    title = Column(String(500), nullable=True, comment="搜索结果标题")
    snippet = Column(Text, nullable=True, comment="搜索结果摘要")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, comment="缓存时间")


class WebsiteCache(Base):
    """官网抓取缓存（V2.0 新增，避免重复抓取相同网站）"""
    __tablename__ = "website_cache"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    website = Column(String(500), nullable=False, unique=True, index=True, comment="网站域名")
    content = Column(Text, nullable=True, comment="抓取的纯文本内容")
    content_hash = Column(String(64), nullable=True, comment="内容哈希值，用于判断内容是否变化")
    last_crawled = Column(DateTime, nullable=True, comment="上次抓取时间")


class HunterCache(Base):
    """Hunter API 查询缓存（V3.0 新增，避免重复消耗搜索/验证额度）"""
    __tablename__ = "hunter_cache"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    cache_key = Column(String(500), nullable=False, unique=True, index=True, comment="缓存唯一键: domain|type|params")
    domain = Column(String(255), nullable=False, index=True, comment="公司域名")
    query_type = Column(String(30), nullable=False, comment="查询类型: email_count/domain_search/email_finder/email_verifier")
    result = Column(Text, nullable=False, comment="API 返回结果 (JSON)")
    hits = Column(Integer, default=1, comment="缓存命中次数（辅助统计）")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, comment="创建时间")


class TombaCache(Base):
    """Tomba API 查询缓存（Phase 1 新增，避免重复消耗搜索额度）"""
    __tablename__ = "tomba_cache"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    cache_key = Column(String(500), nullable=False, unique=True, index=True, comment="缓存唯一键: domain|type|params")
    domain = Column(String(255), nullable=False, index=True, comment="公司域名")
    query_type = Column(String(30), nullable=False, comment="查询类型: domain_search/email_finder/email_verifier")
    result = Column(Text, nullable=False, comment="API 返回结果 (JSON)")
    hits = Column(Integer, default=1, comment="缓存命中次数（辅助统计）")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, comment="创建时间")


class EmailQuotaLog(Base):
    """邮箱发现配额使用日志（Phase 1 新增，持久化记录各平台配额消耗）"""
    __tablename__ = "email_quota_log"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    source = Column(String(30), nullable=False, comment="数据源: hunter/tomba/scraped")
    query_type = Column(String(30), nullable=False, comment="查询类型")
    domain = Column(String(255), nullable=False, comment="查询的域名")
    result_count = Column(Integer, default=0, comment="返回结果数量")
    credits_consumed = Column(Integer, default=0, comment="消耗的配额次数")
    success = Column(Integer, default=1, comment="是否成功: 1成功/0失败")
    error_message = Column(String(500), nullable=True, comment="错误信息")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, comment="记录时间")


class AnalysisCache(Base):
    """AI分析缓存（V2.0 新增，避免重复调用DeepSeek）"""
    __tablename__ = "analysis_cache"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    website = Column(String(500), nullable=False, index=True, comment="网站域名")
    content_hash = Column(String(64), nullable=True, comment="对应官网内容的哈希值")
    company_type = Column(String(50), nullable=True, comment="公司类型")
    summary = Column(Text, nullable=True, comment="英文摘要")
    sales_hook = Column(Text, nullable=True, comment="开发切入点")
    target_position = Column(Text, nullable=True, comment="推荐联系职位")
    analysis_reason = Column(Text, nullable=True, comment="分析原因")
    identified_projects = Column(Text, nullable=True, comment="识别的项目信息")
    raw_json = Column(Text, nullable=True, comment="AI返回的原始JSON")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, comment="缓存时间")


class GeocodeCache(Base):
    """地理编码结果缓存（V3.2.5 新增 → V3.2.6 Firecrawl 降级集成）"""
    __tablename__ = "geocode_cache"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    query_key = Column(String(500), nullable=False, unique=True, index=True, comment="查询键: city|Country 或 Country")
    country = Column(String(100), nullable=True, comment="国家")
    city = Column(String(200), nullable=True, comment="城市")
    latitude = Column(Float, nullable=False, comment="纬度")
    longitude = Column(Float, nullable=False, comment="经度")
    display_name = Column(String(500), nullable=True, comment="Nominatim 返回的完整地址名")
    hits = Column(Integer, default=1, comment="命中次数")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, comment="缓存时间")


class ProspeoCache(Base):
    """Prospeo API 查询缓存（V3.2.2 新增，避免重复消耗搜索额度）"""
    __tablename__ = "prospeo_cache"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    cache_key = Column(String(500), nullable=False, unique=True, index=True, comment="缓存唯一键: domain|type|params")
    domain = Column(String(255), nullable=False, index=True, comment="公司域名")
    query_type = Column(String(30), nullable=False, comment="查询类型: search_person/enrich_person")
    person_id = Column(String(100), nullable=True, comment="Enrich 时对应的人员 ID")
    result = Column(Text, nullable=False, comment="API 返回结果 (JSON)")
    hits = Column(Integer, default=1, comment="缓存命中次数（辅助统计）")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, comment="创建时间")


class UserApiConfig(Base):
    """用户 API Key 配置（Round 3 新增）

    统一存储用户自定义的外部 API Key（LLM / Hunter / Tomba / Prospeo /
    Tavily / SerpAPI / SearXNG / Firecrawl）。每个用户每个服务一行。
    API Key 使用 Fernet 对称加密存储，绝不保存明文。
    """
    __tablename__ = "user_api_config"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True, comment="用户ID（关联 users.id）")
    service = Column(String(30), nullable=False, index=True, comment="服务: llm/hunter/tomba/prospeo/tavily/serpapi/searxng/firecrawl/search_engine")
    provider = Column(String(50), nullable=True, comment="LLM Provider 名称（glm/openai-compatible/deepseek/qwen/custom）")
    api_key = Column(Text, nullable=True, comment="API Key（Fernet 加密存储）")
    api_secret = Column(Text, nullable=True, comment="API Secret（Fernet 加密存储，如 Tomba）")
    base_url = Column(String(500), nullable=True, comment="Base URL / SearXNG URL / Reader URL / 偏好的搜索引擎名")
    default_model = Column(String(100), nullable=True, comment="默认模型（LLM）")
    fallback_models = Column(Text, nullable=True, comment="备用模型列表（JSON数组，LLM）")
    enabled = Column(Integer, default=1, comment="是否启用: 1启用/0禁用")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, comment="更新时间")

    __table_args__ = (
        UniqueConstraint("user_id", "service", name="uq_user_api_config"),
    )


class LinkedInOAuthToken(Base):
    """LinkedIn OAuth 令牌（V5.1 新增，3-legged 授权）

    每个用户一行。access_token 为敏感字段，使用与 user_api_config
    相同的 Fernet 加密存储，绝不保存明文。
    """
    __tablename__ = "linkedin_oauth_tokens"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, unique=True, index=True, comment="用户ID（关联 users.id）")
    access_token_encrypted = Column(Text, nullable=False, comment="access token（Fernet 加密存储）")
    scope = Column(String(255), nullable=True, comment="授权 scope")
    expires_at = Column(DateTime, nullable=True, comment="token 过期时间")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, comment="更新时间")


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


def get_db():
    """获取数据库会话的生成器函数"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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
        "idx_customer_emails_customer_id": "CREATE INDEX IF NOT EXISTS idx_customer_emails_customer_id ON customer_emails(customer_id)",
        "idx_customer_emails_email": "CREATE INDEX IF NOT EXISTS idx_customer_emails_email ON customer_emails(email)",
        "idx_customer_emails_domain": "CREATE INDEX IF NOT EXISTS idx_customer_emails_domain ON customer_emails(domain)",
        "idx_social_profiles_customer": "CREATE INDEX IF NOT EXISTS idx_social_profiles_customer ON customer_social_profiles(customer_id, platform)",
        "idx_mail_accounts_user": "CREATE INDEX IF NOT EXISTS idx_mail_accounts_user ON mail_accounts(user_id)",
        "idx_mail_activities_customer": "CREATE INDEX IF NOT EXISTS idx_mail_activities_customer ON customer_email_activities(customer_id, sent_at)",
        "idx_mail_activities_message": "CREATE INDEX IF NOT EXISTS idx_mail_activities_message ON customer_email_activities(provider_message_id)",
    }
    with engine.connect() as conn:
        for name, ddl in indexes.items():
            try:
                conn.execute(sa.text(ddl))
            except Exception as e:
                print(f"  索引创建跳过 {name}: {e}")
        conn.commit()


def init_db():
    """初始化数据库：创建所有表 + 自动迁移新增列（V2.2 支持）"""
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

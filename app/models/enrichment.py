"""数据富化与缓存模型：抓取/AI/邮箱发现缓存 + 配额日志（V5.3 拆分）"""
import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime

from app.core.database import Base


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

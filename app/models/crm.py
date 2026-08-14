"""CRM 主数据模型：Customer 主档 + CustomerEmail（V5.3 拆分）"""
import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, Date, Float, UniqueConstraint, ForeignKey
from sqlalchemy.orm import relationship

from app.core.database import Base


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
    emails = Column(Text, nullable=True, comment="提取的邮箱列表（JSON格式，V5.1 起为兼容视图）")
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

    # V5.2 新增：最近发信时间（Gmail 发信检测自动回填）
    last_email_sent_at = Column(DateTime, nullable=True, index=True, comment="最近发信时间（Gmail 检测到发信后自动更新）")

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

"""智能分析模型：官网快照 / AI 分析运行 / 评分快照（V5.3 拆表）

把 Customer 主表中的可变大字段（website_text / ai_raw_json / 分数）
沉淀为不可变的历史运行记录，主表保留最新投影（双写兼容期）。
"""
import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, Float, ForeignKey

from app.core.database import Base


class WebsiteSnapshot(Base):
    """官网抓取快照（V5.3 新增，一次抓取一条，历史可追溯）"""
    __tablename__ = "website_snapshots"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True, comment="关联客户ID")
    website = Column(String(500), nullable=True, comment="抓取的域名")
    content = Column(Text, nullable=True, comment="官网纯文本内容")
    content_hash = Column(String(64), nullable=True, index=True, comment="内容哈希（去重用）")
    scrape_status = Column(String(20), nullable=True, comment="抓取状态: success/failed/partial/skipped")
    source = Column(String(30), default="pipeline", comment="来源: pipeline/manual/backfill")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True, comment="抓取时间")


class AnalysisRun(Base):
    """AI 分析运行记录（V5.3 新增，一次 AI 调用一条，失败不覆盖历史成功）"""
    __tablename__ = "analysis_runs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True, comment="关联客户ID")
    website_snapshot_id = Column(Integer, nullable=True, index=True, comment="基于哪次抓取快照")
    content_hash = Column(String(64), nullable=True, comment="分析内容的哈希")
    provider = Column(String(50), nullable=True, comment="LLM Provider（glm/deepseek/...）")
    model = Column(String(100), nullable=True, comment="使用的模型")
    status = Column(String(20), default="success", comment="success/failed")
    company_type = Column(String(50), nullable=True, comment="公司类型")
    summary = Column(Text, nullable=True, comment="英文摘要")
    sales_hook = Column(Text, nullable=True, comment="开发切入点")
    target_position = Column(Text, nullable=True, comment="推荐联系职位")
    identified_projects = Column(Text, nullable=True, comment="项目信息（JSON）")
    analysis_reason = Column(Text, nullable=True, comment="分析原因")
    buyer_intent_score = Column(Integer, nullable=True, comment="买家意向评分 0-10")
    is_price_inquiry = Column(Integer, nullable=True, default=0, comment="是否价格询盘")
    address_city = Column(String(200), nullable=True, comment="AI 识别城市")
    needs_identified = Column(Text, nullable=True, comment="客户需求清单（JSON）")
    product_match = Column(String(500), nullable=True, comment="产品匹配关键词")
    raw_json = Column(Text, nullable=True, comment="AI 原始返回 JSON")
    error_message = Column(Text, nullable=True, comment="失败原因")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True, comment="分析时间")


class ScoreSnapshot(Base):
    """评分快照（V5.3 新增，一次评分一条，规则变更后可追溯）"""
    __tablename__ = "score_snapshots"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True, comment="关联客户ID")
    analysis_run_id = Column(Integer, nullable=True, index=True, comment="关联的分析运行（可选）")
    industry_score = Column(Integer, nullable=True, comment="行业匹配度 0-30")
    project_score = Column(Integer, nullable=True, comment="项目匹配度 0-25")
    company_type_score = Column(Integer, nullable=True, comment="公司类型 0-20")
    country_score = Column(Integer, nullable=True, comment="国家优先级 0-15")
    contact_score = Column(Integer, nullable=True, comment="联系方式 0-10")
    total_score = Column(Integer, nullable=True, comment="总分 0-100")
    priority = Column(String(1), nullable=True, comment="优先级 A/B/C/D")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True, comment="评分时间")

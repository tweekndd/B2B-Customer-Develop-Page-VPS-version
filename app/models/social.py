"""社交情报模型：CustomerSocialProfile（V5.3 拆分）"""
import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, Float, UniqueConstraint, ForeignKey

from app.core.database import Base


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

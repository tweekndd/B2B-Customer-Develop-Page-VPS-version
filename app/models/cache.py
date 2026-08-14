"""地理编码缓存模型：GeocodeCache（V5.3 拆分）"""
import datetime

from sqlalchemy import Column, Integer, String, Float, DateTime

from app.core.database import Base


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

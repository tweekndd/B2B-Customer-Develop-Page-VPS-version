"""对象存储索引模型：StorageObject（Phase0）

业务表不直接保存大体积原文/附件，而是外置到对象存储（默认本地 DATA_DIR/objects，
可扩展 S3 等 Provider），数据库仅保存对象索引。对应方案 5.3「大字段外置」。
"""
import datetime

from sqlalchemy import Column, Integer, String, DateTime

from app.core.database import Base


class StorageObject(Base):
    """对象存储索引（Phase0 新增）

    object_key  全局唯一对象键（默认 sha256(content)[:32]，内容寻址可去重）
    content_hash sha256(content) 完整哈希（校验用）
    """
    __tablename__ = "storage_objects"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    object_key = Column(String(128), nullable=False, unique=True, index=True, comment="对象键（默认内容哈希）")
    content_hash = Column(String(64), nullable=False, index=True, comment="sha256 完整哈希")
    content_type = Column(String(128), nullable=True, comment="MIME 类型")
    size_bytes = Column(Integer, nullable=False, default=0, comment="字节大小")
    storage_provider = Column(String(30), default="local", comment="存储 Provider: local/s3/...")
    retention_until = Column(DateTime, nullable=True, comment="保留截止时间，空=长期保留")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, comment="创建时间")

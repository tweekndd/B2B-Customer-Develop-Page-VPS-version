"""
数据库基础设施（V5.3 重构：从 database.py 拆出）

只负责 Engine / SessionLocal / Base / get_db，不放业务模型。
业务模型定义在 app/models/，通过 app/database.py 兼容层统一导出。
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

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


def get_db():
    """获取数据库会话的生成器函数"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

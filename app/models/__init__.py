"""
业务模型聚合（V5.3 重构：从 database.py 拆出）

显式导入全部模型，确保 Base.metadata 注册完整（create_all 不漏表）。
"""
from app.core.database import Base, engine, SessionLocal, get_db  # noqa: F401

from .crm import Customer, CustomerEmail  # noqa: F401
from .discovery import SearchTask, SearchCache  # noqa: F401
from .enrichment import (  # noqa: F401
    WebsiteCache,
    HunterCache,
    TombaCache,
    EmailQuotaLog,
    AnalysisCache,
    ProspeoCache,
)
from .social import CustomerSocialProfile  # noqa: F401
from .intelligence import WebsiteSnapshot, AnalysisRun, ScoreSnapshot  # noqa: F401
from .outreach import MailAccount, CustomerEmailActivity  # noqa: F401
from .identity import User, UserApiConfig, LinkedInOAuthToken  # noqa: F401
from .cache import GeocodeCache  # noqa: F401


def init_db():
    """初始化数据库：建表 + 索引 + 自动迁移（见 app.core.db_migrations）"""
    from app.core.db_migrations import init_db as _run_migrations
    _run_migrations()

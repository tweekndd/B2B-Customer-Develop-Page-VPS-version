"""
数据库兼容层（V5.3 重构）

原有 `app/database.py` 的模型与基础设施已按业务域拆分：
- 基础设施（Engine/Session/Base/get_db）→ app/core/database.py
- 迁移与初始化 → app/core/db_migrations.py
- 业务模型 → app/models/{crm,discovery,enrichment,social,outreach,identity,cache}.py

本文件保留全部旧导出，确保现有 `from app.database import ...` 零改动。
"""
# 基础设施
from app.core.database import (  # noqa: F401
    engine,
    SessionLocal,
    Base,
    get_db,
    DATABASE_URL,
)

# 业务模型（经由 app.models 聚合导入，保证 metadata 注册完整）
from app.models import (  # noqa: F401
    Customer,
    CustomerEmail,
    CustomerSocialProfile,
    WebsiteSnapshot,
    AnalysisRun,
    ScoreSnapshot,
    SearchTask,
    SearchCache,
    WebsiteCache,
    HunterCache,
    TombaCache,
    EmailQuotaLog,
    AnalysisCache,
    GeocodeCache,
    ProspeoCache,
    User,
    UserApiConfig,
    LinkedInOAuthToken,
    MailAccount,
    CustomerEmailActivity,
    init_db,
)

# 兼容旧版内部符号（历史 import 路径）
from app.core.db_migrations import (  # noqa: F401
    _ensure_indexes,
    _migrate_add_column,
    _migrate_email_normalization,
)

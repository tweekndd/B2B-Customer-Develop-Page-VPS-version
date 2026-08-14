"""身份与授权模型：User / UserApiConfig / LinkedInOAuthToken（V5.3 拆分）"""
import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, UniqueConstraint

from app.core.database import Base


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


class UserApiConfig(Base):
    """用户 API Key 配置（Round 3 新增）

    统一存储用户自定义的外部 API Key（LLM / Hunter / Tomba / Prospeo /
    Tavily / SerpAPI / SearXNG / Firecrawl / LinkedIn / Gmail）。每个用户每个服务一行。
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

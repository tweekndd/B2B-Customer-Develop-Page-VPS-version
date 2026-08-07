"""
Round 3 用户 API 配置 — 服务层单元测试
覆盖：Fernet 加解密、环境变量回退、用户优先解析、搜索引擎偏好优先级

说明：仅测试服务层（app.services.user_config），使用独立内存 SQLite，
     不导入 FastAPI app，避免与 test_api_integration 的 get_db 覆盖冲突。
     API 层测试见 test_api_integration.py::TestUserConfigAPI。
"""
import os
import sys
import json

import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, User
from app.auth import hash_password
from app.services import user_config as uc


_engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


@pytest.fixture(autouse=True)
def setup_db():
    """每个测试前重建表"""
    Base.metadata.create_all(bind=_engine)
    yield
    Base.metadata.drop_all(bind=_engine)


def _create_user(db, username: str, role: str = "user") -> User:
    u = User(
        username=username,
        password_hash=hash_password("pw123456"),
        role=role,
        is_active=1,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


# ═══════════════════════════════════════════════
# 加解密
# ═══════════════════════════════════════════════

class TestEncryption:
    def test_fernet_roundtrip(self):
        secret = "sk-abcdef-123456"
        token = uc.encrypt_secret(secret)
        assert token
        assert token != secret
        assert uc.decrypt_secret(token) == secret

    def test_encrypt_empty(self):
        assert uc.encrypt_secret("") == ""

    def test_decrypt_invalid(self):
        assert uc.decrypt_secret("not-a-valid-token") == ""

    def test_mask_secret(self):
        assert uc.mask_secret("abcdef1234") == "****1234"
        assert uc.mask_secret("abc") == "****"
        assert uc.mask_secret("") == ""


# ═══════════════════════════════════════════════
# 生效配置解析（用户优先，环境回退）
# ═══════════════════════════════════════════════

class TestEffectiveConfig:
    def test_env_fallback(self, monkeypatch):
        monkeypatch.setenv("HUNTER_API_KEY", "env-hunter-key")
        monkeypatch.setenv("TOMBA_API_KEY", "env-tomba-key")
        monkeypatch.setenv("TOMBA_API_SECRET", "env-tomba-secret")
        monkeypatch.delenv("SERPAPI_API_KEY", raising=False)

        db = SessionLocal()
        assert uc.get_effective_api_key(db, 1, uc.SERVICE_HUNTER) == "env-hunter-key"
        assert uc.get_effective_api_key(db, 1, uc.SERVICE_TOMBA) == "env-tomba-key"
        assert uc.get_effective_api_secret(db, 1, uc.SERVICE_TOMBA) == "env-tomba-secret"
        assert uc.get_effective_api_key(db, 1, uc.SERVICE_SERPAPI) == ""
        db.close()

    def test_user_config_overrides_env(self, monkeypatch):
        monkeypatch.setenv("HUNTER_API_KEY", "env-hunter-key")
        db = SessionLocal()
        user_a = _create_user(db, "alice")
        user_b = _create_user(db, "bob")

        uc.set_user_api_config(db, user_a.id, uc.SERVICE_HUNTER, api_key="user-a-key")

        # A 用自己配置的 Key，B 回退环境变量
        assert uc.get_effective_api_key(db, user_a.id, uc.SERVICE_HUNTER) == "user-a-key"
        assert uc.get_effective_api_key(db, user_b.id, uc.SERVICE_HUNTER) == "env-hunter-key"
        db.close()

    def test_disabled_user_config_falls_back(self, monkeypatch):
        monkeypatch.setenv("HUNTER_API_KEY", "env-hunter-key")
        db = SessionLocal()
        u = _create_user(db, "carol")
        uc.set_user_api_config(db, u.id, uc.SERVICE_HUNTER, api_key="user-key", enabled=False)
        assert uc.get_effective_api_key(db, u.id, uc.SERVICE_HUNTER) == "env-hunter-key"
        db.close()

    def test_delete_config_restores_env(self, monkeypatch):
        monkeypatch.setenv("HUNTER_API_KEY", "env-hunter-key")
        db = SessionLocal()
        u = _create_user(db, "dave")
        uc.set_user_api_config(db, u.id, uc.SERVICE_HUNTER, api_key="user-key")
        assert uc.get_effective_api_key(db, u.id, uc.SERVICE_HUNTER) == "user-key"
        assert uc.delete_user_api_config(db, u.id, uc.SERVICE_HUNTER) is True
        assert uc.delete_user_api_config(db, u.id, uc.SERVICE_HUNTER) is False
        assert uc.get_effective_api_key(db, u.id, uc.SERVICE_HUNTER) == "env-hunter-key"
        db.close()

    def test_llm_env_alias(self, monkeypatch):
        """LLM 兼容旧的 DEEPSEEK_API_KEY"""
        monkeypatch.delenv("GLM_API_KEY", raising=False)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
        db = SessionLocal()
        assert uc.get_effective_api_key(db, 1, uc.SERVICE_LLM) == "deepseek-key"
        db.close()

    def test_encrypted_at_rest(self):
        """数据库里存的必须是密文，不允许明文"""
        db = SessionLocal()
        u = _create_user(db, "eve")
        uc.set_user_api_config(db, u.id, uc.SERVICE_HUNTER, api_key="top-secret-key")
        row = uc.get_user_api_config(db, u.id, uc.SERVICE_HUNTER)
        assert row.api_key
        assert "top-secret-key" not in row.api_key
        assert "top-secret-key" not in json.dumps(row.api_key)
        db.close()


# ═══════════════════════════════════════════════
# 搜索引擎配置优先级
# ═══════════════════════════════════════════════

class TestSearchConfig:
    def test_global_fallback(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "env-tavily")
        db = SessionLocal()
        sc = uc.resolve_search_config(db, None, global_engine="none")
        assert sc["engine"] == "none"
        assert sc["source"] == "global"
        assert sc["available"]["tavily"] is True
        sc2 = uc.resolve_search_config(db, None, global_engine="tavily")
        assert sc2["engine"] == "tavily"
        db.close()

    def test_user_preferred_engine_wins(self):
        db = SessionLocal()
        u = _create_user(db, "frank")
        uc.set_user_api_config(db, u.id, uc.SERVICE_TAVILY, api_key="user-tavily")
        uc.set_user_api_config(db, u.id, uc.SERVICE_SERPAPI, api_key="user-serpapi")
        # 用户偏好 tavily，且已配置 → 使用 tavily
        uc.set_user_api_config(db, u.id, uc.SERVICE_SEARCH_ENGINE, base_url="tavily")
        sc = uc.resolve_search_config(db, u.id, global_engine="none")
        assert sc["engine"] == "tavily"
        assert sc["source"] == "user"
        assert sc["tavily_key"] == "user-tavily"
        db.close()

    def test_user_preferred_unavailable_falls_back(self):
        """偏好 searxng 但用户只配了 serpapi → 自动降级到 serpapi"""
        db = SessionLocal()
        u = _create_user(db, "grace")
        uc.set_user_api_config(db, u.id, uc.SERVICE_SERPAPI, api_key="user-serpapi")
        uc.set_user_api_config(db, u.id, uc.SERVICE_SEARCH_ENGINE, base_url="searxng")
        sc = uc.resolve_search_config(db, u.id, global_engine="none")
        assert sc["engine"] == "serpapi"
        assert sc["source"] == "user"
        assert sc["serpapi_key"] == "user-serpapi"
        db.close()

    def test_user_without_config_uses_global(self, monkeypatch):
        monkeypatch.setenv("SERPAPI_API_KEY", "env-serpapi")
        db = SessionLocal()
        u = _create_user(db, "heidi")
        sc = uc.resolve_search_config(db, u.id, global_engine="serpapi")
        assert sc["engine"] == "serpapi"
        assert sc["source"] == "global"
        assert sc["serpapi_key"] == "env-serpapi"
        db.close()

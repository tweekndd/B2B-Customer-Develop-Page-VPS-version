"""
LinkedIn OAuth 2.0 与 Organizations Lookup API 测试（V5.1）
覆盖：凭据保存/回退、授权 URL 构造、token 兑换（mock）、token 加密存取、
Lookup API 解析、resolve 端点、oauth start/callback/disconnect、状态
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.database import (
    Base, get_db, Customer, CustomerSocialProfile, LinkedInOAuthToken, UserApiConfig,
)
from app.auth import hash_password
from main import app


_TEST_DB = os.path.join(os.path.dirname(__file__), "test_api.db")
TEST_DATABASE_URL = f"sqlite:///{_TEST_DB}"
_test_engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_test_engine)


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=_test_engine)
    yield
    Base.metadata.drop_all(bind=_test_engine)


@pytest.fixture(autouse=True)
def login_user(setup_db):
    db = TestSessionLocal()
    try:
        from app.database import User
        db.add(User(
            username="testadmin",
            password_hash=hash_password("testpassword"),
            role="admin",
            is_active=1,
        ))
        db.commit()
    finally:
        db.close()
    resp = client.post(
        "/api/auth/login",
        json={"username": "testadmin", "password": "testpassword"},
    )
    assert resp.status_code == 200, resp.text
    yield
    client.post("/api/auth/logout")


def _save_credentials(db: Session, client_id="86testclient", secret="WPL_AP1.testsecret"):
    """直接写 user_api_config（绕过 API，模拟已保存）"""
    from app.services.user_config import set_user_api_config, SERVICE_LINKEDIN
    set_user_api_config(db, 1, SERVICE_LINKEDIN, api_key=client_id, api_secret=secret, enabled=True)


def _save_token(db: Session, user_id=1, token="test-access-token", expires_in=86400):
    from app.services.linkedin_oauth_service import save_access_token
    save_access_token(db, user_id, token, scope="r_organization_social", expires_in=expires_in)


class TestCredentials:
    def test_save_and_read_user_config(self):
        """设置页保存 client id/secret → 加密存储 → 脱敏回读"""
        db = TestSessionLocal()
        _save_credentials(db)
        db.close()

        resp = client.get("/api/user-config/linkedin")
        assert resp.status_code == 200
        data = resp.json()
        assert data["api_key_set"] is True
        assert data["api_secret_set"] is True
        assert data["api_key"].startswith("****")
        assert data["api_key"].endswith("ient")
        assert data["effective"]["configured"] is True

        # 列表接口（与设置页一致）验证来源为 user
        resp = client.get("/api/user-config/")
        assert resp.status_code == 200
        eff = resp.json()["effective"]["linkedin"]
        assert eff["source"] == "user"
        assert eff["configured"] is True

    def test_credentials_env_fallback(self):
        """未配置用户 → 回退环境变量"""
        os.environ["LINKEDIN_CLIENT_ID"] = "env-client-id"
        os.environ["LINKEDIN_CLIENT_SECRET"] = "env-client-secret"
        try:
            from app.services.linkedin_oauth_service import get_client_credentials
            db = TestSessionLocal()
            cid, secret = get_client_credentials(db, 1)
            assert cid == "env-client-id"
            assert secret == "env-client-secret"
            db.close()
        finally:
            os.environ.pop("LINKEDIN_CLIENT_ID", None)
            os.environ.pop("LINKEDIN_CLIENT_SECRET", None)

    def test_auth_url_contains_params(self):
        """授权 URL 包含正确参数"""
        from app.services.linkedin_oauth_service import build_authorization_url
        url = build_authorization_url("cid123", "https://example.com/cb", "state_xyz")
        assert url.startswith("https://www.linkedin.com/oauth/v2/authorization?")
        assert "client_id=cid123" in url
        assert "response_type=code" in url
        assert "state=state_xyz" in url
        assert "scope=r_organization_social" in url
        assert "redirect_uri=https%3A%2F%2Fexample.com%2Fcb" in url


class TestTokenStorage:
    def test_save_get_delete_token(self):
        """token 加密存储、读取、过期判断、删除"""
        from app.services.linkedin_oauth_service import (
            get_access_token, delete_access_token,
        )
        db = TestSessionLocal()
        _save_token(db)
        assert get_access_token(db, 1) == "test-access-token"

        # 密文存储（非明文）
        row = db.query(LinkedInOAuthToken).filter(LinkedInOAuthToken.user_id == 1).first()
        assert row.access_token_encrypted != "test-access-token"
        assert "test-access-token" not in row.access_token_encrypted

        # 过期
        row.expires_at = datetime.datetime.utcnow() - datetime.timedelta(hours=1)
        db.commit()
        assert get_access_token(db, 1) is None

        # 删除
        assert delete_access_token(db, 1) is True
        assert get_access_token(db, 1) is None
        db.close()

    def test_oauth_status(self):
        """状态：未授权 / 已授权"""
        from app.services.linkedin_oauth_service import get_oauth_status
        db = TestSessionLocal()
        _save_credentials(db)
        st = get_oauth_status(db, 1)
        assert st["client_configured"] is True
        assert st["authorized"] is False

        _save_token(db)
        st = get_oauth_status(db, 1)
        assert st["authorized"] is True
        assert st["expires_at"] is not None
        db.close()


class TestLookupAPI:
    def test_exchange_code_ok(self, monkeypatch):
        """授权码兑换成功"""
        from app.services import linkedin_oauth_service as los

        class FakeResp:
            status_code = 200
            text = ""
            def json(self):
                return {"access_token": "tok123", "expires_in": 86400, "scope": "r_organization_social"}

        def fake_post(url, data, timeout=None):
            assert url == los.ACCESS_TOKEN_URL
            assert data["grant_type"] == "authorization_code"
            assert data["client_id"] == "cid"
            assert data["client_secret"] == "sec"
            assert data["code"] == "code123"
            return FakeResp()

        monkeypatch.setattr("httpx.post", fake_post)
        data = los.exchange_code_for_token("code123", "cid", "sec", "https://x/cb")
        assert data["access_token"] == "tok123"

    def test_exchange_code_failure(self, monkeypatch):
        """授权码兑换失败（错误凭据）"""
        from app.services import linkedin_oauth_service as los
        from app.services.linkedin_oauth_service import LinkedInOAuthError

        class FakeResp:
            status_code = 401
            text = "invalid client"
            def json(self):
                return {}

        def fake_post(url, data, timeout=None):
            return FakeResp()

        monkeypatch.setattr("httpx.post", fake_post)
        with pytest.raises(LinkedInOAuthError) as exc:
            los.exchange_code_for_token("code", "bad", "bad", "https://x/cb")
        assert exc.value.status_code == 400

    def test_lookup_parsing(self):
        """Lookup 元素解析：名称/URN/员工规模/官网/Logo"""
        from app.services.linkedin_oauth_service import _parse_lookup_result
        raw = {
            "organizationUrn": "urn:li:organization:12345",
            "localizedName": "AquaTech Solutions",
            "vanityName": "aquatech-solutions",
            "staffCountRange": {"start": 1001, "end": 5000},
            "locations": [{"address": {"city": "Riyadh"}}],
            "logoV2": {"original": "urn:li:digitalmediaAsset:D4D1AQ"},
            "localizedWebsite": {"localized": {"en_US": "https://aquatech-solutions.com"}},
        }
        result = _parse_lookup_result(raw)
        assert result["external_id"] == "urn:li:organization:12345"
        assert result["display_name"] == "AquaTech Solutions"
        assert result["vanity_name"] == "aquatech-solutions"
        assert result["staff_count_range"] == "1001-5000"
        assert result["website_url"] == "https://aquatech-solutions.com"
        assert result["logo_url"] == "urn:li:digitalmediaAsset:D4D1AQ"
        assert result["location_json"]

    def test_lookup_api_call(self, monkeypatch):
        """Lookup API 请求构造与响应解析"""
        from app.services import linkedin_oauth_service as los

        class FakeResp:
            status_code = 200
            text = ""
            def json(self):
                return {"elements": [{"organizationUrn": "urn:li:organization:1", "localizedName": "Acme"}]}

        captured = {}

        def fake_get(url, params, headers, timeout=None):
            captured["url"] = url
            captured["params"] = params
            captured["headers"] = headers
            return FakeResp()

        monkeypatch.setattr("httpx.get", fake_get)
        element = los.lookup_organization_by_vanity_name("tok", "acme")
        assert element["localizedName"] == "Acme"
        assert captured["url"] == "https://api.linkedin.com/rest/organizations"
        assert captured["params"] == {"q": "vanityName", "vanityName": "acme"}
        assert captured["headers"]["Authorization"] == "Bearer tok"
        assert captured["headers"]["LinkedIn-Version"]

    def test_lookup_unauthorized(self, monkeypatch):
        """Lookup 401 → 提示重新授权"""
        from app.services import linkedin_oauth_service as los
        from app.services.linkedin_oauth_service import LinkedInOAuthError

        class FakeResp:
            status_code = 401
            text = "unauthorized"
            def json(self):
                return {}

        monkeypatch.setattr("httpx.get", lambda url, params, headers, timeout=None: FakeResp())
        with pytest.raises(LinkedInOAuthError) as exc:
            los.lookup_organization_by_vanity_name("bad-token", "acme")
        assert exc.value.status_code == 401


class TestOAuthAPI:
    def test_start_requires_credentials(self):
        """未配置凭据时 start 返回 400"""
        resp = client.get("/api/linkedin/oauth/start")
        assert resp.status_code == 400

    def test_start_redirects(self):
        """start → 302 到 LinkedIn 授权页，state 写入会话"""
        db = TestSessionLocal()
        _save_credentials(db)
        db.close()

        resp = client.get("/api/linkedin/oauth/start", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"].startswith("https://www.linkedin.com/oauth/v2/authorization?")
        assert "client_id=86testclient" in resp.headers["location"]
        assert "state=" in resp.headers["location"]

    def test_callback_state_mismatch(self):
        """回调 state 不匹配 → 400"""
        resp = client.get("/api/linkedin/oauth/callback?code=abc&state=wrong")
        assert resp.status_code == 400
        assert "state" in resp.json()["detail"]

    def test_callback_success(self, monkeypatch):
        """回调成功 → 302 回设置页 + token 已保存"""
        import app.services.linkedin_oauth_service as los

        class FakeResp:
            status_code = 200
            text = ""
            def json(self):
                return {"access_token": "real-token", "expires_in": 86400, "scope": "r_organization_social"}

        monkeypatch.setattr("httpx.post", lambda url, data, timeout=None: FakeResp())

        db = TestSessionLocal()
        _save_credentials(db)
        db.close()

        # 先 start 拿 state
        resp = client.get("/api/linkedin/oauth/start", follow_redirects=False)
        from urllib.parse import urlparse, parse_qs
        state = parse_qs(urlparse(resp.headers["location"]).query)["state"][0]

        # 模拟回调
        resp = client.get(f"/api/linkedin/oauth/callback?code=xyz&state={state}", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/settings"

        from app.services.linkedin_oauth_service import get_access_token
        db = TestSessionLocal()
        assert get_access_token(db, 1) == "real-token"
        db.close()

    def test_disconnect(self):
        """断开授权删除 token"""
        db = TestSessionLocal()
        _save_token(db)
        db.close()
        resp = client.post("/api/linkedin/oauth/disconnect")
        assert resp.status_code == 200
        db = TestSessionLocal()
        assert db.query(LinkedInOAuthToken).filter(LinkedInOAuthToken.user_id == 1).count() == 0
        db.close()


class TestResolveAPI:
    def _create_profile(self, db: Session, vanity="aquatech-solutions") -> CustomerSocialProfile:
        c = Customer(company_name="AquaTech", website="aquatech-solutions.com")
        db.add(c)
        db.commit()
        p = CustomerSocialProfile(
            customer_id=c.id,
            platform="linkedin",
            profile_type="company",
            profile_url=f"https://www.linkedin.com/company/{vanity}",
            vanity_name=vanity,
            source="search",
            confidence=50,
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        return p

    def test_resolve_without_authorization(self):
        """未授权 → 401 提示先授权"""
        db = TestSessionLocal()
        _save_credentials(db)
        p = self._create_profile(db)
        db.close()

        resp = client.post(f"/api/social-profiles/{p.id}/resolve")
        assert resp.status_code == 401
        assert "授权" in resp.json()["detail"]

    def test_resolve_success(self, monkeypatch):
        """已授权 + 官方 API → 更新组织详情"""
        from app.services import linkedin_oauth_service as los

        db = TestSessionLocal()
        _save_credentials(db)
        p = self._create_profile(db)
        db.close()
        _save_token(db)

        class FakeResp:
            status_code = 200
            text = ""
            def json(self):
                return {"elements": [{
                    "organizationUrn": "urn:li:organization:999",
                    "localizedName": "AquaTech Solutions Official",
                    "vanityName": "aquatech-solutions",
                    "staffCountRange": {"start": 501, "end": 1000},
                    "localizedWebsite": {"localized": {"en_US": "https://www.aquatech-solutions.com"}},
                }]}

        monkeypatch.setattr("httpx.get", lambda url, params, headers, timeout=None: FakeResp())

        resp = client.post(f"/api/social-profiles/{p.id}/resolve")
        assert resp.status_code == 200, resp.text
        profile = resp.json()["profile"]
        assert profile["display_name"] == "AquaTech Solutions Official"
        assert profile["external_id"] == "urn:li:organization:999"
        assert profile["staff_count_range"] == "501-1000"
        assert profile["source"] == "official_api"
        assert profile["website_url"] == "https://www.aquatech-solutions.com"

    def test_resolve_not_found(self, monkeypatch):
        """官方 API 未找到组织 → 404"""
        from app.services import linkedin_oauth_service as los

        db = TestSessionLocal()
        _save_credentials(db)
        p = self._create_profile(db, vanity="nonexistent-co")
        db.close()
        _save_token(db)

        class FakeResp:
            status_code = 404
            text = ""

        monkeypatch.setattr("httpx.get", lambda url, params, headers, timeout=None: FakeResp())

        resp = client.post(f"/api/social-profiles/{p.id}/resolve")
        assert resp.status_code == 404

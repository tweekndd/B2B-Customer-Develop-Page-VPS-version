"""
Gmail 发信检测测试（V5.2）
覆盖：域名匹配器（严格主域/子域/反向包含/公共后缀）、同步编排（mock Gmail API）、
活动幂等去重、manual_email 匹配、API（账户/活动/webhook 白名单/忽略/删除）
"""
import base64
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.database import (
    Base, get_db, Customer, CustomerEmail, CustomerEmailActivity, MailAccount, User,
)
from app.auth import hash_password
from app.services.user_config import encrypt_secret
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


def _create_customer(db: Session, **overrides) -> Customer:
    data = {
        "company_name": "AquaTech Solutions",
        "website": "https://www.aquatech-solutions.com",
        "country": "Saudi Arabia",
    }
    data.update(overrides)
    c = Customer(**data)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _create_account(db: Session, email="me@gmail.com", status="active") -> MailAccount:
    account = MailAccount(
        user_id=1,
        provider="gmail",
        email_address=email,
        access_token_encrypted=encrypt_secret("fake-access-token"),
        refresh_token_encrypted=encrypt_secret("fake-refresh-token"),
        token_expires_at=datetime.datetime.utcnow() + datetime.timedelta(hours=1),
        status=status,
        scopes=json.dumps(["gmail.readonly"]),
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


class TestDomainMatcher:
    def test_extract_registrable_domain(self):
        from app.services.email_domain_matcher import extract_registrable_domain
        assert extract_registrable_domain("https://www.aquatech.com/about") == "aquatech.com"
        assert extract_registrable_domain("http://sub.aquatech.co.uk") == "aquatech.co.uk"
        assert extract_registrable_domain("aquatech.com.cn") == "aquatech.com.cn"
        assert extract_registrable_domain("www.aquatech.io") == "aquatech.io"
        assert extract_registrable_domain("https://aquatech.com:8443/x") == "aquatech.com"
        assert extract_registrable_domain("") is None
        assert extract_registrable_domain("notadomain") is None

    def test_match_domain_rules(self):
        """严格主域匹配：子域默认不匹配，反向包含不匹配"""
        from app.services.email_domain_matcher import match_domain
        assert match_domain("aquatech.com", "aquatech.com") is True
        assert match_domain("aquatech.com", "sales.aquatech.com") is False  # 默认不开子域
        assert match_domain("aquatech.com", "sales.aquatech.com", allow_subdomain=True) is True
        assert match_domain("aquatech.com", "evilaquatech.com") is False  # 反向包含
        assert match_domain("aquatech.com", "aquatech.com.evil.com") is False
        assert match_domain("", "x.com") is False

    def test_extract_email_domain(self):
        from app.services.email_domain_matcher import extract_email_domain
        assert extract_email_domain("Sales@AquaTech.com") == "aquatech.com"
        assert extract_email_domain("no-at-sign") is None


class TestMailSync:
    def test_sync_creates_activity(self, monkeypatch):
        """同步：匹配收件人域名并生成活动记录"""
        import app.services.gmail_service as gs
        from app.services import mail_sync_service

        db = TestSessionLocal()
        c = _create_customer(db)
        customer_id = c.id
        account = _create_account(db)
        db.close()

        sent_msg = {
            "id": "msg-1",
            "threadId": "thread-1",
            "snippet": "报价如下",
            "internalDate": "1700000000000",
            "payload": {
                "headers": [
                    {"name": "From", "value": "me@gmail.com"},
                    {"name": "To", "value": "sales@aquatech-solutions.com"},
                    {"name": "Subject", "value": "RE: Water treatment quote"},
                    {"name": "Date", "value": "Tue, 14 Nov 2023 10:00:00 +0000"},
                    {"name": "Message-ID", "value": "<abc@mail.gmail.com>"},
                ]
            },
        }

        monkeypatch.setattr(gs, "list_sent_messages", lambda token, start_history_id=None, page_token=None, max_results=100, after_date=None: {
            "messages": [{"id": "msg-1"}],
            "nextPageToken": None,
        })
        monkeypatch.setattr(gs, "get_message_metadata", lambda token, mid: sent_msg)

        db = TestSessionLocal()
        account = db.query(MailAccount).filter(MailAccount.id == account.id).first()
        stats = mail_sync_service.sync_account(db, account)
        assert stats["processed"] == 1
        assert stats["matched"] == 1
        assert stats["new_activities"] == 1

        activity = db.query(CustomerEmailActivity).filter(
            CustomerEmailActivity.customer_id == customer_id
        ).first()
        assert activity is not None
        assert activity.matched_domain == "aquatech-solutions.com"
        assert activity.match_type == "exact_domain"
        assert activity.subject == "RE: Water treatment quote"
        assert activity.sent_at is not None
        db.close()

    def test_sync_idempotent(self, monkeypatch):
        """重复同步不产生重复活动"""
        import app.services.gmail_service as gs
        from app.services import mail_sync_service

        db = TestSessionLocal()
        c = _create_customer(db)
        customer_id = c.id
        account = _create_account(db)
        db.close()

        sent_msg = {
            "id": "msg-1",
            "threadId": "t1",
            "snippet": "",
            "internalDate": "1700000000000",
            "payload": {
                "headers": [
                    {"name": "To", "value": "sales@aquatech-solutions.com"},
                    {"name": "Subject", "value": "Hi"},
                ]
            },
        }
        monkeypatch.setattr(gs, "list_sent_messages", lambda token, start_history_id=None, page_token=None, max_results=100, after_date=None: {
            "messages": [{"id": "msg-1"}], "nextPageToken": None,
        })
        monkeypatch.setattr(gs, "get_message_metadata", lambda token, mid: sent_msg)

        db = TestSessionLocal()
        account = db.query(MailAccount).filter(MailAccount.id == account.id).first()
        mail_sync_service.sync_account(db, account)
        stats2 = mail_sync_service.sync_account(db, account)
        assert stats2["new_activities"] == 0
        assert db.query(CustomerEmailActivity).count() == 1
        db.close()

    def test_no_match_for_other_domain(self, monkeypatch):
        """发往其他域名的邮件不生成活动"""
        import app.services.gmail_service as gs
        from app.services import mail_sync_service

        db = TestSessionLocal()
        _create_customer(db)
        account = _create_account(db)
        db.close()

        sent_msg = {
            "id": "msg-2",
            "threadId": "t2",
            "internalDate": "1700000000000",
            "payload": {"headers": [
                {"name": "To", "value": "info@totally-other-company.com"},
                {"name": "Subject", "value": "Hello"},
            ]},
        }
        monkeypatch.setattr(gs, "list_sent_messages", lambda token, start_history_id=None, page_token=None, max_results=100, after_date=None: {
            "messages": [{"id": "msg-2"}], "nextPageToken": None,
        })
        monkeypatch.setattr(gs, "get_message_metadata", lambda token, mid: sent_msg)

        db = TestSessionLocal()
        account = db.query(MailAccount).filter(MailAccount.id == account.id).first()
        stats = mail_sync_service.sync_account(db, account)
        assert stats["matched"] == 0
        db.close()

    def test_manual_email_match(self, monkeypatch):
        """收件人在 customer_emails 表中 → manual_email 匹配"""
        import app.services.gmail_service as gs
        from app.services import mail_sync_service

        db = TestSessionLocal()
        c = _create_customer(db, website="https://www.unrelated-site.com")  # 官网域名不匹配
        db.add(CustomerEmail(customer_id=c.id, email="purchase@other-domain.com", source="manual"))
        db.commit()
        account = _create_account(db)
        db.close()

        sent_msg = {
            "id": "msg-3",
            "threadId": "t3",
            "internalDate": "1700000000000",
            "payload": {"headers": [
                {"name": "To", "value": "purchase@other-domain.com"},
                {"name": "Subject", "value": "报价"},
            ]},
        }
        monkeypatch.setattr(gs, "list_sent_messages", lambda token, start_history_id=None, page_token=None, max_results=100, after_date=None: {
            "messages": [{"id": "msg-3"}], "nextPageToken": None,
        })
        monkeypatch.setattr(gs, "get_message_metadata", lambda token, mid: sent_msg)

        db = TestSessionLocal()
        account = db.query(MailAccount).filter(MailAccount.id == account.id).first()
        stats = mail_sync_service.sync_account(db, account)
        assert stats["matched"] == 1
        activity = db.query(CustomerEmailActivity).first()
        assert activity.match_type == "manual_email"
        assert activity.matched_domain == "other-domain.com"
        db.close()


class TestMailAccountAPI:
    def test_list_empty(self):
        """未绑定时返回空列表与凭据状态"""
        resp = client.get("/api/mail-accounts")
        assert resp.status_code == 200
        data = resp.json()
        assert data["accounts"] == []
        assert data["status"]["client_configured"] is False

    def test_oauth_start_requires_credentials(self):
        """未配置凭据时 start 返回 400"""
        resp = client.get("/api/mail-accounts/gmail/oauth/start", follow_redirects=False)
        assert resp.status_code == 400

    def test_oauth_start_redirects(self):
        """配置凭据后 start → 302 到 Google 授权页"""
        from app.services.user_config import set_user_api_config, SERVICE_GMAIL
        db = TestSessionLocal()
        set_user_api_config(db, 1, SERVICE_GMAIL, api_key="cid.apps.googleusercontent.com", api_secret="secret", enabled=True)
        db.close()

        resp = client.get("/api/mail-accounts/gmail/oauth/start", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
        assert "access_type=offline" in resp.headers["location"]
        assert "gmail.readonly" in resp.headers["location"]

    def test_callback_state_mismatch(self):
        resp = client.get("/api/mail-accounts/oauth/callback/gmail?code=abc&state=wrong")
        assert resp.status_code == 400

    def test_disconnect(self):
        db = TestSessionLocal()
        account = _create_account(db)
        db.close()
        resp = client.post(f"/api/mail-accounts/{account.id}/disconnect")
        assert resp.status_code == 200
        db = TestSessionLocal()
        assert db.query(MailAccount).filter(MailAccount.id == account.id).count() == 0
        db.close()

    def test_sync_endpoint(self, monkeypatch):
        """手动同步端点"""
        import app.services.gmail_service as gs
        from app.services import mail_sync_service

        db = TestSessionLocal()
        c = _create_customer(db)
        customer_id = c.id
        account = _create_account(db)
        db.close()

        sent_msg = {
            "id": "msg-9", "threadId": "t9", "internalDate": "1700000000000",
            "payload": {"headers": [
                {"name": "To", "value": "sales@aquatech-solutions.com"},
                {"name": "Subject", "value": "Quote"},
            ]},
        }
        monkeypatch.setattr(gs, "list_sent_messages", lambda token, start_history_id=None, page_token=None, max_results=100, after_date=None: {
            "messages": [{"id": "msg-9"}], "nextPageToken": None,
        })
        monkeypatch.setattr(gs, "get_message_metadata", lambda token, mid: sent_msg)

        resp = client.post(f"/api/mail-accounts/{account.id}/sync")
        assert resp.status_code == 200, resp.text
        assert resp.json()["stats"]["new_activities"] == 1

        resp = client.get(f"/api/customers/{customer_id}/email-activities")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1


class TestMailActivityAPI:
    def test_activities_ignore_and_delete(self):
        db = TestSessionLocal()
        c = _create_customer(db)
        customer_id = c.id
        account = _create_account(db)
        db.add(CustomerEmailActivity(
            customer_id=c.id, mail_account_id=account.id, provider="gmail",
            provider_message_id="m1", matched_domain="aquatech-solutions.com",
            subject="Hi", to_addresses_json='["sales@aquatech-solutions.com"]',
        ))
        db.commit()
        activity_id = db.query(CustomerEmailActivity).first().id
        db.close()

        # 忽略
        resp = client.post(f"/api/email-activities/{activity_id}/ignore")
        assert resp.status_code == 200
        # 默认列表不含已忽略
        resp = client.get(f"/api/customers/{customer_id}/email-activities")
        assert resp.json()["total"] == 0
        # include_ignored 包含
        resp = client.get(f"/api/customers/{customer_id}/email-activities?include_ignored=true")
        assert resp.json()["total"] == 1

        # 删除
        resp = client.delete(f"/api/email-activities/{activity_id}")
        assert resp.status_code == 200
        db = TestSessionLocal()
        assert db.query(CustomerEmailActivity).count() == 0
        db.close()

    def test_sync_activities_endpoint_no_account(self):
        """未绑定邮箱时同步返回 400"""
        db = TestSessionLocal()
        c = _create_customer(db)
        customer_id = c.id
        db.close()
        resp = client.post(f"/api/customers/{customer_id}/email-activities/sync")
        assert resp.status_code == 400


class TestWebhook:
    def test_webhook_public_no_auth(self):
        """Webhook 无需登录（绕过 API 认证中间件）"""
        payload = {
            "message": {
                "data": base64.b64encode(json.dumps({
                    "emailAddress": "me@gmail.com",
                    "historyId": "12345",
                }).encode()).decode(),
            }
        }
        resp = client.post("/api/webhooks/gmail/pubsub", json=payload)
        # 未绑定账户也返回 200（幂等确认）
        assert resp.status_code == 200
        assert resp.json()["received"] is True

    def test_webhook_invalid_token(self):
        """配置 GMAIL_PUBSUB_TOKEN 后，无 token 的推送被拒绝"""
        os.environ["GMAIL_PUBSUB_TOKEN"] = "secret-token"
        try:
            resp = client.post("/api/webhooks/gmail/pubsub", json={"message": {"data": "e30="}})
            assert resp.status_code == 401
            resp = client.post(
                "/api/webhooks/gmail/pubsub",
                json={"message": {"data": "e30="}},
                headers={"Authorization": "Bearer secret-token"},
            )
            assert resp.status_code == 200
        finally:
            os.environ.pop("GMAIL_PUBSUB_TOKEN", None)


class TestMessageParsing:
    def test_parse_message_payload(self):
        """消息解析：Header 提取与收件人地址提取"""
        from app.services.gmail_service import parse_message_payload, _extract_addresses
        raw = {
            "id": "m1",
            "threadId": "t1",
            "snippet": "hello",
            "payload": {"headers": [
                {"name": "From", "value": "Me <me@gmail.com>"},
                {"name": "To", "value": "Sales <sales@aquatech-solutions.com>, boss@aquatech-solutions.com"},
                {"name": "Cc", "value": "cc@other.com"},
                {"name": "Subject", "value": "Re: Quote"},
                {"name": "Message-ID", "value": "<x@mail.gmail.com>"},
            ]},
        }
        parsed = parse_message_payload(raw)
        assert parsed["subject"] == "Re: Quote"
        assert parsed["message_id"] == "<x@mail.gmail.com>"
        assert _extract_addresses(parsed["to_raw"]) == ["sales@aquatech-solutions.com", "boss@aquatech-solutions.com"]
        assert _extract_addresses(parsed["cc_raw"]) == ["cc@other.com"]


"""
客户邮箱维护 API 测试（V5.1）
覆盖：手动新增/重复/大小写/非法格式/删除/设主邮箱/JSON 双写/merge-legacy/
add-emails 来源/sync 导出导入
"""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.database import Base, get_db, Customer, CustomerEmail
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
    """登录测试管理员（认证中间件要求 /api/* 必须登录）"""
    db = TestSessionLocal()
    user = None
    try:
        from app.database import User
        user = User(
            username="testadmin",
            password_hash=hash_password("testpassword"),
            role="admin",
            is_active=1,
        )
        db.add(user)
        db.commit()
    finally:
        db.close()
    resp = client.post(
        "/api/auth/login",
        json={"username": "testadmin", "password": "testpassword"},
    )
    assert resp.status_code == 200, resp.text
    yield
    if user:
        resp = client.post("/api/auth/logout")


def _create_customer(db: Session, **overrides) -> Customer:
    data = {
        "company_name": "Test Water Corp",
        "website": "testwater.com",
        "country": "Saudi Arabia",
    }
    data.update(overrides)
    c = Customer(**data)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _reload_customer(db: Session, customer_id: int) -> Customer:
    return db.query(Customer).filter(Customer.id == customer_id).first()


class TestManualEmailCRUD:
    def test_add_valid_email(self):
        """新增合法手动邮箱：保存成功，来源为 manual"""
        db = TestSessionLocal()
        c = _create_customer(db)
        db.close()

        resp = client.post(f"/api/customers/{c.id}/emails",
                           json={"email": "sales@testwater.com"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["email"]["email"] == "sales@testwater.com"
        assert data["email"]["source"] == "manual"
        assert data["email"]["verification"] == "unknown"

        # 数据库记录 + JSON 视图同步
        db = TestSessionLocal()
        records = db.query(CustomerEmail).filter(CustomerEmail.customer_id == c.id).all()
        assert len(records) == 1
        assert records[0].domain == "testwater.com"
        assert records[0].local_part == "sales"
        assert records[0].source == "manual"
        c = _reload_customer(db, c.id)
        assert json.loads(c.emails) == ["sales@testwater.com"]
        db.close()

    def test_duplicate_email_is_idempotent(self):
        """重复新增相同邮箱：不产生重复记录"""
        db = TestSessionLocal()
        c = _create_customer(db)
        db.close()

        r1 = client.post(f"/api/customers/{c.id}/emails", json={"email": "info@testwater.com"})
        assert r1.status_code == 200
        r2 = client.post(f"/api/customers/{c.id}/emails", json={"email": "info@testwater.com"})
        assert r2.status_code == 200

        db = TestSessionLocal()
        records = db.query(CustomerEmail).filter(CustomerEmail.customer_id == c.id).all()
        assert len(records) == 1
        db.close()

    def test_case_insensitive_dedup(self):
        """大小写不同的相同邮箱视为同一邮箱"""
        db = TestSessionLocal()
        c = _create_customer(db)
        db.close()

        client.post(f"/api/customers/{c.id}/emails", json={"email": "INFO@TestWater.com"})
        client.post(f"/api/customers/{c.id}/emails", json={"email": "info@testwater.com"})

        db = TestSessionLocal()
        records = db.query(CustomerEmail).filter(CustomerEmail.customer_id == c.id).all()
        assert len(records) == 1
        assert records[0].email == "info@testwater.com"
        db.close()

    def test_invalid_email_format(self):
        """非法邮箱格式返回 400，不写入数据库"""
        db = TestSessionLocal()
        c = _create_customer(db)
        db.close()

        for bad in ["not-an-email", "a@b", "x@", "@domain.com", "a b@c.com"]:
            resp = client.post(f"/api/customers/{c.id}/emails", json={"email": bad})
            assert resp.status_code == 400, f"{bad} 应被拒绝"

        db = TestSessionLocal()
        assert db.query(CustomerEmail).filter(CustomerEmail.customer_id == c.id).count() == 0
        db.close()

    def test_add_email_customer_not_found(self):
        """客户不存在返回 404"""
        resp = client.post("/api/customers/999/emails", json={"email": "a@b.com"})
        assert resp.status_code == 404

    def test_edit_email(self):
        """编辑邮箱内容"""
        db = TestSessionLocal()
        c = _create_customer(db)
        db.close()
        r = client.post(f"/api/customers/{c.id}/emails", json={"email": "old@testwater.com"})
        email_id = r.json()["email"]["id"]

        resp = client.put(f"/api/customer-emails/{email_id}", json={"email": "new@testwater.com"})
        assert resp.status_code == 200
        assert resp.json()["email"]["email"] == "new@testwater.com"

        db = TestSessionLocal()
        records = db.query(CustomerEmail).filter(CustomerEmail.customer_id == c.id).all()
        assert len(records) == 1
        assert records[0].email == "new@testwater.com"
        c = _reload_customer(db, c.id)
        assert json.loads(c.emails) == ["new@testwater.com"]
        db.close()

    def test_delete_email(self):
        """删除邮箱：只删除客户邮箱记录"""
        db = TestSessionLocal()
        c = _create_customer(db)
        db.close()
        r = client.post(f"/api/customers/{c.id}/emails", json={"email": "del@testwater.com"})
        email_id = r.json()["email"]["id"]

        resp = client.delete(f"/api/customer-emails/{email_id}")
        assert resp.status_code == 200

        db = TestSessionLocal()
        assert db.query(CustomerEmail).filter(CustomerEmail.customer_id == c.id).count() == 0
        c = _reload_customer(db, c.id)
        assert json.loads(c.emails) == []
        db.close()

    def test_delete_email_not_found(self):
        resp = client.delete("/api/customer-emails/999")
        assert resp.status_code == 404


class TestPrimaryEmail:
    def test_single_primary(self):
        """设主邮箱互斥：同一客户只能有一个主邮箱"""
        db = TestSessionLocal()
        c = _create_customer(db)
        db.close()

        r1 = client.post(f"/api/customers/{c.id}/emails", json={"email": "a@testwater.com", "is_primary": True})
        r2 = client.post(f"/api/customers/{c.id}/emails", json={"email": "b@testwater.com", "is_primary": True})
        id_a = r1.json()["email"]["id"]
        id_b = r2.json()["email"]["id"]
        assert r2.json()["email"]["is_primary"] is True

        resp = client.put(f"/api/customer-emails/{id_a}", json={"is_primary": True})
        assert resp.status_code == 200

        db = TestSessionLocal()
        records = db.query(CustomerEmail).filter(CustomerEmail.customer_id == c.id).order_by(CustomerEmail.email).all()
        by_email = {r.email: bool(r.is_primary) for r in records}
        assert by_email == {"a@testwater.com": True, "b@testwater.com": False}
        db.close()
        assert id_a != id_b


class TestAddEmailsEndpoint:
    def test_add_emails_with_source(self):
        """add-emails 带 source 写入新表且来源正确"""
        db = TestSessionLocal()
        c = _create_customer(db)
        db.close()

        resp = client.post(
            f"/api/customers/{c.id}/add-emails",
            params={"emails": json.dumps(["sales@testwater.com", "info@testwater.com"]), "source": "hunter"},
        )
        assert resp.status_code == 200

        db = TestSessionLocal()
        records = db.query(CustomerEmail).filter(CustomerEmail.customer_id == c.id).order_by(CustomerEmail.email).all()
        assert {r.email for r in records} == {"sales@testwater.com", "info@testwater.com"}
        assert all(r.source == "hunter" for r in records)
        c = _reload_customer(db, c.id)
        assert set(json.loads(c.emails)) == {"sales@testwater.com", "info@testwater.com"}
        db.close()

    def test_add_emails_without_source_legacy_json(self):
        """add-emails 不带 source 保持旧行为（JSON 合并）"""
        db = TestSessionLocal()
        c = _create_customer(db, emails=json.dumps(["a@testwater.com"]))
        db.close()

        resp = client.post(
            f"/api/customers/{c.id}/add-emails",
            params={"emails": json.dumps(["b@testwater.com"])},
        )
        assert resp.status_code == 200
        assert resp.json()["email_count"] == 2

        db = TestSessionLocal()
        c = _reload_customer(db, c.id)
        assert set(json.loads(c.emails)) == {"a@testwater.com", "b@testwater.com"}
        db.close()

    def test_invalid_source_rejected(self):
        """非法来源返回 400"""
        db = TestSessionLocal()
        c = _create_customer(db)
        db.close()
        resp = client.post(
            f"/api/customers/{c.id}/add-emails",
            params={"emails": json.dumps(["a@testwater.com"]), "source": "hack"},
        )
        assert resp.status_code == 400


class TestLegacyMerge:
    def test_merge_legacy_emails(self):
        """旧 JSON 邮箱可迁移到新表，且幂等"""
        db = TestSessionLocal()
        c = _create_customer(db, emails=json.dumps(["old1@testwater.com", "old2@testwater.com"]))
        db.close()

        r1 = client.post(f"/api/customers/{c.id}/emails/merge-legacy")
        assert r1.status_code == 200
        assert r1.json()["merged"] == 2

        db = TestSessionLocal()
        records = db.query(CustomerEmail).filter(CustomerEmail.customer_id == c.id).all()
        assert len(records) == 2
        assert all(r.source == "legacy" for r in records)
        db.close()

        # 幂等：再次合并不产生重复
        r2 = client.post(f"/api/customers/{c.id}/emails/merge-legacy")
        assert r2.status_code == 200
        db = TestSessionLocal()
        assert db.query(CustomerEmail).filter(CustomerEmail.customer_id == c.id).count() == 2
        db.close()


class TestDetailAndList:
    def test_detail_includes_email_records(self):
        """详情接口返回结构化 email_records"""
        db = TestSessionLocal()
        c = _create_customer(db)
        db.close()
        client.post(f"/api/customers/{c.id}/emails", json={"email": "sales@testwater.com"})

        resp = client.get(f"/api/customers/{c.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["email_records"]) == 1
        assert data["email_records"][0]["email"] == "sales@testwater.com"
        assert data["email_records"][0]["source"] == "manual"

    def test_list_emails_endpoint(self):
        """GET /api/customers/{id}/emails 返回结构化列表"""
        db = TestSessionLocal()
        c = _create_customer(db)
        db.close()
        client.post(f"/api/customers/{c.id}/emails", json={"email": "x@testwater.com"})

        resp = client.get(f"/api/customers/{c.id}/emails")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1


class TestSyncWithEmails:
    def test_export_includes_customer_emails(self):
        """导出包含 customer_emails 结构化数据"""
        db = TestSessionLocal()
        c = _create_customer(db)
        db.close()
        client.post(f"/api/customers/{c.id}/emails", json={"email": "sync@testwater.com"})

        resp = client.get("/api/sync/export")
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == "2.9"
        cust = data["data"]["customers"][0]
        assert cust["customer_emails"][0]["email"] == "sync@testwater.com"
        assert cust["customer_emails"][0]["source"] == "manual"

    def test_import_merges_emails_for_existing_customer(self):
        """导入时对已存在客户合并邮箱（修复原跳过逻辑）"""
        db = TestSessionLocal()
        c = _create_customer(db)
        db.close()

        export = client.get("/api/sync/export").json()
        export["data"]["customers"][0]["customer_emails"] = [
            {"email": "imported@testwater.com", "source": "hunter", "is_primary": True}
        ]

        resp = client.post("/api/sync/import", json=export)
        assert resp.status_code == 200, resp.text

        db = TestSessionLocal()
        records = db.query(CustomerEmail).filter(CustomerEmail.customer_id == c.id).all()
        assert len(records) == 1
        assert records[0].email == "imported@testwater.com"
        assert records[0].source == "hunter"
        assert records[0].is_primary == 1
        c = _reload_customer(db, c.id)
        assert json.loads(c.emails) == ["imported@testwater.com"]
        db.close()

    def test_import_new_customer_with_emails(self):
        """导入新客户时一并导入邮箱"""
        export = client.get("/api/sync/export").json()
        export["data"]["customers"] = [{
            "company_name": "Fresh Co",
            "website": "freshco.com",
            "country": "UAE",
            "customer_emails": [
                {"email": "sales@freshco.com", "source": "website", "is_primary": True}
            ],
        }]

        resp = client.post("/api/sync/import", json=export)
        assert resp.status_code == 200
        assert resp.json()["imported"]["customers"] == 1

        db = TestSessionLocal()
        c = db.query(Customer).filter(Customer.company_name == "Fresh Co").first()
        assert c is not None
        records = db.query(CustomerEmail).filter(CustomerEmail.customer_id == c.id).all()
        assert len(records) == 1
        assert records[0].email == "sales@freshco.com"
        db.close()


class TestEmailServiceUnit:
    def test_normalize_email(self):
        """规范化函数：去空格、转小写、非法返回 None"""
        from app.services.customer_email_service import normalize_email
        assert normalize_email("  Sales@TestWater.com  ") == "sales@testwater.com"
        assert normalize_email("bad") is None
        assert normalize_email("a@b.c") is None
        assert normalize_email("") is None
        assert normalize_email("a@long-domain-name-with-123.com") == "a@long-domain-name-with-123.com"

    def test_scoring_contact_uses_json_view(self):
        """评分：联系方式得分基于双写后的 JSON 视图"""
        from app.services.scoring_engine import calculate_scores
        db = TestSessionLocal()
        c = _create_customer(db)
        db.close()
        for i in range(4):
            client.post(f"/api/customers/{c.id}/emails", json={"email": f"e{i}@testwater.com"})

        db = TestSessionLocal()
        c = _reload_customer(db, c.id)
        scores = calculate_scores(
            website_text="water treatment",
            positive_keywords={},
            company_type="Distributor",
            country="Saudi Arabia",
            emails=json.loads(c.emails or "[]"),
        )
        assert scores["contact_score"] == 10  # 4个邮箱 = 满分10
        db.close()



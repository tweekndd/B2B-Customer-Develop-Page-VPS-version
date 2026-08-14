"""
模型拆分完整性测试（V5.3 重构）

1. Base.metadata 必须注册全部 17 张表（防拆分后漏导入）
2. 列表接口瘦身后的行为：email_count 来自 customer_emails 聚合、
   国家列表缓存、显式字段查询不回归
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

from app.database import Base, get_db, Customer, CustomerEmail, User
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
    # 重置列表接口的国家列表缓存（模块级全局，防跨测试残留）
    from app.api import customers as customers_api
    customers_api._country_cache["ts"] = 0.0
    customers_api._country_cache["data"] = []


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


EXPECTED_TABLES = {
    "customers",
    "customer_emails",
    "customer_social_profiles",
    "customer_email_activities",
    "mail_accounts",
    "linkedin_oauth_tokens",
    "users",
    "user_api_config",
    "search_tasks",
    "search_cache",
    "website_cache",
    "hunter_cache",
    "tomba_cache",
    "email_quota_log",
    "analysis_cache",
    "geocode_cache",
    "prospeo_cache",
    # V5.3 阶段2：智能分析快照
    "website_snapshots",
    "analysis_runs",
    "score_snapshots",
}


class TestModelRegistry:
    def test_all_tables_registered(self):
        """拆分后 Base.metadata 必须包含全部 17 张表"""
        tables = set(Base.metadata.tables.keys())
        missing = EXPECTED_TABLES - tables
        assert not missing, f"漏注册的表: {missing}"

    def test_no_extra_unexpected_tables(self):
        tables = set(Base.metadata.tables.keys())
        assert tables == EXPECTED_TABLES, f"意外多出的表: {tables - EXPECTED_TABLES}"

    def test_models_mappable(self):
        """关键模型可正常映射（含 relationship）"""
        from sqlalchemy.orm import configure_mappers
        configure_mappers()
        assert Customer.email_records.property.mapper.class_ is CustomerEmail

    def test_import_from_models_directly(self):
        """新路径 app.models 可直接导入"""
        from app.models import Customer as M1
        from app.models import MailAccount, CustomerEmailActivity
        assert M1.__tablename__ == "customers"
        assert MailAccount.__tablename__ == "mail_accounts"
        assert CustomerEmailActivity.__tablename__ == "customer_email_activities"

    def test_import_from_database_compat(self):
        """旧路径 app.database 兼容导出不缺失"""
        import app.database as db
        for name in ("Customer", "CustomerEmail", "CustomerSocialProfile", "SearchTask",
                     "SearchCache", "WebsiteCache", "HunterCache", "TombaCache",
                     "EmailQuotaLog", "AnalysisCache", "GeocodeCache", "ProspeoCache",
                     "User", "UserApiConfig", "LinkedInOAuthToken", "MailAccount",
                     "CustomerEmailActivity", "WebsiteSnapshot", "AnalysisRun",
                     "ScoreSnapshot", "init_db", "get_db", "Base", "SessionLocal"):
            assert hasattr(db, name), f"app.database 缺少导出: {name}"


class TestListPerformanceBehavior:
    def _create_customer(self, db: Session, **overrides) -> Customer:
        data = {
            "company_name": "Test Co",
            "website": "testco.com",
            "country": "Saudi Arabia",
            "total_score": 70,
            "priority": "B",
            "website_text": "LONG WEBSITE TEXT " * 500,  # 大字段（列表不应加载）
            "ai_raw_json": json.dumps({"analysis_reason": "x" * 2000}),
        }
        data.update(overrides)
        c = Customer(**data)
        db.add(c)
        db.commit()
        db.refresh(c)
        return c

    def test_email_count_from_table(self):
        """列表 email_count 来自 customer_emails 表聚合"""
        db = TestSessionLocal()
        c = self._create_customer(db)
        customer_id = c.id
        db.add(CustomerEmail(customer_id=customer_id, email="a@testco.com", source="manual"))
        db.add(CustomerEmail(customer_id=customer_id, email="b@testco.com", source="website"))
        db.commit()
        db.close()

        resp = client.get("/api/customers")
        assert resp.status_code == 200
        customers = resp.json()["customers"]
        assert len(customers) == 1
        assert customers[0]["email_count"] == 2

    def test_legacy_json_email_count_fallback(self):
        """仅 JSON（未入表）时 email_count 应回退为 0（视图同步后正常场景应为表数据）"""
        db = TestSessionLocal()
        c = self._create_customer(db, emails=json.dumps(["x@testco.com"]))
        db.close()
        resp = client.get("/api/customers")
        # 表里没有记录 → 聚合为 0（列表不再解析 JSON 大字段）
        assert resp.json()["customers"][0]["email_count"] == 0

    def test_country_list_cached(self):
        """国家列表返回且缓存生效（连续两次请求一致）"""
        db = TestSessionLocal()
        self._create_customer(db, country="Saudi Arabia")
        self._create_customer(db, country="UAE")
        db.close()

        r1 = client.get("/api/customers")
        r2 = client.get("/api/customers")
        assert r1.json()["countries"] == ["Saudi Arabia", "UAE"]
        assert r2.json()["countries"] == r1.json()["countries"]

    def test_list_still_filters_and_sorts(self):
        """筛选/排序/搜索不回归"""
        db = TestSessionLocal()
        self._create_customer(db, company_name="Alpha Water", total_score=90, priority="A")
        self._create_customer(db, company_name="Beta Trade", total_score=50, priority="C")
        db.close()

        resp = client.get("/api/customers?priority=A")
        assert resp.json()["total"] == 1
        assert resp.json()["customers"][0]["company_name"] == "Alpha Water"

        resp = client.get("/api/customers?search=Beta")
        assert resp.json()["total"] == 1
        assert resp.json()["customers"][0]["company_name"] == "Beta Trade"

        resp = client.get("/api/customers?sort_by_score=asc")
        assert [c["company_name"] for c in resp.json()["customers"]] == ["Beta Trade", "Alpha Water"]

    def test_list_pagination(self):
        """分页正常"""
        db = TestSessionLocal()
        for i in range(15):
            self._create_customer(db, company_name=f"Co {i}")
        db.close()

        resp = client.get("/api/customers?page=2&page_size=10")
        data = resp.json()
        assert data["total"] == 15
        assert len(data["customers"]) == 5
        assert data["page"] == 2
        assert data["total_pages"] == 2


class TestSyncExportSlimming:
    """V5.3：导出瘦身（standard 默认 / full 完整）"""

    def _seed_caches(self):
        from app.database import SearchCache, WebsiteCache, AnalysisCache
        db = TestSessionLocal()
        for i in range(5):
            db.add(SearchCache(keyword=f"kw{i}", country="SA", website=f"site{i}.com"))
        for i in range(3):
            db.add(WebsiteCache(
                website=f"site{i}.com",
                content="X" * 5000,  # 大字段：standard 模式不应导出
                content_hash=f"hash{i}",
                last_crawled=datetime.datetime.utcnow(),
            ))
        for i in range(4):
            db.add(AnalysisCache(website=f"site{i}.com", summary=f"summary{i}"))
        db.commit()
        db.close()

    def test_standard_mode_excludes_content_and_limits_cache(self):
        """standard（默认）：website_cache 不含 content；缓存限量"""
        self._seed_caches()

        resp = client.get("/api/sync/export")
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == "2.9"
        assert data["mode"] == "standard"
        assert data["stats"]["truncated_cache"] is True

        wc = data["data"]["website_cache"]
        assert len(wc) == 3
        assert all("content" not in w for w in wc)  # 不含官网原文
        assert wc[0]["content_hash"]  # 保留元数据

        sc = data["data"]["search_cache"]
        assert len(sc) == 5

    def test_full_mode_includes_content(self):
        """full：包含官网原文 content"""
        self._seed_caches()
        resp = client.get("/api/sync/export?mode=full")
        data = resp.json()
        assert data["mode"] == "full"
        assert data["stats"]["truncated_cache"] is False
        wc = data["data"]["website_cache"]
        assert any("content" in w and w["content"] for w in wc)

    def test_cached_limit(self, monkeypatch):
        """缓存导出数量受 SYNC_CACHE_EXPORT_LIMIT 限制"""
        import app.api.sync as sync_api
        monkeypatch.setattr(sync_api, "CACHE_EXPORT_LIMIT", 2)
        self._seed_caches()
        resp = client.get("/api/sync/export")
        data = resp.json()
        assert len(data["data"]["search_cache"]) == 2
        assert len(data["data"]["website_cache"]) == 2
        assert len(data["data"]["analysis_cache"]) == 2

    def test_import_cache_dedup_bulk(self):
        """导入：缓存批量查重（已存在 key 不重复插入，缺失 key 被补回）"""
        from app.database import SearchCache, WebsiteCache, AnalysisCache

        self._seed_caches()
        export = client.get("/api/sync/export").json()

        def _cache_keys(db):
            return {
                r[0] for r in db.query(SearchCache.keyword, SearchCache.country, SearchCache.website).all()
            }, {
                r[0] for r in db.query(WebsiteCache.website).all()
            }, {
                r[0] for r in db.query(AnalysisCache.website).all()
            }

        db = TestSessionLocal()
        before = _cache_keys(db)

        resp = client.post("/api/sync/import", json=export)
        assert resp.status_code == 200, resp.text

        db = TestSessionLocal()
        after = _cache_keys(db)
        # 全部已存在 → 无任何新增（去重生效，不产生重复 key）
        assert after == before

        # 模拟目标库缺失一条 → 导入后补回
        db.query(SearchCache).filter(SearchCache.website == "site1.com").delete()
        db.query(WebsiteCache).filter(WebsiteCache.website == "site2.com").delete()
        db.commit()
        db.close()

        client.post("/api/sync/import", json=export)
        db = TestSessionLocal()
        sc_keys, wc_keys, _ = _cache_keys(db)
        assert any(k[2] == "site1.com" for k in { (r[0], r[1], r[2]) for r in db.query(SearchCache.keyword, SearchCache.country, SearchCache.website).all() })
        assert "site2.com" in wc_keys
        db.close()


class TestStage3SnapshotSync:
    """V5.3 阶段3：快照表导出/导入 + 客户大字段停写"""

    def test_standard_export_excludes_customer_big_fields(self):
        """standard 模式：客户记录不含 website_text/ai_raw_json；快照表单独导出"""
        db = TestSessionLocal()
        c = Customer(
            company_name="Big Co",
            website="bigco.com",
            website_text="X" * 1000,
            ai_raw_json='{"company_type": "EPC"}',
            total_score=60,
        )
        db.add(c)
        db.commit()
        cid = c.id
        db.close()

        resp = client.get("/api/sync/export")
        data = resp.json()
        assert data["version"] == "2.9"
        cust = data["data"]["customers"][0]
        assert "website_text" not in cust
        assert "ai_raw_json" not in cust
        assert "website_snapshots" in data["data"]
        assert "analysis_runs" in data["data"]
        assert "score_snapshots" in data["data"]
        assert cid is not None

    def test_full_export_includes_big_fields(self):
        """full 模式：客户记录包含 website_text/ai_raw_json"""
        db = TestSessionLocal()
        db.add(Customer(company_name="Full Co", website="fullco.com", website_text="TEXT", ai_raw_json="{}"))
        db.commit()
        db.close()
        resp = client.get("/api/sync/export?mode=full")
        cust = resp.json()["data"]["customers"][0]
        assert cust.get("website_text") == "TEXT"
        assert cust.get("ai_raw_json") == "{}"

    def test_snapshot_export_and_import_with_id_mapping(self):
        """快照随客户导出并导入（源 id → 目标 id 映射）"""
        from app.database import WebsiteSnapshot, AnalysisRun, ScoreSnapshot

        # 源库：客户 + 快照
        db = TestSessionLocal()
        c = Customer(company_name="Snapshot Co", website="snapco.com", total_score=66)
        db.add(c)
        db.commit()
        db.refresh(c)
        src_id = c.id
        db.add(WebsiteSnapshot(customer_id=src_id, website="snapco.com", content="snap content", content_hash="h1"))
        db.add(AnalysisRun(customer_id=src_id, status="success", company_type="EPC", summary="snap summary", created_at=datetime.datetime(2026, 1, 1, 12, 0, 0)))
        db.add(ScoreSnapshot(customer_id=src_id, total_score=66, priority="B", created_at=datetime.datetime(2026, 1, 1, 12, 0, 0)))
        db.commit()
        db.close()

        export = client.get("/api/sync/export").json()

        # 模拟空目标库：清空客户与快照后导入（测试库即目标库）
        db = TestSessionLocal()
        db.query(WebsiteSnapshot).delete()
        db.query(AnalysisRun).delete()
        db.query(ScoreSnapshot).delete()
        db.query(Customer).filter(Customer.company_name == "Snapshot Co").delete()
        db.commit()
        db.close()

        resp = client.post("/api/sync/import", json=export)
        assert resp.status_code == 200, resp.text
        imp = resp.json()["imported"]
        assert imp["customers"] == 1
        assert imp["website_snapshots"] == 1
        assert imp["analysis_runs"] == 1
        assert imp["score_snapshots"] == 1

        db = TestSessionLocal()
        new_c = db.query(Customer).filter(Customer.company_name == "Snapshot Co").first()
        assert new_c is not None
        assert db.query(WebsiteSnapshot).filter(WebsiteSnapshot.customer_id == new_c.id).count() == 1
        run = db.query(AnalysisRun).filter(AnalysisRun.customer_id == new_c.id).first()
        assert run is not None
        assert run.summary == "snap summary"
        assert db.query(ScoreSnapshot).filter(ScoreSnapshot.customer_id == new_c.id).count() == 1
        db.close()

    def test_snapshot_import_idempotent(self):
        """快照导入幂等：重复导入不产生重复快照"""
        from app.database import WebsiteSnapshot

        db = TestSessionLocal()
        c = Customer(company_name="Idem Co", website="idemco.com")
        db.add(c)
        db.commit()
        db.refresh(c)
        db.add(WebsiteSnapshot(customer_id=c.id, content="c", content_hash="h2"))
        db.commit()
        db.close()

        export = client.get("/api/sync/export").json()

        client.post("/api/sync/import", json=export)
        client.post("/api/sync/import", json=export)

        db = TestSessionLocal()
        new_c = db.query(Customer).filter(Customer.company_name == "Idem Co").first()
        assert db.query(WebsiteSnapshot).filter(WebsiteSnapshot.customer_id == new_c.id).count() == 1
        db.close()


class TestDetailSnapshotFallback:
    """V5.3 阶段3：详情读取切快照表（回退兼容）"""

    def test_detail_reads_snapshot_content(self):
        """详情 website_text 优先取最新快照"""
        from app.database import WebsiteSnapshot

        db = TestSessionLocal()
        c = Customer(company_name="Snap Detail", website="snap-detail.com", website_text="OLD TEXT")
        db.add(c)
        db.commit()
        db.refresh(c)
        cid = c.id
        db.add(WebsiteSnapshot(customer_id=cid, content="NEW SNAPSHOT TEXT", content_hash="x"))
        db.commit()
        db.close()

        resp = client.get(f"/api/customers/{cid}")
        assert resp.status_code == 200
        assert resp.json()["website_text"] == "NEW SNAPSHOT TEXT"

    def test_detail_fallback_to_customer_field(self):
        """无快照时回退主表旧字段"""
        db = TestSessionLocal()
        c = Customer(company_name="Old Co", website="oldco.com", website_text="LEGACY TEXT")
        db.add(c)
        db.commit()
        db.refresh(c)
        cid = c.id
        db.close()

        resp = client.get(f"/api/customers/{cid}")
        assert resp.json()["website_text"] == "LEGACY TEXT"

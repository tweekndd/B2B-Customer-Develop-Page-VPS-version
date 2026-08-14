"""
智能分析快照测试（V5.3）
覆盖：快照/分析运行/评分快照写入、幂等去重、失败记录不覆盖、
回填脚本幂等、历史 API
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

from app.database import (
    Base, get_db, Customer, AnalysisRun, ScoreSnapshot, WebsiteSnapshot, User,
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
        "total_score": 75,
        "priority": "B",
    }
    data.update(overrides)
    c = Customer(**data)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


class TestIntelligenceService:
    def test_save_snapshot_dedup(self):
        """官网快照：相同内容自动去重"""
        from app.services.intelligence_service import save_website_snapshot
        db = TestSessionLocal()
        c = _create_customer(db)
        cid = c.id
        db.close()

        db = TestSessionLocal()
        s1 = save_website_snapshot(db, cid, "content-1", website="aquatech-solutions.com")
        assert s1 is not None
        s2 = save_website_snapshot(db, cid, "content-1", website="aquatech-solutions.com")
        assert s2 is None  # 重复内容去重
        s3 = save_website_snapshot(db, cid, "content-2", website="aquatech-solutions.com")
        assert s3 is not None
        assert db.query(WebsiteSnapshot).filter(WebsiteSnapshot.customer_id == cid).count() == 2
        db.close()

    def test_save_analysis_run_failure_recorded(self):
        """AI 失败也记录（不覆盖历史成功）"""
        from app.services.intelligence_service import save_analysis_run
        db = TestSessionLocal()
        c = _create_customer(db)
        cid = c.id
        db.close()

        db = TestSessionLocal()
        ok = save_analysis_run(db, cid, {"company_type": "EPC", "summary": "Great"}, status="success")
        failed = save_analysis_run(db, cid, None, status="failed", error_message="timeout")
        assert ok.id != failed.id
        runs = db.query(AnalysisRun).filter(AnalysisRun.customer_id == cid).order_by(AnalysisRun.id).all()
        assert len(runs) == 2
        assert runs[0].status == "success"
        assert runs[1].status == "failed"
        assert runs[1].error_message == "timeout"
        db.close()

    def test_save_score_snapshot(self):
        from app.services.intelligence_service import save_score_snapshot
        db = TestSessionLocal()
        c = _create_customer(db)
        cid = c.id
        db.close()

        db = TestSessionLocal()
        snap = save_score_snapshot(db, cid, {
            "industry_score": 20, "project_score": 15, "company_type_score": 18,
            "country_score": 10, "contact_score": 8, "total_score": 71, "priority": "B",
        })
        assert snap.total_score == 71
        assert db.query(ScoreSnapshot).filter(ScoreSnapshot.customer_id == cid).count() == 1
        db.close()

    def test_analysis_summary(self):
        """详情摘要：计数与最新信息"""
        from app.services.intelligence_service import save_analysis_run, get_analysis_summary
        db = TestSessionLocal()
        c = _create_customer(db)
        cid = c.id
        db.close()

        db = TestSessionLocal()
        save_analysis_run(db, cid, {"company_type": "EPC"}, status="success")
        save_analysis_run(db, cid, {"company_type": "Trader"}, status="success")
        save_analysis_run(db, cid, None, status="failed")
        summary = get_analysis_summary(db, cid)
        assert summary["analysis_count"] == 3
        assert summary["success_count"] == 2
        assert summary["failed_count"] == 1
        assert summary["latest_analysis_status"] == "failed"
        db.close()


class TestBackfill:
    def test_backfill_idempotent(self):
        """回填：从主表字段生成快照，重复运行不产生重复记录"""
        db = TestSessionLocal()
        c = _create_customer(db, website_text="legacy text content", ai_raw_json=json.dumps(
            {"company_type": "EPC", "summary": "legacy summary", "buyer_intent_score": 8}
        ))
        cid = c.id
        db.close()

        from app.core.backfill_intelligence import run_backfill
        db = TestSessionLocal()
        r1 = run_backfill(db=db)
        db.close()
        assert r1["snapshots"] >= 1
        assert r1["analysis_runs"] >= 1
        assert r1["score_snapshots"] >= 1

        db = TestSessionLocal()
        assert db.query(WebsiteSnapshot).filter(WebsiteSnapshot.customer_id == cid).count() == 1
        run = db.query(AnalysisRun).filter(AnalysisRun.customer_id == cid).first()
        assert run is not None
        assert run.summary == "legacy summary"
        assert run.company_type == "EPC"
        db.close()

        # 幂等：再次运行全部跳过
        db = TestSessionLocal()
        r2 = run_backfill(db=db)
        db.close()
        assert r2["snapshots"] == 0
        assert r2["analysis_runs"] == 0
        assert r2["score_snapshots"] == 0

        db = TestSessionLocal()
        assert db.query(WebsiteSnapshot).filter(WebsiteSnapshot.customer_id == cid).count() == 1
        assert db.query(AnalysisRun).filter(AnalysisRun.customer_id == cid).count() == 1
        db.close()


class TestHistoryAPI:
    def test_intelligence_history_endpoint(self):
        """历史 API：返回 AI 运行与评分快照"""
        from app.services.intelligence_service import save_analysis_run, save_score_snapshot
        db = TestSessionLocal()
        c = _create_customer(db)
        cid = c.id
        db.close()

        db = TestSessionLocal()
        run = save_analysis_run(db, cid, {
            "company_type": "EPC",
            "summary": "summary x",
            "buyer_intent_score": 9,
            "is_price_inquiry": True,
            "needs_identified": ["pumps"],
        }, status="success")
        save_score_snapshot(db, cid, {
            "total_score": 80, "priority": "A",
            "industry_score": 25, "project_score": 20, "company_type_score": 20,
            "country_score": 10, "contact_score": 5,
        }, analysis_run_id=run.id)
        db.commit()
        db.close()

        resp = client.get(f"/api/customers/{cid}/intelligence-history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["analysis_count"] == 1
        assert data["analysis_runs"][0]["company_type"] == "EPC"
        assert data["analysis_runs"][0]["buyer_intent_score"] == 9
        assert data["analysis_runs"][0]["is_price_inquiry"] is True
        assert data["analysis_runs"][0]["needs_identified"] == ["pumps"]
        assert data["score_snapshots"][0]["total_score"] == 80
        assert data["score_snapshots"][0]["priority"] == "A"

    def test_detail_includes_intelligence_summary(self):
        """详情接口包含 intelligence 摘要"""
        db = TestSessionLocal()
        c = _create_customer(db)
        cid = c.id
        db.close()
        resp = client.get(f"/api/customers/{cid}")
        assert resp.status_code == 200
        assert "intelligence" in resp.json()
        assert resp.json()["intelligence"]["analysis_count"] == 0

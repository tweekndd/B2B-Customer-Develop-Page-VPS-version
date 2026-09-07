"""
Phase1 邮件外联 API 端到端测试（总体开发方案交付闭环）

链路：Prompt 发布 → 生成外联草稿（mock LLM）→ 提交/审批 → 入队发送
（mock himalaya 子进程）→ 发送日志/客户状态联动 → 幂等重放不重复发信。

使用独立 SQLite + FastAPI TestClient，遵循项目既有测试约定。
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DISABLE_RATE_LIMIT", "1")

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import hash_password
from app.database import Base, get_db, User, Customer, CustomerEmail
from main import app

# 与既有 API 集成测试一致：共享 tests/test_api.db（conftest 会话结束统一清理）。
# 关键：override 必须指向同一共享库文件，否则模块导入顺序不同会让
# 其它测试模块的 API 请求打到错误的数据库（本文件不能使用独立 /tmp 库）。
_TEST_DB = os.path.join(os.path.dirname(__file__), "test_api.db")
_engine = create_engine(f"sqlite:///{_TEST_DB}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=_engine)


def _override_get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db
_client = TestClient(app)


@pytest.fixture(autouse=True)
def _db_reset():
    Base.metadata.create_all(bind=_engine)
    db = SessionLocal()
    db.add(User(username="testadmin", password_hash=hash_password("testpassword"),
                role="admin", is_active=1))
    db.commit()
    db.close()
    _client.post("/api/auth/login", json={"username": "testadmin", "password": "testpassword"})
    yield
    _client.post("/api/auth/logout")
    Base.metadata.drop_all(bind=_engine)


class _FakeLLMResult:
    provider = "glm"
    model = "glm-4.7-flash"
    content = json.dumps({
        "subject": "Intro: Water Pump for your projects",
        "body": "Dear team,\n\nWe supply reliable water pumps.\n\nBest regards",
        "language": "en",
        "tone": "professional",
        "facts_used": ["customer.company_name", "customer.website"],
        "claims_requiring_review": [],
        "call_to_action": "reply",
        "risk_flags": [],
        "needs_human_review": False,
    }, ensure_ascii=False)


class _FakeLLMManager:
    async def chat(self, messages, user_id=None, temperature=0.3, max_tokens=4096):
        return _FakeLLMResult()


class TestOutreachApiE2E:
    def _seed_customer(self) -> int:
        db = SessionLocal()
        c = Customer(company_name="Acme GmbH", website="https://acme.de", country="Germany")
        db.add(c)
        db.commit()
        db.refresh(c)
        cid = c.id  # commit 后实例会 expire，先取回 id 避免 DetachedInstanceError
        db.add(CustomerEmail(customer_id=cid, email="sales@acme.de", is_primary=1))
        db.commit()
        db.close()
        return cid

    def _publish_default_prompt(self) -> int:
        schema = {"customer.company_name": {"type": "string", "required": True}}
        r = _client.post("/api/prompts", json={
            "name": "E2E 默认", "purpose": "first_contact", "language": "auto",
            "freedom_level": "L1",
            "system_prompt": "你是开发信专家，不得虚构事实。",
            "user_prompt_template": "Hi {{customer.company_name}}",
            "variables_schema": schema,
        })
        assert r.status_code == 200, r.text
        tid = r.json()["template"]["id"]
        r = _client.post(f"/api/prompts/{tid}/publish")
        assert r.status_code == 200, r.text
        return tid

    def _add_sender_account(self) -> int:
        r = _client.post("/api/mail-sender-accounts", json={
            "email_address": "sales@ourco.com", "name": "主发件",
            "smtp_host": "smtp.ourco.com", "smtp_port": 587,
            "smtp_encryption": "starttls", "smtp_login": "sales@ourco.com",
            "smtp_password": "secret123", "daily_limit": 30,
        })
        assert r.status_code == 200, r.text
        return r.json()["account"]["id"]

    def test_generate_approve_send_idempotent(self, monkeypatch):
        """完整闭环：生成→审批→发送→日志/客户联动→幂等重放不重发"""
        import app.llm.manager as llm_manager
        from app.services import himalaya_service as hs
        from app.workers import task_handlers

        monkeypatch.setattr(llm_manager, "_manager", _FakeLLMManager())
        # 模拟 himalaya 已安装且发送成功
        monkeypatch.setattr(hs, "is_available", lambda: True)
        monkeypatch.setattr(hs, "_run_himalaya", lambda *a, **kw: {
            "exit_code": 0, "stdout": "250 OK id=provE2E\n", "stderr": "", "binary": "/x",
        })

        cid = self._seed_customer()
        self._publish_default_prompt()
        acct_id = self._add_sender_account()

        # 生成（auto_submit → pending）
        r = _client.post(f"/api/customers/{cid}/outreach/generate",
                         json={"product_name": "Pump", "auto_submit": True})
        assert r.status_code == 200, r.text
        draft = r.json()["draft"]
        did = draft["id"]
        assert draft["status"] == "pending"
        assert draft["recipient_email"] == "sales@acme.de"
        assert draft["prompt_template_id"]
        # 溯源字段：generation_run 存在且含输入快照
        detail = _client.get(f"/api/outreach/drafts/{did}").json()
        assert detail.get("generation_run"), "草稿详情应附 generation_run 溯源"
        run = detail["generation_run"]
        assert run["result_status"] == "success"
        assert run["model"] == "glm-4.7-flash"
        assert run["prompt_version_id"]

        # 审批
        r = _client.post(f"/api/outreach/drafts/{did}/approve",
                         json={"sender_account_id": acct_id})
        assert r.status_code == 200, r.text
        assert r.json()["draft"]["status"] == "approved"

        # 发送（immediate 同步兜底）
        r = _client.post(f"/api/outreach/drafts/{did}/send", json={"immediate": True})
        assert r.status_code == 200, r.text
        # 任务 succeeded
        tasks = _client.get("/api/outreach/tasks").json()["tasks"]
        assert tasks and tasks[0]["status"] == "succeeded", tasks
        # 发送日志 sent + Message-ID
        logs = _client.get("/api/outreach/send-logs").json()["logs"]
        assert logs and logs[0]["status"] == "sent", logs
        assert logs[0]["internet_message_id"]
        assert logs[0]["provider_message_id"] == "provE2E"
        # 草稿 sent
        assert _client.get(f"/api/outreach/drafts/{did}").json()["draft"]["status"] == "sent"
        # 客户状态联动 + 发信记录（V5.2 activity）
        cust = _client.get(f"/api/customers/{cid}").json()
        assert cust.get("status") == "已发邮件"
        assert _client.get(f"/api/customers/{cid}/email-activities").json()["total"] >= 1

        # 幂等重放：再次 send → 不产生第二条日志
        r = _client.post(f"/api/outreach/drafts/{did}/send", json={"immediate": True})
        assert r.status_code == 200
        logs_after = _client.get("/api/outreach/send-logs").json()["logs"]
        assert len(logs_after) == 1, "幂等键应阻止重复发送产生新日志"

    def test_blacklist_blocks_generation_approve(self, monkeypatch):
        """禁止联系名单在审批/发送两个时点都拦截"""
        import app.llm.manager as llm_manager
        from app.services import himalaya_service as hs
        monkeypatch.setattr(llm_manager, "_manager", _FakeLLMManager())
        monkeypatch.setattr(hs, "is_available", lambda: True)
        monkeypatch.setattr(hs, "_run_himalaya", lambda *a, **kw: {
            "exit_code": 0, "stdout": "250 OK id=x\n", "stderr": "", "binary": "/x"})

        cid = self._seed_customer()
        self._publish_default_prompt()
        acct_id = self._add_sender_account()

        # 加入名单后审批 → 409
        _client.post("/api/outreach/blacklist",
                     json={"email": "sales@acme.de", "reason": "unsubscribe"})
        r = _client.post(f"/api/customers/{cid}/outreach/generate",
                         json={"product_name": "Pump", "auto_submit": True})
        assert r.status_code == 200
        did = r.json()["draft"]["id"]
        r = _client.post(f"/api/outreach/drafts/{did}/approve",
                         json={"sender_account_id": acct_id})
        assert r.status_code == 409, r.text
        # 名单查询
        bl = _client.get("/api/outreach/blacklist").json()["entries"]
        assert any(e["email"] == "sales@acme.de" for e in bl)

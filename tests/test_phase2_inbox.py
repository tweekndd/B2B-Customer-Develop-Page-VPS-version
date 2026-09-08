"""
Phase2 回复同步与 AI 分类测试（总体开发方案 Phase2 交付标准）

覆盖：
1. customer_status 客户状态机（只升不降 / 无效线索终态 / 审计留痕）
2. reply_classifier 规则预判（退信/退订/自动回复）+ LLM 结构化归一 + 规则兜底
3. inbox_sync_service 收件同步（mock imaplib）：去重 / 客户匹配 / 正文与附件外置
   / 退信退订→黑名单 / rfq→待办 / 状态机联动
4. task_service.rerun_task：失败/取消任务人工重跑 + 事件流水
5. inbox API（列表 / 详情 / 待办 / 手动同步）
6. model 注册完整性（mail_messages 等新表在 Base.metadata）
"""
import datetime
import email as email_lib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DISABLE_RATE_LIMIT", "1")

from email.message import EmailMessage
from unittest import mock  # noqa: E402

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.auth import hash_password  # noqa: E402
from app.database import (  # noqa: E402
    Base, get_db, User, Customer, CustomerEmail,
    MailSenderAccount, MailMessage, MailAttachment, CustomerTodo,
    CustomerStatusHistory, AutomationTask, AutomationTaskEvent,
)
from app.models.automation import TASK_QUEUED, TASK_FAILED, TASK_CANCELLED  # noqa: E402
from app.models.outreach_send import UnsubscribeBlacklist  # noqa: E402
from main import app  # noqa: E402

# 与既有测试共享 test_api.db（避免 app.dependency_overrides 指向不同库）
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

from fastapi.testclient import TestClient  # noqa: E402
_client = TestClient(app)


@pytest.fixture(autouse=True)
def _db_reset():
    Base.metadata.create_all(bind=_engine)
    db = SessionLocal()
    db.add(User(username="phase2admin", password_hash=hash_password("phase2password"),
                role="admin", is_active=1))
    db.commit()
    db.close()
    _client.post("/api/auth/login", json={"username": "phase2admin", "password": "phase2password"})
    yield
    _client.post("/api/auth/logout")
    Base.metadata.drop_all(bind=_engine)


def _make_sender_account(db, email_address="postmaster@mycompany.com", enabled=1) -> MailSenderAccount:
    from app.services.user_config import encrypt_secret
    acc = MailSenderAccount(
        user_id=1, name="t", email_address=email_address, display_name="t",
        provider="himalaya",
        smtp_host="smtp.example.com", smtp_port=587, smtp_encryption="starttls",
        smtp_login=email_address, smtp_password_encrypted=encrypt_secret("secret"),
        imap_host="imap.example.com", imap_port=993, imap_encryption="tls",
        imap_login=email_address, imap_password_encrypted=encrypt_secret("secret"),
        daily_limit=0, enabled=enabled, status="active",
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return acc


def _seed_customer(db) -> Customer:
    c = Customer(company_name="Apex Pumps Ltd", website="https://apexpumps.com",
                 status="已发邮件")
    db.add(c)
    db.commit()
    db.refresh(c)
    ce = CustomerEmail(customer_id=c.id, email="sales@apexpumps.com", source="manual")
    db.add(ce)
    db.commit()
    db.refresh(c)
    return c


def _raw_reply(subject="Re: our water pump offer", body="Please send quote for 5000 units.\nDelivery to Rotterdam.",
               from_addr="sales@apexpumps.com", msg_id="<m1@apexpumps.com>",
               extra_headers=None) -> bytes:
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = "postmaster@mycompany.com"
    msg["Subject"] = subject
    msg["Date"] = "Mon, 08 Sep 2026 10:00:00 +0000"
    msg["Message-ID"] = msg_id
    msg["In-Reply-To"] = "<our-msg-1@mycompany.com>"
    for k, v in (extra_headers or {}).items():
        msg[k] = v
    msg.set_content(body)
    return msg.as_bytes()


def _raw_bounce() -> bytes:
    msg = EmailMessage()
    msg["From"] = "mailer-daemon@mx.apexpumps.com"
    msg["To"] = "postmaster@mycompany.com"
    msg["Subject"] = "Mail delivery failed: returning message to sender"
    msg["Date"] = "Mon, 08 Sep 2026 09:00:00 +0000"
    msg["Message-ID"] = "<b1@mx.apexpumps.com>"
    msg["X-Failed-Recipients"] = "notexist@apexpumps.com"
    msg.set_content("Your message could not be delivered.\n550 user unknown")
    return msg.as_bytes()


def _raw_unsubscribe() -> bytes:
    return _raw_reply(subject="Unsubscribe", body="Please remove me from your list. Thanks.",
                      msg_id="<unsub@apexpumps.com>")


def _mime_with_attachment() -> bytes:
    msg = EmailMessage()
    msg["From"] = "sales@apexpumps.com"
    msg["To"] = "postmaster@mycompany.com"
    msg["Subject"] = "Re: specs please"
    msg["Date"] = "Mon, 08 Sep 2026 11:00:00 +0000"
    msg["Message-ID"] = "<m2@apexpumps.com>"
    msg["References"] = "<our-msg-1@mycompany.com>"
    msg.set_content("Please send specs. Attached our requirements.")
    msg.add_attachment(b"hello attachment", maintype="text", subtype="plain",
                       filename="req.txt")
    return msg.as_bytes()


class _FakeIMAP:
    """mock imaplib：返回固定 UID 列表 + 按 UID 返回原始邮件。"""

    def __init__(self, uid_to_raw: dict, uidvalidity="12345"):
        self.uid_to_raw = {str(k).encode(): v for k, v in uid_to_raw.items()}
        self.uidvalidity = uidvalidity
        self.selected = False
        self.logged_out = False

    def select(self, mailbox, readonly=True):
        self.selected = True
        return "OK", [f"UIDVALIDITY {self.uidvalidity}".encode()]

    def uid(self, command, *args):
        cmd = (command or "").lower()
        if cmd == "search":
            return "OK", [b" ".join(sorted(self.uid_to_raw.keys()))]
        if cmd == "fetch":
            uid = args[0] if isinstance(args[0], bytes) else str(args[0]).encode()
            return "OK", [(b"1 (RFC822)", self.uid_to_raw[uid])]
        return ("NO", b"bad command")

    def logout(self):
        self.logged_out = True
        return "BYE"


def _patch_imap(uid_to_raw: dict, uidvalidity="12345"):
    fake = _FakeIMAP(uid_to_raw, uidvalidity)
    return (
        mock.patch("app.services.inbox_sync_service._connect_imap", return_value=fake),
        fake,
    )


# ═══════════════════════════════════════════════════════════════
# 1. customer_status 状态机
# ═══════════════════════════════════════════════════════════════

class TestCustomerStatus:
    def test_upgrade_allowed_and_history(self):
        from app.services import customer_status as cs
        db = SessionLocal()
        try:
            c = Customer(company_name="C1", status="已发邮件")
            db.add(c)
            db.commit()
            db.refresh(c)
            changed = cs.transition_customer_status(
                db, c, "已回复", trigger="mail_reply",
                source_message_id=9, note="收到回复")
            assert changed is True
            assert c.status == "已回复"
            hist = db.query(CustomerStatusHistory).filter(
                CustomerStatusHistory.customer_id == c.id).all()
            assert len(hist) == 1 and hist[0].new_status == "已回复"
        finally:
            db.close()

    def test_no_downgrade_blocks_auto(self):
        from app.services import customer_status as cs
        db = SessionLocal()
        try:
            c = Customer(company_name="C2", status="已回复")
            db.add(c)
            db.commit()
            db.refresh(c)
            with pytest.raises(cs.StatusTransitionError):
                cs.transition_customer_status(db, c, "已发邮件", trigger="mail_send")
            assert c.status == "已回复"  # 未降级
            # 人工 force 允许降级（留痕）
            cs.transition_customer_status(db, c, "待联系", trigger="manual", force=True)
            assert c.status == "待联系"
        finally:
            db.close()

    def test_invalid_lead_is_terminal_for_auto(self):
        from app.services import customer_status as cs
        db = SessionLocal()
        try:
            c = Customer(company_name="C3", status="待联系")
            db.add(c)
            db.commit()
            db.refresh(c)
            # 自动不进无效线索
            with pytest.raises(cs.StatusTransitionError):
                cs.transition_customer_status(db, c, "无效线索", trigger="bounce")
            assert c.status == "待联系"
            # 人工可激活无效线索
            cs.transition_customer_status(db, c, "无效线索", trigger="manual", force=True)
            assert c.status == "无效线索"
            # 人工重新激活
            cs.revive_customer(db, c)
            assert c.status == "待联系"
        finally:
            db.close()

    def test_same_status_records_audit_no_change(self):
        from app.services import customer_status as cs
        db = SessionLocal()
        try:
            c = Customer(company_name="C4", status="已回复")
            db.add(c)
            db.commit()
            db.refresh(c)
            changed = cs.transition_customer_status(db, c, "已回复", trigger="mail_reply")
            assert changed is False  # 状态未变，但留痕
            hist = db.query(CustomerStatusHistory).filter(
                CustomerStatusHistory.customer_id == c.id).all()
            assert len(hist) == 1
        finally:
            db.close()


# ═══════════════════════════════════════════════════════════════
# 2. reply_classifier
# ═══════════════════════════════════════════════════════════════

class TestReplyClassifier:
    def test_rule_bounce(self):
        from app.services import reply_classifier as rc
        r = rc.rule_preclassify("Mail delivery failed", "550 user unknown", {}, "")
        assert r and r["intent"] == "bounce" and r["next_action"] == "blacklist"
        # 报文结构识别
        r2 = rc.rule_preclassify("Delivery Status Notification", "", {},
                                 "multipart/report")
        assert r2 and r2["intent"] == "bounce"

    def test_rule_unsubscribe(self):
        from app.services import reply_classifier as rc
        r = rc.rule_preclassify("Unsubscribe", "Please remove me from your list", {}, "")
        assert r and r["intent"] == "unsubscribe"
        r2 = rc.rule_preclassify("", "退订，不要再发了", {}, "")
        assert r2 and r2["intent"] == "unsubscribe"

    def test_rule_out_of_office(self):
        from app.services import reply_classifier as rc
        r = rc.rule_preclassify("Out of office", "I am on vacation until Friday", {}, "")
        assert r and r["intent"] == "out_of_office"

    def test_llm_classify_invalid_falls_back(self):
        import asyncio
        from app.services import reply_classifier as rc
        # LLM 返回非法 JSON → sanitize None → 规则兜底
        with mock.patch("app.llm.manager.get_llm_manager") as mgr:
            inst = mock.MagicMock()
            result = mock.MagicMock()
            result.output_text = "not json at all"
            async def _chat(*a, **k):
                return result
            inst.chat = _chat
            mgr.return_value = inst
            out = asyncio.run(rc.classify_reply("Re: quote", "Need price for pumps",
                                                user_id=1, llm_available=True))
            assert out["intent"] in rc._ALLOWED_INTENTS
            assert out.get("needs_human_review") is not None

    def test_sanitize_normalizes(self):
        from app.services import reply_classifier as rc
        out = rc._sanitize({"intent": "RFQ", "confidence": "0.9",
                            "product": "pump", "missing_fields": "notlist",
                            "next_action": "manual_reply", "needs_human_review": 1})
        assert out["intent"] == "rfq"
        assert out["confidence"] == 0.9
        assert out["missing_fields"] == []
        assert out["next_action"] == "manual_reply"
        assert rc._sanitize({"intent": "hacker"}) is None
        assert rc._sanitize([1, 2]) is None

    def test_fallback_rfq(self):
        from app.services import reply_classifier as rc
        out = rc._fallback_classify("", "Please send quote for 5000 units")
        assert out["intent"] == "rfq" and out["next_action"] == "create_todo"


# ═══════════════════════════════════════════════════════════════
# 3. inbox_sync_service
# ═══════════════════════════════════════════════════════════════

class TestInboxSync:
    @pytest.fixture(autouse=True)
    def _no_llm(self):
        """同步测试避开真实 LLM 调用：LLM 固定返回非法 JSON → 走规则兜底。
        保证 rfq/bounce/unsubscribe 分类确定性。"""
        from app.services import reply_classifier as rc
        inst = mock.MagicMock()

        async def _chat(*a, **k):
            res = mock.MagicMock()
            res.output_text = "not-json"
            return res

        inst.chat = _chat
        with mock.patch.object(rc, "get_llm_manager", return_value=inst):
            yield

    def test_sync_matches_and_creates_todo_externalizes(self):
        db = SessionLocal()
        try:
            acc = _make_sender_account(db)
            customer = _seed_customer(db)
            raw = _raw_reply()
            patch, fake = _patch_imap({b"1": raw})
            with patch:
                from app.services import inbox_sync_service as iss
                stats = iss.sync_account(db, acc.id)
            assert stats["new"] == 1 and stats["matched"] == 1

            m = db.query(MailMessage).filter(MailMessage.mail_account_id == acc.id).first()
            assert m is not None
            assert m.customer_id == customer.id
            assert m.provider_message_id == "<m1@apexpumps.com>"
            # 正文外置
            assert m.body_text_object_id
            # 正文内容可读回
            from app.services import object_storage as objsv
            body_text = objsv.get_object(db, m.body_text_object_id).decode()
            assert "quote for 5000 units" in body_text
            assert m.raw_mime_object_id
            assert m.classification in ("rfq", "interest", "question")

            # 待办创建
            todo = db.query(CustomerTodo).filter(
                CustomerTodo.customer_id == customer.id).first()
            assert todo is not None
            assert todo.message_id == m.id
            assert todo.status == "open"

            # 状态联动：已发邮件 → 已回复
            db.refresh(customer)
            assert customer.status == "已回复"
        finally:
            db.close()

    def test_dedupe_no_duplicates(self):
        db = SessionLocal()
        try:
            acc = _make_sender_account(db)
            _seed_customer(db)
            raw = _raw_reply()
            patch, fake = _patch_imap({b"1": raw})
            from app.services import inbox_sync_service as iss
            with patch:
                s1 = iss.sync_account(db, acc.id)
                s2 = iss.sync_account(db, acc.id)
            assert s1["new"] == 1
            assert s2["new"] == 0
            assert db.query(MailMessage).filter(
                MailMessage.mail_account_id == acc.id).count() == 1
        finally:
            db.close()

    def test_attachment_externalized(self):
        db = SessionLocal()
        try:
            acc = _make_sender_account(db)
            c = _seed_customer(db)
            raw = _mime_with_attachment()
            patch, fake = _patch_imap({b"1": raw})
            from app.services import inbox_sync_service as iss
            with patch:
                iss.sync_account(db, acc.id)
            m = db.query(MailMessage).filter(MailMessage.mail_account_id == acc.id).first()
            atts = db.query(MailAttachment).filter(MailAttachment.message_id == m.id).all()
            assert len(atts) == 1
            assert atts[0].filename == "req.txt"
            assert atts[0].sha256
            from app.services import object_storage as objsv
            content = objsv.get_object(db, atts[0].object_id)
            assert content == b"hello attachment"
        finally:
            db.close()

    def test_bounce_adds_blacklist(self):
        db = SessionLocal()
        try:
            acc = _make_sender_account(db)
            customer = _seed_customer(db)
            raw = _raw_bounce()
            patch, fake = _patch_imap({b"1": raw})
            from app.services import inbox_sync_service as iss
            with patch:
                iss.sync_account(db, acc.id)
            bl = db.query(UnsubscribeBlacklist).filter(
                UnsubscribeBlacklist.email == "notexist@apexpumps.com").first()
            assert bl is not None and bl.reason == "bounce"
            m = db.query(MailMessage).filter(MailMessage.mail_account_id == acc.id).first()
            assert m.classification == "bounce"
        finally:
            db.close()

    def test_unsubscribe_adds_blacklist(self):
        db = SessionLocal()
        try:
            acc = _make_sender_account(db)
            _seed_customer(db)
            raw = _raw_unsubscribe()
            patch, fake = _patch_imap({b"1": raw})
            from app.services import inbox_sync_service as iss
            with patch:
                iss.sync_account(db, acc.id)
            bl = db.query(UnsubscribeBlacklist).filter(
                UnsubscribeBlacklist.email == "sales@apexpumps.com").first()
            assert bl is not None and bl.reason == "unsubscribe"
            m = db.query(MailMessage).filter(MailMessage.mail_account_id == acc.id).first()
            assert m.classification == "unsubscribe"
        finally:
            db.close()

    def test_skips_disabled_account_without_imap(self):
        db = SessionLocal()
        try:
            from app.services.user_config import encrypt_secret
            acc = MailSenderAccount(
                user_id=1, email_address="noreply@x.com", provider="himalaya",
                smtp_host="smtp.x.com", smtp_port=587, smtp_encryption="starttls",
                smtp_login="noreply@x.com",
                smtp_password_encrypted=encrypt_secret("p"),
                imap_host=None, daily_limit=0, enabled=1, status="active",
            )
            db.add(acc)
            db.commit()
            db.refresh(acc)
            from app.services import inbox_sync_service as iss
            assert iss.adapter_for_sync(acc) is False  # 无 IMAP 主机
            stats = iss.sync_account(db, acc.id)
            assert stats["new"] == 0
        finally:
            db.close()

    def test_connection_failure_fail_open(self):
        db = SessionLocal()
        try:
            acc = _make_sender_account(db)
            with mock.patch("app.services.inbox_sync_service._connect_imap",
                            side_effect=Exception("conn refused")):
                from app.services import inbox_sync_service as iss
                stats = iss.sync_account(db, acc.id)
            assert stats["error"] == 1 and stats["new"] == 0
            db.refresh(acc)
            assert "conn refused" in (acc.last_error or "")
        finally:
            db.close()


# ═══════════════════════════════════════════════════════════════
# 4. task rerun + events
# ═══════════════════════════════════════════════════════════════

class TestTaskRerun:
    def _task(self, db, status):
        t = AutomationTask(task_type="send_email", status=status, payload_json="{}",
                           attempts=1, max_attempts=5)
        db.add(t)
        db.commit()
        db.refresh(t)
        return t

    def test_rerun_failed_task(self):
        from app.services import task_service as ts
        db = SessionLocal()
        try:
            t = self._task(db, TASK_FAILED)
            t.error_message = "boom"
            db.commit()
            t2 = ts.rerun_task(db, t.id)
            assert t2.status == TASK_QUEUED
            assert t2.attempts == 0 and t2.error_message is None
            events = ts.list_task_events(db, t.id)
            assert any(e["event_type"] == "rerun" for e in events)
        finally:
            db.close()

    def test_cannot_rerun_succeeded(self):
        from app.services import task_service as ts
        db = SessionLocal()
        try:
            t = self._task(db, "succeeded")
            with pytest.raises(ts.TaskError):
                ts.rerun_task(db, t.id)
        finally:
            db.close()

    def test_cancel_records_event(self):
        from app.services import task_service as ts
        db = SessionLocal()
        try:
            t = self._task(db, TASK_QUEUED)
            ts.cancel_task(db, t.id)
            assert t.status == TASK_CANCELLED
            assert any(e["event_type"] == "cancelled" for e in ts.list_task_events(db, t.id))
        finally:
            db.close()


# ═══════════════════════════════════════════════════════════════
# 5. inbox API
# ═══════════════════════════════════════════════════════════════

class TestInboxAPI:
    def test_list_and_detail(self):
        db = SessionLocal()
        try:
            acc = _make_sender_account(db)
            customer = _seed_customer(db)
            expected_name = customer.company_name
            raw = _raw_reply()
            patch, fake = _patch_imap({b"1": raw})
            with patch:
                from app.services import inbox_sync_service as iss
                iss.sync_account(db, acc.id)
        finally:
            db.close()

        r = _client.get("/api/inbox/messages")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] >= 1
        msg = data["messages"][0]
        assert msg["customer_name"] == expected_name
        assert msg["classification"] in ("rfq", "interest", "question")

        # 详情（正文按需加载）
        detail = _client.get(f"/api/inbox/messages/{msg['id']}")
        assert detail.status_code == 200
        body = detail.json()
        assert "quote for 5000 units" in body["body"]
        assert body["attachments"] == []
        # 下载不存在附件 404
        assert _client.get(f"/api/inbox/messages/{msg['id']}/attachments/9999").status_code == 404

    def test_todo_lifecycle(self):
        db = SessionLocal()
        try:
            acc = _make_sender_account(db)
            c = _seed_customer(db)
            raw = _raw_reply()
            patch, fake = _patch_imap({b"1": raw})
            with patch:
                from app.services import inbox_sync_service as iss
                iss.sync_account(db, acc.id)
        finally:
            db.close()
        r = _client.get("/api/inbox/todos")
        todos = r.json()["todos"]
        assert len(todos) >= 1
        tid = todos[0]["id"]
        assert _client.post(f"/api/inbox/todos/{tid}/done").json()["message"]
        assert _client.post(f"/api/inbox/todos/{tid}/reopen").json()["message"]
        assert _client.post(f"/api/inbox/todos/{tid}/dismiss").json()["message"]
        rr = _client.get("/api/inbox/todos?status=dismissed")
        assert any(t["id"] == tid for t in rr.json()["todos"])

    def test_manual_sync_and_handle(self):
        db = SessionLocal()
        try:
            acc = _make_sender_account(db)
            c = _seed_customer(db)
            raw = _raw_reply()
            patch, fake = _patch_imap({b"1": raw})
            with patch:
                r = _client.post(f"/api/inbox/sync?account_id={acc.id}")
            assert r.status_code == 200
            listed = _client.get("/api/inbox/messages").json()["messages"]
            mid = listed[0]["id"]
            assert _client.post(f"/api/inbox/messages/{mid}/handle").json()["message"]
            listed2 = _client.get("/api/inbox/messages?handled=handled").json()["messages"]
            assert any(m["id"] == mid for m in listed2)
            assert _client.post(f"/api/inbox/messages/{mid}/ignore").json()["message"]
        finally:
            db.close()


# ═══════════════════════════════════════════════════════════════
# 6. 新表在 Base.metadata 注册完整
# ═══════════════════════════════════════════════════════════════

class TestPhase2ModelsRegistered:
    def test_new_tables_in_metadata(self):
        from app.database import Base as B
        tables = set(B.metadata.tables.keys())
        for t in ("mail_messages", "mail_attachments", "customer_todos",
                  "customer_status_history", "automation_task_events"):
            assert t in tables
        # MailSenderAccount 新增 IMAP 游标列由 003 migration / init_db 提供，
        # create_all 用 model 定义（含三列）→ 断言 Model 层面有该字段
        from app.models.outreach_send import MailSenderAccount as MSA
        assert hasattr(MSA, "last_inbox_uid")
        assert hasattr(MSA, "last_inbox_uid_validity")
        assert hasattr(MSA, "last_inbox_sync_at")
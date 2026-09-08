"""收件箱同步服务（Phase2 新增：方案 5.4 / 13.2）

编排（交付标准：新回复 → 去重 → 匹配客户 → AI 分类 → 创建待办/黑名单）：
  遍历启用且配置了 IMAP 的 mail_sender_accounts
   → imaplib 增量拉取 INBOX（UIDVALIDITY + 最大 UID 游标）
   → 解析 MIME（正文提取 / 附件外置对象存储 / 原始 MIME 外置）
   → 按 Message-ID 去重（provider + mail_account_id + provider_message_id 唯一）
   → 域名匹配客户（复用 mail_sync_service 的匹配逻辑）
   → 规则预判退信/退订 → LLM 分类 → 规则兜底
   → bounce/unsubscribe 进黑名单（unsubscribe_blacklist）
   → 动作类意图创建 customer_todos
   → 客户状态机联动（已发邮件 → 已回复；退信/退订 → 无效线索）

仅在启用 IMAP 且凭据可解的账户上运行；单个账户同步失败不影响其它账户
（fail-open：账户 last_error 回写，主流程不中断）。
"""
import datetime
import email as email_lib
import email.header
import email.utils
import hashlib
import imaplib
import json
import logging
import os
import re
from email.message import Message as EmailMessage
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.database import (
    Customer,
    CustomerEmail,
    MailSenderAccount,
    MailMessage,
    MailAttachment,
)
from app.models.inbox import (
    TODO_RESPOND, TODO_FOLLOW_UP, TODO_QUOTE_REQUEST, TODO_COMPLAINT, TODO_OTHER,
    TODO_OPEN,
    CustomerTodo,
)
from app.models.outreach_send import UnsubscribeBlacklist
from app.services import object_storage as objsv
from app.services import reply_classifier as rc
from app.services import customer_status as cs
from app.services.email_domain_matcher import (
    extract_email_domain,
    extract_registrable_domain,
    match_domain,
)
from app.services.user_config import decrypt_secret

logger = logging.getLogger("inbox_sync")

# 单次同步处理上限（防超时/刷屏）
_MAX_PER_RUN = 200

# 黑名单来源标记
_BLACKLIST_REASON_BOUNCE = "bounce"
_BLACKLIST_REASON_UNSUBSCRIBE = "unsubscribe"


# ═══════════════════════════════════════════════════════════════════════
# IMAP 连接与增量游标
# ═══════════════════════════════════════════════════════════════════════

def _imap_credentials(account: MailSenderAccount) -> Optional[Tuple[str, str]]:
    """解析 IMAP 登录名/密码；未配置 IMAP 主机返回 None。"""
    if not (account.imap_host or "").strip():
        return None
    login = (account.imap_login or "").strip() or (account.email_address or "").strip()
    imap_password = decrypt_secret(account.imap_password_encrypted)
    smtp_password = decrypt_secret(account.smtp_password_encrypted)
    password = imap_password or smtp_password
    if not password:
        return None
    return login, password


def _connect_imap(account: MailSenderAccount, timeout: int = 30):
    """建立 imaplib 连接（tls 隐式 / starttls / none）。测试可 monkeypatch。"""
    login, password = _imap_credentials(account)
    if not login:
        raise ValueError("IMAP 凭据不可用")
    encryption = (account.imap_encryption or "tls").lower()
    port = int(account.imap_port or (993 if encryption == "tls" else 143))
    if encryption == "tls":
        client = imaplib.IMAP4_SSL(account.imap_host, port, timeout=timeout)
    else:
        client = imaplib.IMAP4(account.imap_host, port, timeout=timeout)
        if encryption == "starttls":
            client.starttls()
    client.login(login, password)
    return client


def _load_last_cursor(db: Session, account: MailSenderAccount) -> Tuple[Optional[str], Optional[str]]:
    """返回 (uidvalidity, last_uid)。"""
    return account.last_inbox_uid_validity, account.last_inbox_uid


def _save_cursor(db: Session, account: MailSenderAccount, uidvalidity: str, last_uid: str) -> None:
    account.last_inbox_uid_validity = uidvalidity
    account.last_inbox_uid = str(last_uid)
    account.last_inbox_sync_at = datetime.datetime.utcnow()


# ═══════════════════════════════════════════════════════════════════════
# MIME 解析
# ═══════════════════════════════════════════════════════════════════════

def _decode_header_value(value) -> str:
    if not value:
        return ""
    try:
        parts = email.header.decode_header(value)
        return "".join(
            part.decode(enc or "utf-8", errors="replace") if isinstance(part, bytes) else part
            for part, enc in parts
        )
    except Exception:
        return str(value)


def _extract_main_text(msg: EmailMessage) -> str:
    """提取正文纯文本（优先 text/plain；无则 html 转纯文本占位）。"""
    texts: List[str] = []
    for part in msg.walk():
        ct = (part.get_content_type() or "").lower()
        if part.is_multipart():
            continue
        if ct == "text/plain":
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                try:
                    texts.append(payload.decode(charset, errors="replace"))
                except Exception:
                    texts.append(payload.decode("utf-8", errors="replace"))
    if texts:
        return "\n\n".join(texts)
    for part in msg.walk():
        if (part.get_content_type() or "").lower() == "text/html":
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                try:
                    html = payload.decode(charset, errors="replace")
                except Exception:
                    html = payload.decode("utf-8", errors="replace")
                html = re.sub(r"<[^>]+>", " ", html)
                return re.sub(r"\s+", " ", html).strip()[:4000]
    return ""


def _parse_attachments(msg: EmailMessage) -> List[dict]:
    """提取附件元数据（内容解码为 bytes，由调用方外置）。"""
    results: List[dict] = []
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() != "attachment":
            continue
        filename = _decode_header_value(part.get_filename()) or f"attachment_{len(results)}"
        payload = part.get_payload(decode=True) or b""
        results.append({
            "filename": filename,
            "content_type": part.get_content_type() or "application/octet-stream",
            "data": payload,
        })
    return results


def _parse_date(value) -> Optional[datetime.datetime]:
    if not value:
        return None
    dt = email.utils.parsedate_to_datetime(value)
    if dt is None:
        return None
    if dt.tzinfo:
        dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return dt


def _extract_addresses(raw: str) -> List[str]:
    """从地址头提取邮箱地址列表。"""
    if not raw:
        return []
    found = []
    for name, addr in email.utils.getaddresses([raw]):
        addr = (addr or "").strip().lower()
        if addr and addr not in found:
            found.append(addr)
    return found


def _extract_uidvalidity(client) -> Tuple[Optional[str], Optional[str]]:
    """SELECT 后取 UIDVALIDITY；失败返回 None。"""
    try:
        typ, data = client.select("INBOX", readonly=True)
        if typ != "OK":
            return None, None
        uidvalidity = None
        for line in data or []:
            if isinstance(line, bytes):
                m = re.search(rb"UIDVALIDITY (\d+)", line)
                if m:
                    uidvalidity = m.group(1).decode()
        return uidvalidity, None
    except Exception:
        return None, None


def _fetch_uids(client, uidvalidity: Optional[str], last_uid: Optional[str],
                max_results: int = _MAX_PER_RUN) -> List[str]:
    """按游标增量搜索 UID 列表。无游标时全量拉最近 max_results 封。"""
    try:
        if last_uid and uidvalidity:
            command = f"UID {int(last_uid)}:*".encode()
        else:
            command = b"ALL"
        typ, data = client.uid("search", None, command)
        if typ != "OK" or not data or not data[0]:
            return []
        nums = data[0].split()
        if not nums:
            return []
        uids = [n.decode() for n in nums]
        if last_uid and uidvalidity:
            # 过滤掉等于游标的那一封（已在库）
            if uids and uids[0] == last_uid:
                uids = uids[1:]
        if not last_uid:
            uids = uids[-max_results:]
        return uids
    except Exception:
        return []


def _fetch_raw(client, uid: str) -> Optional[bytes]:
    try:
        typ, data = client.uid("fetch", uid.encode() if isinstance(uid, str) else uid, b"(RFC822)")
        if typ != "OK" or not data:
            return None
        for item in data:
            if isinstance(item, tuple) and len(item) >= 1 and isinstance(item[1], bytes):
                return item[1]
        return None
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════
# 客户匹配（复用 mail_sync_service 的域名匹配策略）
# ═══════════════════════════════════════════════════════════════════════

def _load_customer_domains(db: Session) -> Dict[int, dict]:
    customers = db.query(Customer).filter(
        Customer.website.isnot(None), Customer.website != ""
    ).all()
    result = {}
    for c in customers:
        rd = extract_registrable_domain(c.website)
        if not rd:
            continue
        result[c.id] = {
            "website_domain": rd,
            "manual_domains": [
                extract_email_domain(r.email)
                for r in db.query(CustomerEmail).filter(CustomerEmail.customer_id == c.id).all()
                if extract_email_domain(r.email)
            ],
        }
    return result


def _match_customer(db: Session, customer_domains: Dict[int, dict], from_address: str) -> Optional[Tuple[int, str, str]]:
    """按发件人地址匹配客户 → (customer_id, matched_domain, match_type)。"""
    sender_domain = extract_email_domain(from_address)
    if not sender_domain:
        return None
    for customer_id, info in customer_domains.items():
        if match_domain(info["website_domain"], sender_domain):
            return customer_id, sender_domain, "exact_domain"
        if sender_domain in info["manual_domains"]:
            return customer_id, sender_domain, "manual_email"
    return None


# ═══════════════════════════════════════════════════════════════════════
# 落库（去重 + 外置 + 状态/黑名单/待办联动）
# ═══════════════════════════════════════════════════════════════════════

def _existing_message(db: Session, account_id: int, provider_message_id: str) -> Optional[MailMessage]:
    return db.query(MailMessage).filter(
        MailMessage.provider == "imap",
        MailMessage.mail_account_id == account_id,
        MailMessage.provider_message_id == provider_message_id,
    ).first()


def _externalize(db: Session, *payloads) -> List[Optional[str]]:
    """把 bytes 依次写入对象存储，返回 object_key 列表。"""
    keys = []
    for payload in payloads:
        if payload is None:
            keys.append(None)
            continue
        keys.append(objsv.put_object(db, payload, content_type="application/octet-stream").object_key)
    return keys


def _add_blacklist(db: Session, account: MailSenderAccount, address: str, reason: str,
                   source_message_id: str, note: str) -> None:
    email_addr = (address or "").strip().lower()
    if not email_addr:
        return
    domain = None
    if email_addr:
        try:
            domain = email_addr.split("@", 1)[1]
        except IndexError:
            domain = None
    exists = db.query(UnsubscribeBlacklist).filter(UnsubscribeBlacklist.email == email_addr).first()
    if exists:
        return
    db.add(UnsubscribeBlacklist(
        email=email_addr,
        domain=domain,
        reason=reason,
        source=source_message_id,
        source_message_id=source_message_id,
        note=note,
        created_at=datetime.datetime.utcnow(),
    ))


def _create_todo(db: Session, *, customer_id: int, message_id: int, intent: str,
                 summary: str, details: None | Dict = None, priority: int = 2) -> None:
    todo_type = {
        rc.INTENT_RFQ: TODO_QUOTE_REQUEST,
        rc.INTENT_QUESTION: TODO_RESPOND,
        rc.INTENT_INTEREST: TODO_FOLLOW_UP,
        rc.INTENT_COMPLAINT: TODO_COMPLAINT,
    }.get(intent, TODO_OTHER)
    if intent in (rc.INTENT_RFQ, rc.INTENT_QUESTION, rc.INTENT_INTEREST, rc.INTENT_COMPLAINT):
        priority = 1 if intent in (rc.INTENT_RFQ, rc.INTENT_COMPLAINT) else 2
    db.add(CustomerTodo(
        customer_id=customer_id,
        message_id=message_id,
        todo_type=todo_type,
        summary=summary,
        details_json=json.dumps(details or {}, ensure_ascii=False) if details else None,
        priority=priority,
        status=TODO_OPEN,
        created_at=datetime.datetime.utcnow(),
        updated_at=datetime.datetime.utcnow(),
    ))
    db.flush()


def _handle_status(db: Session, customer_id: int, intent: str, message_id: int,
                   note: Optional[str] = None) -> None:
    """客户状态机联动。

    普通回复 → 已回复（自动不降级）；退信/退订 → 人工置无效线索并留痕
    （无效线索为终态，自动不入 —— 由状态机抛 StatusTransitionError，这里降级为
    仅记录审计 + 备注，等待人工确认）。
    """
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if customer is None:
        return
    trigger = {
        rc.INTENT_BOUNCE: "bounce",
        rc.INTENT_UNSUBSCRIBE: "unsubscribe",
    }.get(intent, "mail_reply")
    if intent in (rc.INTENT_BOUNCE, rc.INTENT_UNSUBSCRIBE):
        try:
            cs.transition_customer_status(
                db, customer, "无效线索", trigger=trigger,
                source_message_id=message_id, note=note or cs.TRIGGER_DEFAULT_NOTE.get(trigger),
                force=False,
            )
        except cs.StatusTransitionError as exc:
            logger.info("退信/退订状态机拒绝自动降级，仅留痕: %s", exc)
            cs.record_history(db, customer_id=customer_id, old_status=customer.status,
                              new_status=customer.status, trigger=trigger,
                              source_message_id=message_id, note="退信/退订（状态未降级，待人工确认）")
    else:
        try:
            cs.transition_customer_status(
                db, customer, "已回复", trigger="mail_reply",
                source_message_id=message_id, force=False,
            )
        except cs.StatusTransitionError as exc:
            logger.warning("回复状态机流转失败: %s", exc)


# ═══════════════════════════════════════════════════════════════════════
# 单封处理
# ═══════════════════════════════════════════════════════════════════════

def _process_raw(db: Session, account: MailSenderAccount, uid: str, raw_bytes: bytes,
                 customer_domains: Dict[int, dict]) -> Optional[Dict[str, int]]:
    msg = email_lib.message_from_bytes(raw_bytes)
    provider_message_id = _decode_header_value(msg.get("Message-ID")) or f"uid-{uid}"
    if len(provider_message_id) > 255:
        provider_message_id = provider_message_id[:255]

    if _existing_message(db, account.id, provider_message_id):
        return None  # 去重

    subject = _decode_header_value(msg.get("Subject"))[:2000]
    from_address = _decode_header_value(msg.get("From"))
    from_emails = _extract_addresses(msg.get("From"))
    from_addr = from_emails[0] if from_emails else ""
    to_raw = msg.get("To")
    to_emails = _extract_addresses(to_raw)
    sent_at = _parse_date(msg.get("Date"))
    received_at = datetime.datetime.utcnow()
    # 线程：References[0] 否则 In-Reply-To（与我们发送时的 Message-ID 对应）
    thread_id = None
    refs = msg.get("References")
    if refs:
        parsed = _extract_addresses(refs)
        if not parsed and refs:
            m = re.search(r"<([^<>]+)>", refs)
            if m:
                thread_id = m.group(1)[:255]
    irt = msg.get("In-Reply-To")
    if not thread_id and irt:
        m = re.search(r"<([^<>]+)>", irt)
        if m:
            thread_id = m.group(1)[:255]

    body_text = _extract_main_text(msg)
    snippet = body_text.replace("\n", " ").strip()[:200] or subject[:200]

    # 外置：正文 / 原始 MIME
    body_key, raw_key = _externalize(
        db,
        body_text.encode("utf-8", errors="replace") if body_text else None,
        raw_bytes,
    )

    headers_for_classify = {k.lower(): v for k, v in msg.items()}
    headers_for_classify.update({h: "1" for h in _BOUNCE_HEADERS_RULE if msg.get(h)})

    message = MailMessage(
        provider="imap",
        provider_message_id=provider_message_id,
        thread_id=thread_id,
        mail_account_id=account.id,
        direction="in",
        from_address=from_addr or None,
        to_addresses_json=json.dumps(to_emails, ensure_ascii=False) if to_emails else None,
        subject=subject or None,
        sent_at=sent_at,
        received_at=received_at,
        snippet=snippet or None,
        body_text_object_id=body_key,
        raw_mime_object_id=raw_key,
        created_at=received_at,
    )
    db.add(message)
    db.flush()

    # 附件外置
    attachments = _parse_attachments(msg)
    for att in attachments:
        a_key = objsv.put_object(db, att["data"], content_type=att["content_type"]).object_key
        db.add(MailAttachment(
            message_id=message.id,
            filename=att["filename"][:255],
            content_type=att["content_type"][:128],
            size_bytes=len(att["data"]),
            object_id=a_key,
            sha256=hashlib.sha256(att["data"]).hexdigest(),
            created_at=received_at,
        ))

    # 客户匹配
    match = _match_customer(db, customer_domains, from_addr)
    if match:
        message.customer_id = match[0]
        message.matched_domain = match[1]

    # 分类（规则预判 → LLM → 规则兜底）。无论是否匹配客户都分类，
    # 便于退信/退订即使未匹配也进黑名单；待办/状态联动仅在匹配客户时执行。
    classifiers = {
        "subject": subject,
        "body": body_text,
        "headers": headers_for_classify,
        "content_type": msg.get_content_type(),
    }
    result = _classify_body(db, account, **classifiers)
    message.classification = result.get("intent")
    message.classification_confidence = result.get("confidence")
    message.classification_json = json.dumps(result, ensure_ascii=False)

    db.flush()
    stats = {"processed": 1, "matched": 1 if match else 0, "new": 1}

    # 联动
    intent = result.get("intent")
    if intent in (rc.INTENT_BOUNCE, rc.INTENT_UNSUBSCRIBE):
        if intent == rc.INTENT_BOUNCE:
            failed_addr = msg.get("x-failed-recipients") or from_addr
            _add_blacklist(db, account, failed_addr, _BLACKLIST_REASON_BOUNCE,
                           provider_message_id, "检测到退信")
            _handle_status(db, message.customer_id, intent, message.id,
                           note=f"检测到退信: {subj_snippet_for_note(subject)}")
        else:
            _add_blacklist(db, account, from_addr, _BLACKLIST_REASON_UNSUBSCRIBE,
                           provider_message_id, "客户主动退订")
            _handle_status(db, message.customer_id, intent, message.id,
                           note=f"客户退订: {subj_snippet_for_note(subject)}")
    elif match and result.get("next_action") == "create_todo":
        summary = result.get("summary") or f"客户回复: {snippet or subject}"
        _create_todo(db, customer_id=message.customer_id, message_id=message.id,
                     intent=intent, summary=summary[:500], details=result)
        _handle_status(db, message.customer_id, intent, message.id)

    return stats


# 规则头（用于分类预判）：x-failed-recipients / x-failure-reason / diagnostic-code
_BOUNCE_HEADERS_RULE = ("x-failed-recipients", "x-failure-reason", "diagnostic-code")


def subj_snippet_for_note(subject: str) -> str:
    return (subject or "").strip()[:80] or "(无主题)"


def _run_async_func(coro_factory):
    """在独立事件循环中执行协程（兼容已在运行 loop 的线程）。

    分类是可选增强：失败不抛，回退规则兜底。
    """
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro_factory())
    except Exception:
        return None
    finally:
        loop.close()


def _classify_body(db: Session, account: MailSenderAccount, subject: str, body: str,
                   headers: Dict[str, str], content_type: str) -> Dict:
    """分类入口：规则预判 → LLM（独立事件循环）→ 规则兜底。"""
    rule = rc.rule_preclassify(subject, body, headers, content_type)
    if rule:
        return rule
    result = _run_async_func(lambda: rc.classify_reply(
        subject, body, headers, content_type,
        user_id=account.user_id, llm_available=True,
    ))
    if result is None:
        result = rc._fallback_classify(subject, body)
    return result


# ═══════════════════════════════════════════════════════════════════════
# 账户级同步入口
# ═══════════════════════════════════════════════════════════════════════

def sync_account(db: Session, account_id: int) -> Dict[str, int]:
    """同步单个发件账户的 INBOX（增量）。返回统计。"""
    stats = {"processed": 0, "matched": 0, "new": 0, "error": 0}
    account = db.query(MailSenderAccount).filter(MailSenderAccount.id == account_id).first()
    if account is None:
        stats["error"] = 1
        return stats
    if not adapter_for_sync(account):
        return stats
    try:
        client = _connect_imap(account)
    except Exception as exc:
        account.last_error = f"IMAP 连接失败: {exc}"[:500]
        db.commit()
        stats["error"] = 1
        return stats
    try:
        uidvalidity, _placeholder = _extract_uidvalidity(client)
        if not uidvalidity:
            client.logout()
            return stats
        known_uv, last_uid = _load_last_cursor(db, account)
        # UIDVALIDITY 变化 → 放弃游标全量重吸
        if known_uv and known_uv != uidvalidity:
            last_uid = None
        uids = _fetch_uids(client, uidvalidity, last_uid, max_results=_MAX_PER_RUN)
        customer_domains = _load_customer_domains(db)

        newest_uid = last_uid
        for uid in uids:
            raw = _fetch_raw(client, uid)
            if not raw:
                continue
            try:
                processed = _process_raw(db, account, uid, raw, customer_domains)
                if processed:
                    stats["processed"] += processed["processed"]
                    stats["matched"] += processed["matched"]
                    stats["new"] += processed["new"]
            except Exception as exc:
                logger.warning("处理 UID %s 失败: %s", uid, exc)
                stats["error"] += 1
            newest_uid = uid
        if newest_uid and newest_uid != last_uid:
            _save_cursor(db, account, uidvalidity, newest_uid)
        account.last_error = None
        db.commit()
    finally:
        try:
            client.logout()
        except Exception:
            pass
    return stats


def adapter_for_sync(account: MailSenderAccount) -> bool:
    """账户是否具备同步前置条件：启用 + IMAP 主机 + 凭据可解。"""
    if not account.enabled:
        return False
    creds = _imap_credentials(account)
    return creds is not None


def sync_all_accounts(db: Session, account_ids: Optional[List[int]] = None) -> Dict[int, Dict[str, int]]:
    """同步全部（或指定）启用账户。单账户失败不回滚。"""
    q = db.query(MailSenderAccount).filter(MailSenderAccount.enabled == 1)
    if account_ids:
        q = q.filter(MailSenderAccount.id.in_(account_ids))
    results = {}
    for acc in q.all():
        results[acc.id] = sync_account(db, acc.id)
    return results
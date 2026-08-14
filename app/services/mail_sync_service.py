"""
Gmail 发信检测同步服务（V5.2 新增）

编排：读取账户 → 获取 token → 增量同步已发送邮件 → 解析元数据 →
域名匹配客户 → 写入 CustomerEmailActivity（幂等去重）。
"""
import datetime
import json
import logging
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.database import (
    Customer, CustomerEmail, CustomerEmailActivity, MailAccount,
)
from app.services import gmail_service as gs
from app.services.email_domain_matcher import (
    extract_email_domain,
    extract_registrable_domain,
    match_domain,
)

logger = logging.getLogger("mail_sync")

# 每页处理的邮件上限（防止单次同步过久）
_PAGE_SIZE = 50


def _load_recipient_domains(db: Session, customer: Customer) -> List[str]:
    """从 customer_emails 表收集该客户已知邮箱域名（用于 manual_email 匹配）"""
    rows = (
        db.query(CustomerEmail)
        .filter(CustomerEmail.customer_id == customer.id)
        .all()
    )
    domains = []
    for r in rows:
        d = extract_email_domain(r.email)
        if d and d not in domains:
            domains.append(d)
    return domains


def _load_customer_domains(db: Session) -> Dict[int, dict]:
    """预加载所有客户：{customer_id: {website_domain, manual_domains}}"""
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
            "manual_domains": _load_recipient_domains(db, c),
        }
    return result


def _match_customer(
    customer_domains: Dict[int, dict],
    recipients: List[str],
) -> Optional[tuple]:
    """在收件人列表中匹配客户，返回 (customer_id, matched_domain, match_type)"""
    for email in recipients:
        recipient_domain = extract_email_domain(email)
        if not recipient_domain:
            continue
        for customer_id, info in customer_domains.items():
            # 严格主域匹配
            if match_domain(info["website_domain"], recipient_domain):
                return customer_id, recipient_domain, "exact_domain"
            # 已知手动邮箱域名
            if recipient_domain in info["manual_domains"]:
                return customer_id, recipient_domain, "manual_email"
    return None


def _save_activity(
    db: Session,
    customer_id: int,
    account_id: int,
    message: dict,
    matched_domain: str,
    match_type: str,
    sent_at: Optional[datetime.datetime],
) -> bool:
    """写入活动记录（幂等：mail_account_id+provider+message_id+matched_domain 唯一）"""
    to_list = gs._extract_addresses(message.get("to_raw", ""))
    cc_list = gs._extract_addresses(message.get("cc_raw", "")) if message.get("cc_raw") else []

    existing = (
        db.query(CustomerEmailActivity)
        .filter(
            CustomerEmailActivity.mail_account_id == account_id,
            CustomerEmailActivity.provider == "gmail",
            CustomerEmailActivity.provider_message_id == message.get("id", ""),
            CustomerEmailActivity.matched_domain == matched_domain,
        )
        .first()
    )
    if existing:
        return False

    activity = CustomerEmailActivity(
        customer_id=customer_id,
        mail_account_id=account_id,
        provider="gmail",
        provider_message_id=message.get("id", "")[:255],
        internet_message_id=message.get("message_id") or None,
        thread_id=message.get("thread_id") or None,
        from_address=(message.get("from_header") or "")[:255],
        to_addresses_json=json.dumps(to_list, ensure_ascii=False),
        cc_addresses_json=json.dumps(cc_list, ensure_ascii=False),
        subject=(message.get("subject") or "")[:2000],
        sent_at=sent_at,
        matched_domain=matched_domain,
        match_type=match_type,
        snippet=message.get("snippet") or None,
        raw_metadata_json=json.dumps({
            "internal_date": message.get("internal_date"),
            "date_header": message.get("date_header"),
        }, ensure_ascii=False),
        created_at=datetime.datetime.utcnow(),
    )
    db.add(activity)

    # V5.2：检测到发信后自动更新客户跟进状态 + 最近发信时间
    _update_customer_on_email_sent(db, customer_id, sent_at)
    return True


def _update_customer_on_email_sent(
    db: Session,
    customer_id: int,
    sent_at: Optional[datetime.datetime],
):
    """检测到发信后更新客户：跟进状态 → 已发邮件，同步发信时间。

    仅当状态为「待联系」或未设置时更新为「已发邮件」（不覆盖已回复/成单等更高级状态）。
    """
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if customer is None:
        return
    if not customer.status or customer.status == "待联系":
        customer.status = "已发邮件"
    if sent_at:
        if not customer.last_email_sent_at or sent_at > customer.last_email_sent_at:
            customer.last_email_sent_at = sent_at


def sync_account(db: Session, account: MailAccount) -> Dict[str, int]:
    """同步单个邮箱账户（增量），返回统计 {processed, matched, new_activities}"""
    stats = {"processed": 0, "matched": 0, "new_activities": 0}
    token = gs.get_account_access_token(db, account)
    if not token:
        return stats

    customer_domains = _load_customer_domains(db)
    if not customer_domains:
        return stats

    start_history_id = account.sync_cursor or None
    page_token = None
    pages = 0

    # 轮询模式增量：无 history 游标时，基于上次同步时间用 after: 查询
    # （messages.list 响应不含 historyId，无法自行建立历史游标）
    after_date = None
    if not start_history_id and account.last_synced_at:
        after_date = account.last_synced_at.date() - datetime.timedelta(days=1)  # 留 1 天余量防时区漏检

    while pages < 10:  # 上限 10 页
        try:
            data = gs.list_sent_messages(
                token, start_history_id=start_history_id, page_token=page_token,
                max_results=_PAGE_SIZE, after_date=after_date,
            )
        except gs.GmailServiceError:
            break

        # 历史模式下提取消息 ID；列表模式下从 messages 提取
        if start_history_id:
            message_ids = gs.extract_history_message_ids(data)
        else:
            message_ids = [m["id"] for m in data.get("messages", []) or [] if m.get("id")]

        for mid in message_ids:
            try:
                raw = gs.get_message_metadata(token, mid)
            except gs.GmailServiceError:
                continue
            message = gs.parse_message_payload(raw)
            stats["processed"] += 1

            recipients = gs._extract_addresses(message.get("to_raw", ""))
            if not recipients:
                continue
            match = _match_customer(customer_domains, recipients)
            if not match:
                continue
            customer_id, matched_domain, match_type = match
            stats["matched"] += 1
            sent_at = gs.parse_sent_datetime(message.get("date_header"), message.get("internal_date"))
            if _save_activity(db, customer_id, account.id, message, matched_domain, match_type, sent_at):
                stats["new_activities"] += 1

        # 更新游标并翻页
        new_cursor = data.get("historyId") or (data.get("nextHistoryId") or "")
        if new_cursor:
            account.sync_cursor = new_cursor
        page_token = data.get("nextPageToken") or ""
        if not page_token:
            break
        pages += 1

    # 首次同步且未建立历史游标时：若配置了 Pub/Sub topic，尝试 watch 建立游标
    # （messages.list 无法拿到 historyId，watch 响应中的 historyId 是后续增量起点）
    if not account.sync_cursor:
        import os
        topic = os.environ.get("GMAIL_PUBSUB_TOPIC", "").strip()
        if topic:
            try:
                history_id, expiration = gs.gmail_watch(token, topic)
                if history_id:
                    account.sync_cursor = history_id
                if expiration:
                    try:
                        account.watch_expiration_at = datetime.datetime.utcfromtimestamp(int(expiration) / 1000)
                    except (ValueError, TypeError):
                        pass
            except gs.GmailServiceError:
                pass

    account.last_synced_at = datetime.datetime.utcnow()
    db.commit()
    logger.info("邮箱同步 %s: %s", account.email_address, stats)
    return stats


def sync_all_accounts(db: Session) -> Dict[str, Dict[str, int]]:
    """同步所有 active 账户"""
    accounts = db.query(MailAccount).filter(MailAccount.status.in_(["active", "reauth_required"])).all()
    results = {}
    for account in accounts:
        results[account.email_address] = sync_account(db, account)
    return results


def renew_watches(db: Session, topic_name: str) -> Dict[str, str]:
    """续期所有账户的 Gmail watch（需要 Pub/Sub topic 配置）"""
    results = {}
    if not topic_name:
        return results
    accounts = db.query(MailAccount).filter(MailAccount.status == "active").all()
    for account in accounts:
        token = gs.get_account_access_token(db, account)
        if not token:
            results[account.email_address] = "reauth_required"
            continue
        try:
            history_id, expiration = gs.gmail_watch(token, topic_name)
            if history_id:
                account.sync_cursor = history_id
            if expiration:
                try:
                    account.watch_expiration_at = datetime.datetime.utcfromtimestamp(int(expiration) / 1000)
                except (ValueError, TypeError):
                    pass
            db.commit()
            results[account.email_address] = "renewed"
        except gs.GmailServiceError:
            results[account.email_address] = "error"
    return results

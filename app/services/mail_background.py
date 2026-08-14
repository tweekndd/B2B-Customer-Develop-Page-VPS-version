"""
Gmail 发信检测后台任务（V5.2 新增）

单体 FastAPI 单进程部署：由 lifespan 启动一个 asyncio 循环任务，每隔
MAIL_MAINTENANCE_INTERVAL（默认 6 小时）执行：
1. 续期所有 active 账户的 Gmail watch（需配置 GMAIL_PUBSUB_TOPIC）
2. 补偿同步：无推送环境下的轮询兜底（history 增量，幂等）

多副本部署时需外部任务锁；本项目 docker-compose 为单副本。
"""
import asyncio
import datetime
import logging
import os

from app.database import SessionLocal, MailAccount
from app.services import mail_sync_service

logger = logging.getLogger("mail_background")

_MAINTENANCE_INTERVAL = int(os.environ.get("MAIL_MAINTENANCE_INTERVAL", "21600"))  # 6h


async def periodic_mail_maintenance():
    """周期执行 watch 续期 + 补偿同步"""
    while True:
        try:
            await asyncio.to_thread(run_maintenance_once)
        except Exception as e:
            logger.warning("邮箱后台维护失败: %s", e)
        await asyncio.sleep(_MAINTENANCE_INTERVAL)


def run_maintenance_once():
    """执行一轮维护（同步线程内，避免阻塞事件循环）"""
    topic = os.environ.get("GMAIL_PUBSUB_TOPIC", "").strip()
    db = SessionLocal()
    try:
        accounts = db.query(MailAccount).filter(MailAccount.status == "active").all()
        if not accounts:
            return

        now = datetime.datetime.utcnow()
        for account in accounts:
            try:
                # 1) watch 续期：到期前 24h 内续期（需 topic）
                needs_renew = (
                    topic
                    and (account.watch_expiration_at is None
                         or account.watch_expiration_at < now + datetime.timedelta(hours=24))
                )
                # 2) 补偿同步：距上次同步超过 12 小时，或从未同步
                needs_sync = (
                    account.last_synced_at is None
                    or account.last_synced_at < now - datetime.timedelta(hours=12)
                )

                if needs_renew:
                    from app.services import gmail_service as gs
                    token = gs.get_account_access_token(db, account)
                    if token:
                        try:
                            history_id, expiration = gs.gmail_watch(token, topic)
                            if history_id:
                                account.sync_cursor = history_id
                            if expiration:
                                try:
                                    account.watch_expiration_at = datetime.datetime.utcfromtimestamp(
                                        int(expiration) / 1000
                                    )
                                except (ValueError, TypeError):
                                    pass
                            db.commit()
                            logger.info("watch 续期成功: %s", account.email_address)
                        except gs.GmailServiceError:
                            pass

                if needs_sync:
                    mail_sync_service.sync_account(db, account)
            except Exception as e:
                logger.warning("账户维护失败 %s: %s", account.email_address, e)
    finally:
        db.close()

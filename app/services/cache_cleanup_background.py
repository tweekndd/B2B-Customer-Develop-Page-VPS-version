"""
缓存周期清理后台任务（V5.3 阶段3）

数据库瘦身：每天自动清理过期缓存（搜索/官网/AI/邮箱/地理编码），
防止缓存表无限增长撑大数据库文件。
由 main.py lifespan 启动（单进程部署，与 mail_background 相同模式）。
"""
import asyncio
import logging
import os

logger = logging.getLogger("cache_cleanup")

_CLEANUP_INTERVAL = int(os.environ.get("CACHE_CLEANUP_INTERVAL", "86400"))  # 默认 24h


async def periodic_cache_cleanup():
    while True:
        try:
            await asyncio.to_thread(run_cleanup_once)
        except Exception as e:
            logger.warning("缓存清理失败: %s", e)
        await asyncio.sleep(_CLEANUP_INTERVAL)


def run_cleanup_once():
    """执行一轮过期缓存清理（线程内，避免阻塞事件循环）"""
    from app.database import SessionLocal
    from app.services.cache_manager import clean_expired_cache

    db = SessionLocal()
    try:
        cleaned = clean_expired_cache(db)
        total = sum(cleaned.values())
        if total > 0:
            logger.info("周期缓存清理: 已删除 %d 条 (%s)", total, cleaned)
    finally:
        db.close()

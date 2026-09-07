"""
数据库基础设施（V5.3 重构：从 database.py 拆出 / Phase0 加固）

只负责 Engine / SessionLocal / Base / get_db / 连接池 / 健康检查 / 慢查询日志，
不放业务模型。业务模型定义在 app/models/，通过 app/database.py 兼容层统一导出。

Phase0 变更：
- PostgreSQL 生产库：连接池（大小/溢出/回收/超时）可配置，pool_pre_ping 保活；
- 健康检查 check_database()：供 /healthz 与容器 healthcheck 使用；
- 慢查询日志：SLOW_QUERY_MS 配置阈值，超过时记录到 logs/slow_query.log。
"""
import os
import time
import logging
from logging.handlers import RotatingFileHandler

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import event

# ── 数据库连接：优先使用环境变量 DATABASE_URL，否则回退到 SQLite ──
_DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
_IS_POSTGRES = _DATABASE_URL.startswith("postgresql")

if _DATABASE_URL:
    # PostgreSQL 或其他外部数据库（由环境变量控制）
    DATABASE_URL = _DATABASE_URL
    _engine_kwargs = {
        "pool_pre_ping": True,
        # 连接池配置（生产库多 Worker/多线程抢占时避免频繁建连）
        "pool_size": int(os.environ.get("DB_POOL_SIZE", "5")),
        "max_overflow": int(os.environ.get("DB_MAX_OVERFLOW", "10")),
        "pool_recycle": int(os.environ.get("DB_POOL_RECYCLE", "1800")),
        "pool_timeout": int(os.environ.get("DB_POOL_TIMEOUT", "30")),
    }
else:
    # SQLite 本地文件（默认，开发/单机测试）
    DATABASE_URL = "sqlite:///./app/customers.db"
    _engine_kwargs = {"connect_args": {"check_same_thread": False}}

engine = create_engine(DATABASE_URL, **_engine_kwargs)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ═══════════════════════════════════════════════════════════════════════
# 健康检查
# ═══════════════════════════════════════════════════════════════════════

def check_database() -> dict:
    """数据库连通性检查：执行 SELECT 1，返回状态与耗时（毫秒）。

    失败时返回 status=error + 错误信息，不抛出异常（供 healthcheck 使用）。
    """
    started = time.monotonic()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {
            "status": "ok",
            "engine": "postgresql" if _IS_POSTGRES else "sqlite",
            "latency_ms": round((time.monotonic() - started) * 1000, 2),
        }
    except Exception as exc:  # noqa: BLE001 - healthcheck 需要吞掉一切连接错误
        return {
            "status": "error",
            "engine": "postgresql" if _IS_POSTGRES else "sqlite",
            "error": str(exc)[:300],
        }


# ═══════════════════════════════════════════════════════════════════════
# 慢查询日志（SLOW_QUERY_MS > 0 时启用）
# ═══════════════════════════════════════════════════════════════════════
_SLOW_QUERY_MS = float(os.environ.get("SLOW_QUERY_MS", "0") or 0)

_slow_logger = None
if _SLOW_QUERY_MS > 0:
    try:
        os.makedirs("logs", exist_ok=True)
        _slow_logger = logging.getLogger("slow_query")
        _slow_logger.setLevel(logging.WARNING)
        if not _slow_logger.handlers:
            _handler = RotatingFileHandler(
                "logs/slow_query.log", maxBytes=5 * 1024 * 1024,
                backupCount=5, encoding="utf-8",
            )
            _handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            _slow_logger.addHandler(_handler)
    except Exception:  # noqa: BLE001 - 日志配置失败不应阻止启动
        _slow_logger = None


def _install_slow_query_listener() -> None:
    """为当前 engine 挂载慢查询监听（记录执行超过 SLOW_QUERY_MS 的语句）。"""
    if not (_slow_logger and _SLOW_QUERY_MS > 0):
        return

    _state = {}

    @event.listens_for(engine, "before_cursor_execute")
    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        _state["start"] = time.monotonic()

    @event.listens_for(engine, "after_cursor_execute")
    def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        start = _state.pop("start", None)
        if start is None:
            return
        elapsed_ms = (time.monotonic() - start) * 1000
        if elapsed_ms > _SLOW_QUERY_MS:
            sql = " ".join((statement or "").split())
            _slow_logger.warning("%.0fms | %s", elapsed_ms, sql[:2000])


_install_slow_query_listener()


def get_db():
    """获取数据库会话的生成器函数"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

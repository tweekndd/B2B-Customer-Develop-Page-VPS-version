"""
AI Trade Customer Analyzer V4.6 - 主程序入口
V4.6: 买家/供应商评分分级 + 多语种 AI 开发信生成
客户发现 + 客户分析 + 客户数据库平台
"""
# Copyright 2026 Alex
#
# Licensed under the Apache License, Version 2.0
# You may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#

import os
import time
import logging
import asyncio
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
import uvicorn
from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from dotenv import load_dotenv

# 加载 .env（含 ADMIN_USERNAME / ADMIN_PASSWORD / SESSION_SECRET / 各 API Key）
load_dotenv()

from app.database import init_db, get_db
from app.services.cache_manager import clean_expired_cache
from app.auth import get_user_from_session, get_current_user, require_admin, ensure_admin_exists
from app.api import router
from app.filemanager import router as filemanager_router
from app.security import limiter, get_rate_limit_group, client_ip

# ── 访问日志（logs/access.log，按 5MB 轮转） ──
os.makedirs("logs", exist_ok=True)
_access_logger = logging.getLogger("access")
_access_logger.setLevel(logging.INFO)
if not _access_logger.handlers:
    _access_handler = RotatingFileHandler(
        "logs/access.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    _access_handler.setFormatter(
        logging.Formatter("%(asctime)s %(message)s")
    )
    _access_logger.addHandler(_access_handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    init_db()

    try:
        db_session = next(get_db())
        cleaned = clean_expired_cache(db_session)
        total = sum(cleaned.values())
        if total > 0:
            print(f"  缓存清理: 已删除 {total} 条过期记录 ({cleaned})")

        # 启动时检查管理员账号
        ensure_admin_exists(db_session)
        db_session.close()
    except Exception as e:
        print(f"  初始化跳过: {e}")

    _PORT = int(os.environ.get("PORT", "8000"))

    print("=" * 50)
    print("  AI Trade Customer Analyzer V4.6")
    print(" 客户发现 + AI分析 + 客户数据库 + Hunter + Prospeo 邮箱 + 地图 + Firecrawl 降级")
    print("=" * 50)
    print(f" 访问地址: http://localhost:{_PORT}")
    print(f" 客户列表: http://localhost:{_PORT}")
    print(f" 客户发现: http://localhost:{_PORT}/discovery")
    print(f" 评分配置: http://localhost:{_PORT}/config")
    print(f" Hunter邮箱: http://localhost:{_PORT}/hunter")
    print(f" 地图页面:  http://localhost:{_PORT}/map")
    print(f" AI 设置:   http://localhost:{_PORT}/settings")
    print("=" * 50)

    # V5.2：Gmail 发信检测后台任务（watch 续期 + 补偿同步）
    _mail_worker_task = None
    try:
        from app.services import mail_background
        _mail_worker_task = asyncio.create_task(mail_background.periodic_mail_maintenance())
    except Exception as e:
        print(f"  [Gmail] 后台任务启动跳过: {e}")

    # V5.3 阶段3：缓存周期清理后台任务（数据库瘦身）
    _cache_cleanup_task = None
    try:
        from app.services import cache_cleanup_background
        _cache_cleanup_task = asyncio.create_task(cache_cleanup_background.periodic_cache_cleanup())
    except Exception as e:
        print(f"  [缓存] 后台任务启动跳过: {e}")

    yield

    for _task in (_mail_worker_task, _cache_cleanup_task):
        if _task is not None:
            _task.cancel()
            try:
                await _task
            except (asyncio.CancelledError, Exception):
                pass


# Python 3.14 兼容：关闭 Jinja2 模板缓存（Python 3.14 的 weakref 变更影响缓存键）
_jinja_env = Environment(
    loader=FileSystemLoader("app/templates"),
    cache_size=0,  # 禁用缓存以兼容 Python 3.14（0=None 无缓存）
)
templates = Jinja2Templates(env=_jinja_env)

app = FastAPI(
    title="AI Trade Customer Analyzer V4.6",
    description="客户发现 + AI分析 + 客户数据库 + Hunter 邮箱查找 + Prospeo 邮箱发现 + 城市级地图 + Firecrawl 降级 — V4.6 买家/供应商评分分级 + 多语种 AI 开发信生成",
    version="4.6",
    lifespan=lifespan,
    # 生产安全：关闭自动文档，避免暴露接口结构
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(router)
app.include_router(filemanager_router)


# ── 安全中间件：IP 限流 + 安全响应头 + 访问日志 ──
# 定义在 SessionMiddleware 之前（其外层），故可读取 session 记录 user_id
@app.middleware("http")
async def security_middleware(request: Request, call_next):
    start = time.monotonic()
    path = request.url.path

    # 1) IP 限流（按 IP + 路由组，防刷 AI/搜索/邮箱额度与暴力破解）
    # 测试环境可设 DISABLE_RATE_LIMIT=1 关闭
    ip = client_ip(request)
    group, limit, window = get_rate_limit_group(path)
    if not os.environ.get("DISABLE_RATE_LIMIT") and not limiter.allow(
        f"{ip}:{group}", limit, window
    ):
        return JSONResponse(
            status_code=429,
            content={"detail": "请求过于频繁，请稍后再试"},
        )

    # 2) 执行请求
    response = await call_next(request)

    # 3) 安全响应头
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

    # 4) 访问日志（IP/方法/路径/用户/状态/耗时）
    duration_ms = int((time.monotonic() - start) * 1000)
    user_id = request.session.get("user_id") if request.session else None
    _access_logger.info(
        "%s | %s | %s%s | uid=%s | %d | %dms",
        ip, request.method, path, request.url.query, user_id or "-", response.status_code, duration_ms,
    )

    # 5) 定期清理限流窗口，防内存膨胀
    if group == 0 and int(start * 1000) % 500 < 1:
        limiter.cleanup()

    return response


# ── API 认证中间件 ──
# 注意：必须定义在 add_middleware(SessionMiddleware) 之前，
# 这样 SessionMiddleware 会成为外层，先处理 session 再进入此中间件
_PUBLIC_API_PATHS = (
    "/api/auth/",
    "/api/webhooks/",   # V5.2：Gmail Pub/Sub 推送（第三方服务，无浏览器 Session）
)

@app.middleware("http")
async def api_auth_middleware(request: Request, call_next):
    """API 请求必须登录（/api/auth/* 与 /api/webhooks/* 除外），未登录返回 401"""
    path = request.url.path
    if path.startswith("/api/") and not path.startswith(_PUBLIC_API_PATHS):
        user_id = request.session.get("user_id")
        if user_id is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "请先登录"},
            )
    return await call_next(request)


# ── Session 会话中间件（最后添加，成为最外层，优先处理 session）──
# 未配置 SESSION_SECRET 时使用随机密钥（每次启动更换，防已知密钥伪造），生产环境务必在 .env 设置固定值
_SESSION_SECRET = os.environ.get("SESSION_SECRET", "").strip()
if not _SESSION_SECRET:
    _SESSION_SECRET = __import__("secrets").token_urlsafe(32)
    print("  [警告] 未设置 SESSION_SECRET 环境变量，已生成随机会话密钥（重启后所有用户需重新登录）")
    print("  [建议] 请在 .env 中设置固定 SESSION_SECRET 以保证重启后会话不失效")
app.add_middleware(
    SessionMiddleware,
    secret_key=_SESSION_SECRET,
    max_age=86400 * 7,  # 7 天过期
)


# ── 辅助函数 ──

def _get_db_session():
    """获取数据库会话。测试环境通过 dependency_overrides 注入测试库，生产环境返回默认 get_db。"""
    db_factory = app.dependency_overrides.get(get_db, get_db)
    return next(db_factory())


def _render(request: Request, template: str, **kwargs):
    """渲染模板，自动注入 current_user"""
    db_session = _get_db_session()
    try:
        current_user = get_user_from_session(request, db_session)
        return templates.TemplateResponse(
            request,
            template,
            {"request": request, "current_user": current_user, **kwargs},
        )
    finally:
        db_session.close()


def _login_required_page(request: Request, template: str, **kwargs):
    """需登录的页面，未登录跳转到登录页"""
    db_session = _get_db_session()
    try:
        user = get_user_from_session(request, db_session)
        if user is None:
            next_path = request.url.path
            # 安全校验：只允许相对路径，防止开放重定向
            if not next_path or not next_path.startswith("/") or next_path.startswith("//"):
                next_path = "/"
            return RedirectResponse(url="/login?next=" + next_path, status_code=302)
        return templates.TemplateResponse(
            request,
            template,
            {"request": request, "current_user": user, **kwargs},
        )
    finally:
        db_session.close()


# ── 公开页面（无需登录）──

@app.get("/login")
async def login_page(request: Request):
    """登录页面（已登录则跳转首页）"""
    if request.session.get("user_id"):
        return RedirectResponse(url="/", status_code=302)
    return _render(request, "login.html")


@app.get("/")
async def index_page(request: Request):
    return _render(request, "index.html", active_nav="index")


@app.get("/customer/{customer_id}")
async def detail_page(request: Request, customer_id: int):
    return _render(request, "detail.html", active_nav="detail")


@app.get("/map")
async def map_page(request: Request):
    return _render(request, "map.html", active_nav="map")


# ── 需要登录的页面 ──

@app.get("/discovery")
async def discovery_page(request: Request):
    return _login_required_page(request, "discovery.html", active_nav="discovery")


@app.get("/config")
async def config_page(request: Request):
    return _login_required_page(request, "config.html", active_nav="config")


@app.get("/settings")
async def settings_page(request: Request):
    """AI 与 API 设置页（Round 3/4：用户级 API Key 管理）"""
    return _login_required_page(request, "settings.html", active_nav="settings")


@app.get("/hunter")
async def hunter_page(request: Request):
    return _login_required_page(request, "hunter.html", active_nav="hunter")


@app.get("/sync")
async def sync_page(request: Request):
    return _login_required_page(request, "sync.html", active_nav="sync")


# ── 管理员页面 ──

@app.get("/users")
async def users_page(request: Request):
    """用户管理页面（仅管理员）"""
    db_session = next(get_db())
    try:
        user = get_user_from_session(request, db_session)
        if user is None:
            return RedirectResponse(url="/login?next=/users", status_code=302)
        if user.role != "admin":
            return RedirectResponse(url="/", status_code=302)
        return _render(request, "users.html", active_nav="users")
    finally:
        db_session.close()


# ── VPS 文件管理器（仅管理员）──

@app.get("/file")
@app.get("/file/")
async def filemanager_page(request: Request):
    """VPS 全盘文件管理页面：需登录且仅限 admin"""
    db_session = next(get_db())
    try:
        user = get_user_from_session(request, db_session)
        if user is None:
            return RedirectResponse(url="/login?next=/file/", status_code=302)
        if user.role != "admin":
            return RedirectResponse(url="/", status_code=302)
        return _render(request, "filemanager.html", active_nav="file")
    finally:
        db_session.close()


# ── 管理接口 ──

@app.post("/admin/cleanup-cache")
def cleanup_cache(db: Session = Depends(get_db), admin=Depends(require_admin)):
    """手动触发所有过期缓存清理（仅管理员）"""
    cleaned = clean_expired_cache(db)
    total = sum(cleaned.values())
    return {"message": f"已清理 {total} 条过期记录", "details": cleaned}


if __name__ == "__main__":
    os.makedirs("app/uploads", exist_ok=True)
    os.makedirs("app/static/css", exist_ok=True)
    os.makedirs("app/templates", exist_ok=True)

    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8000")),
        reload=False,
        log_level="info",
    )

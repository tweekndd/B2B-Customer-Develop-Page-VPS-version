"""
app/security.py — 生产安全模块 V1（2026-08 加固）

1. SSRF 防护：is_safe_url() — 仅允许公网 http/https 地址，
   阻止访问回环/内网/链路本地/保留地址（防 127.0.0.1、169.254.169.254 等）
2. IP 限流：RateLimiter + get_rate_limit() — 滑动窗口内存实现，
   防止 AI/搜索/邮箱 API 被刷导致额度耗尽

说明：限流为进程内存实现，单进程部署足够；多进程部署需换 Redis。
"""
import ipaddress
import re
import socket
import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

# ── SSRF 防护 ──────────────────────────────────────────────────────────

_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata",
    "metadata.google.internal",
    "metadata.google.internal.",
}

_PRIVATE_NETS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("192.0.0.0/24"),      # IETF 协议保留
    ipaddress.ip_network("192.0.2.0/24"),      # TEST-NET
    ipaddress.ip_network("198.18.0.0/15"),     # 基准测试
    ipaddress.ip_network("198.51.100.0/24"),   # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),    # TEST-NET-3
    ipaddress.ip_network("224.0.0.0/4"),       # 组播
    ipaddress.ip_network("240.0.0.0/4"),       # 保留
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("fc00::/7"),          # ULA
    ipaddress.ip_network("fe80::/10"),         # 链路本地
    ipaddress.ip_network("ff00::/8"),          # IPv6 组播
    ipaddress.ip_network("::ffff:0:0/96"),     # IPv4-mapped
]


def _is_private_ip(ip) -> bool:
    if ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_multicast or ip.is_reserved:
        return True
    return any(ip in net for net in _PRIVATE_NETS)


def is_safe_url(url: str) -> bool:
    """SSRF 防护：仅允许指向公网的 http/https URL。

    - 拒绝非 http/https scheme（file://、javascript:// 等）
    - 拒绝 localhost / 内网 / 链路本地 / 保留地址（字面 IP 与域名解析结果均检查）
    """
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        return False
    if hostname in _BLOCKED_HOSTS:
        return False

    # 字面 IP 直接判断
    try:
        ip = ipaddress.ip_address(hostname)
        return not _is_private_ip(ip)
    except ValueError:
        pass

    # 域名：解析全部地址，任一命中内网即拒绝（防 DNS rebinding 的基础防线）
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if _is_private_ip(ip):
            return False
    return True


# ── IP 限流 ────────────────────────────────────────────────────────────


class RateLimiter:
    """滑动窗口限流器（线程安全，进程内存）"""

    def __init__(self):
        self._hits: dict[tuple, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window: float) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def cleanup(self, max_age: float = 3600.0) -> None:
        """清理超过 max_age 无活动的窗口，防内存膨胀"""
        now = time.monotonic()
        with self._lock:
            stale = [k for k, q in self._hits.items() if not q or now - q[-1] > max_age]
            for k in stale:
                del self._hits[k]


limiter = RateLimiter()

# (正则路径, 窗口内限制次数, 窗口秒) — 按顺序匹配，先命中先应用
RATE_LIMITS = [
    (r"^/api/auth/", 10, 60),                # 登录/认证 — 防暴力破解
    (r"^/api/user-config/.*/test", 5, 60),   # LLM 测试 — 消耗 AI 额度
    (r"^/api/discovery/", 15, 60),           # 客户发现/搜索 — 消耗搜索+AI 额度
    (r"^/api/customers/.*/(analyze|re-analyze|re-scrape|follow-up|geocode)", 15, 60),
    (r"^/api/customers/analyze-all", 5, 60), # 批量分析 — 最耗额度
    (r"^/api/hunter/", 15, 60),              # Hunter 邮箱查找 — 消耗付费额度
    (r"^/api/tomba/", 15, 60),               # Tomba 邮箱查找 — 消耗付费额度
    (r"^/api/waterfall/", 15, 60),           # 级联邮箱发现
    (r"^/api/sync/", 30, 60),                # 数据同步/导入导出
    (r"^/api/", 120, 60),                    # 其余 API
    (r"^/static/", 300, 60),
    (r"^/.*", 300, 60),                      # 普通页面
]


def get_rate_limit_group(path: str) -> tuple[int, int, int]:
    """返回 (组序号, 限制次数, 窗口秒)。按组限流，防止同组换路径绕过。"""
    for idx, (pattern, limit, window) in enumerate(RATE_LIMITS):
        if re.match(pattern, path):
            return idx, limit, window
    return len(RATE_LIMITS) - 1, 300, 60


def client_ip(request) -> str:
    """获取真实客户端 IP（Nginx 反代后取 X-Forwarded-For 首段）

    安全说明：X-Forwarded-For 可被客户端伪造。仅当请求来自受信任的反向代理时使用。
    如果未配置受信任代理列表，回退到 request.client.host（不可伪造）。
    """
    # 受信任的反向代理 IP 列表（可通过环境变量 TRUSTED_PROXIES 配置，逗号分隔）
    import os
    trusted_proxies = os.environ.get("TRUSTED_PROXIES", "").split(",")
    trusted_proxies = [p.strip() for p in trusted_proxies if p.strip()]

    client_host = request.client.host if request.client else "unknown"

    # 如果未配置受信任代理，直接使用客户端 IP（安全）
    if not trusted_proxies:
        return client_host

    # 如果客户端 IP 是受信任代理，才使用 XFF
    if client_host in trusted_proxies:
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",")[0].strip()

    return client_host

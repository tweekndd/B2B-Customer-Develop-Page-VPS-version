"""
Himalaya 邮件 Adapter（Phase1 新增）

按总体开发方案：Himalaya 作为唯一邮件收发通道被 Adapter 统一封装，
业务代码不直接拼接 himalaya 命令，全部经本模块。

设计（对齐 himalaya v2.x CLI/config）：
- 每个 mail_sender_accounts 行生成一份独立 config.toml（DATA_DIR/himalaya/
  config_<account_id>.toml），账户名固定为 `main`，权限 0600；
- 发送：由本系统用 Python email 标准库构造完整 RFC5322 MIME（含我方生成的
  RFC Message-ID / 可选 In-Reply-To / References），通过子进程
  `himalaya -c <cfg> message send`（stdin=MIME）交给 himalaya 走 SMTP；
- 连接测试：用 Python stdlib smtplib/imaplib 直接验证 SMTP/IMAP 登录
  （不依赖 himalaya 二进制是否安装，设置页可即时反馈凭据对错）；
- himalaya 未安装时 send 抛 HimalayaUnavailableError（明确降级提示），
  不影响草稿/审批/任务等其余 Phase1 链路。
"""
import datetime
import email.utils
import json
import os
import shutil
import subprocess
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path
from typing import Dict, Optional

# DATA_DIR（与对象存储共用，docker 卷持久化；本地默认项目 data/）
HIMALAYA_ROOT = os.path.join(
    os.environ.get("DATA_DIR", "").strip()
    or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data"),
    "himalaya",
)


class HimalayaError(Exception):
    """himalaya 相关统一异常"""

    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code


class HimalayaUnavailableError(HimalayaError):
    """himalaya 二进制不可用（未安装/不在 PATH）"""

    def __init__(self):
        super().__init__(
            "Himalaya CLI 不可用：请先安装 himalaya（Docker 部署已内置；本机开发可 "
            "`brew install himalaya` 或下载 https://github.com/pimalaya/himalaya/releases），"
            "或确认 HIMALAYA_BIN 环境变量指向可执行文件",
            status_code=503,
        )


def himalaya_binary() -> Optional[str]:
    """返回 himalaya 可执行文件路径；不可用时返回 None"""
    override = os.environ.get("HIMALAYA_BIN", "").strip()
    if override:
        return override if os.path.exists(override) else None
    return shutil.which("himalaya")


def is_available() -> bool:
    return himalaya_binary() is not None


def _toml_str(value: str) -> str:
    """将字符串转成合法 TOML basic string（用 JSON 转义足够覆盖常见控制字符）"""
    if value is None:
        return '""'
    return json.dumps(str(value), ensure_ascii=False)


def _server_url(kind: str, host: str, port: int, encryption: str) -> str:
    """构造 himalaya server URL。

    kind: imap/smtp；encryption: tls(隐式)/starttls/none。
    - tls      → imaps://host:993 / smtps://host:465
    - starttls → imap://host:143 / smtp://host:587（另开 starttls=true）
    - none     → imap://host:143 / smtp://host:25（starttls=false）
    """
    if encryption == "tls":
        return f"{kind}s://{host}:{port}"
    scheme = kind  # 明文 + 可选 STARTTLS
    return f"{scheme}://{host}:{port}"


def build_account_toml(
    email_address: str,
    display_name: str,
    smtp_host: str,
    smtp_port: int,
    smtp_encryption: str,
    smtp_login: str,
    smtp_password: str,
    imap_host: Optional[str] = None,
    imap_port: Optional[int] = None,
    imap_encryption: Optional[str] = None,
    imap_login: Optional[str] = None,
    imap_password: Optional[str] = None,
) -> str:
    """根据账户字段生成 himalaya v2 config.toml 文本。

    SMTP 必需；IMAP 可选（Phase2 回复同步预留）。密码以 password.raw 写入，
    配置文件置于 DATA_DIR（gitignore + docker 卷），权限 0600。
    """
    lines = []
    lines.append("[accounts.main]")
    lines.append("default = true")
    lines.append(f"email = {_toml_str(email_address)}")
    if display_name:
        lines.append(f"display-name = {_toml_str(display_name)}")

    # ── SMTP ──
    smtp_scheme_tls = (smtp_encryption or "starttls").lower()
    lines.append(f"smtp.server = {_toml_str(_server_url('smtp', smtp_host, smtp_port, smtp_scheme_tls))}")
    if smtp_scheme_tls == "starttls":
        lines.append("smtp.starttls = true")
    elif smtp_scheme_tls == "none":
        lines.append("smtp.starttls = false")
    lines.append(f"smtp.sasl.plain.username = {_toml_str(smtp_login or email_address)}")
    lines.append(f"smtp.sasl.plain.password.raw = {_toml_str(smtp_password)}")

    # ── IMAP（可选） ──
    if imap_host:
        imap_scheme_tls = (imap_encryption or "tls").lower()
        _imap_port = imap_port or (993 if imap_scheme_tls == "tls" else 143)
        lines.append(f"imap.server = {_toml_str(_server_url('imap', imap_host, _imap_port, imap_scheme_tls))}")
        if imap_scheme_tls == "starttls":
            lines.append("imap.starttls = true")
        elif imap_scheme_tls == "none":
            lines.append("imap.starttls = false")
        lines.append(f"imap.sasl.plain.username = {_toml_str(imap_login or email_address)}")
        lines.append(f"imap.sasl.plain.password.raw = {_toml_str(imap_password or smtp_password)}")

    return "\n".join(lines) + "\n"


def _config_dir() -> str:
    os.makedirs(HIMALAYA_ROOT, exist_ok=True)
    return HIMALAYA_ROOT


def account_config_path(account_id: int) -> str:
    return os.path.join(_config_dir(), f"config_{account_id}.toml")


def write_config_file(cfg_text: str, config_path: str) -> str:
    """将 himalaya 配置文本原子写入指定路径（0600），返回路径"""
    cfg_dir = os.path.dirname(config_path) or _config_dir()
    os.makedirs(cfg_dir, exist_ok=True)
    import tempfile
    fd, tmp_path = tempfile.mkstemp(dir=cfg_dir, suffix=".toml.tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(cfg_text)
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, config_path)
    return config_path


# ═══════════════════════════════════════════════════════════════════
# 发送
# ═══════════════════════════════════════════════════════════════════

def build_mime_message(
    *,
    from_address: str,
    from_name: str,
    to_addresses,
    subject: str,
    body_text: str,
    message_id: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
) -> bytes:
    """构造 RFC5322 MIME 消息（纯文本 UTF-8 邮件）。

    - message_id: 我方生成的幂等可溯源的 RFC Message-ID（默认自动生成）
    - in_reply_to/references: 回复/线程追踪（Phase2 回复同步前已预留）
    """
    msg = EmailMessage()
    msg["From"] = email.utils.formataddr((from_name, from_address))
    msg["To"] = ", ".join(to_addresses) if isinstance(to_addresses, (list, tuple)) else to_addresses
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=False, usegmt=True)
    msg["Message-ID"] = message_id or make_msgid()
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.set_content(body_text, subtype="plain", charset="utf-8")
    return msg.as_bytes()


def _run_himalaya(args, stdin_bytes: Optional[bytes] = None, timeout: int = 120) -> Dict:
    """执行 himalaya 子进程（可被测试 mock subprocess.run）。

    Returns: {"exit_code", "stdout", "stderr", "binary"}
    """
    binary = himalaya_binary()
    if binary is None:
        raise HimalayaUnavailableError()
    cmd = [binary] + args
    try:
        proc = subprocess.run(
            cmd,
            input=stdin_bytes,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise HimalayaError(f"himalaya 执行超时（>{timeout}s）: {' '.join(args[:4])}…", status_code=504)
    except OSError as e:
        raise HimalayaError(f"himalaya 执行失败: {e}", status_code=503)
    return {
        "exit_code": proc.returncode,
        "stdout": (proc.stdout or b"").decode("utf-8", errors="replace"),
        "stderr": (proc.stderr or b"").decode("utf-8", errors="replace"),
        "binary": binary,
    }


def parse_provider_message_id(stdout: str, stderr: str = "") -> Optional[str]:
    """尽力从 himalaya 输出解析 SMTP 服务器返回的消息 ID（形如 id=xxxx）。

    解析不到返回 None —— 不影响发送成功判定，internet_message_id 始终由我方
    生成并保存（幂等溯源以我方 Message-ID 为准）。
    """
    text = (stdout or "") + "\n" + (stderr or "")
    for line in text.splitlines():
        s = line.strip()
        m = None
        import re
        m = re.search(r"\b(id|message[-_ ]?id)\s*=\s*([A-Za-z0-9._%+-]{3,})", s, re.IGNORECASE)
        if m:
            return m.group(2)
        # SMTP 250 OK 行常见格式
        m = re.search(r"\b250\b.*[Oo][Kk].*?(?:id=)?([A-Za-z0-9._-]{4,})", s)
        if m and "himalaya" not in m.group(1).lower():
            return m.group(1)
    return None


def send_message(
    *,
    config_path: str,
    from_address: str,
    from_name: str,
    to_addresses,
    subject: str,
    body_text: str,
    message_id: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
    timeout: int = 120,
) -> Dict:
    """通过 himalaya message send 发送一封邮件（stdin = 完整 MIME）。

    Args:
        config_path: write_config_file 返回的账户配置文件
    Returns:
        {"sent": bool, "exit_code", "stdout", "stderr",
         "provider_message_id": str|None, "internet_message_id": str}
    Raises:
        HimalayaUnavailableError / HimalayaError
    """
    mime_bytes = build_mime_message(
        from_address=from_address,
        from_name=from_name,
        to_addresses=to_addresses,
        subject=subject,
        body_text=body_text,
        message_id=message_id,
        in_reply_to=in_reply_to,
        references=references,
    )
    # 提取我们注入的 Message-ID（无则生成）
    import email
    parsed = email.message_from_bytes(mime_bytes)
    actual_message_id = parsed.get("Message-ID", "")

    args = ["-c", config_path, "message", "send"]
    result = _run_himalaya(args, stdin_bytes=mime_bytes, timeout=timeout)
    exit_code = result.get("exit_code", -1)
    stdout = result.get("stdout", "")
    stderr = result.get("stderr", "")

    if exit_code != 0:
        # 认证失败等账号级错误归类
        err_text = stderr or stdout
        raise HimalayaError(f"Himalaya 发送失败（exit={exit_code}）: {err_text[:500]}", status_code=502)

    return {
        "sent": True,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "provider_message_id": parse_provider_message_id(stdout, stderr),
        "internet_message_id": actual_message_id,
    }


# ═══════════════════════════════════════════════════════════════════
# 连接测试（stdlib，不依赖 himalaya 二进制）
# ═══════════════════════════════════════════════════════════════════

def test_smtp_connection(
    host: str,
    port: int,
    encryption: str,
    login: str,
    password: str,
    timeout: int = 20,
) -> Dict:
    """用 Python smtplib 验证 SMTP 服务器连通 + 登录（不发信）。

    Returns: {"ok": bool, "message": str, "details": {...}}
    """
    import smtplib
    try:
        if (encryption or "").lower() == "tls":
            server = smtplib.SMTP_SSL(host, port, timeout=timeout)
        else:
            server = smtplib.SMTP(host, port, timeout=timeout)
            server.ehlo()
            if (encryption or "").lower() == "starttls":
                server.starttls()
                server.ehlo()
        try:
            server.login(login, password)
        except smtplib.SMTPAuthenticationError as e:
            return {"ok": False, "message": f"认证失败（用户名或密码错误）: {e.smtp_code}", "details": {"stage": "auth"}}
        finally:
            try:
                server.quit()
            except Exception:
                pass
        return {"ok": True, "message": f"SMTP 连接与认证成功（{host}:{port}）", "details": {"stage": "ok"}}
    except (smtplib.SMTPConnectError, ConnectionRefusedError, OSError, TimeoutError) as e:
        return {"ok": False, "message": f"无法连接 SMTP 服务器 {host}:{port}: {e}", "details": {"stage": "connect"}}


def test_imap_connection(
    host: str,
    port: int,
    encryption: str,
    login: str,
    password: str,
    timeout: int = 20,
) -> Dict:
    """用 Python imaplib 验证 IMAP 服务器连通 + 登录。"""
    import imaplib
    try:
        if (encryption or "").lower() == "tls":
            client = imaplib.IMAP4_SSL(host, port, timeout=timeout)
        else:
            client = imaplib.IMAP4(host, port, timeout=timeout)
            if (encryption or "").lower() == "starttls":
                client.starttls()
        try:
            client.login(login, password)
        except imaplib.IMAP4.error as e:
            return {"ok": False, "message": f"IMAP 认证失败: {e}", "details": {"stage": "auth"}}
        finally:
            try:
                client.logout()
            except Exception:
                pass
        return {"ok": True, "message": f"IMAP 连接与认证成功（{host}:{port}）", "details": {"stage": "ok"}}
    except (OSError, TimeoutError, imaplib.IMAP4.error) as e:
        return {"ok": False, "message": f"无法连接 IMAP 服务器 {host}:{port}: {e}", "details": {"stage": "connect"}}


def smtp_login_name(email_address: str, smtp_login: Optional[str]) -> str:
    """SMTP 登录名默认等于邮箱地址"""
    return (smtp_login or "").strip() or (email_address or "").strip()


def current_timestamp() -> datetime.datetime:
    return datetime.datetime.utcnow()

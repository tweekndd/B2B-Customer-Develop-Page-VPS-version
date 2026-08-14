"""
邮件域名匹配器（V5.2 新增）

把客户 website 与邮件收件人域名做标准化匹配：
1. 提取客户 website 的 registrable domain（含公共后缀表）
2. 提取收件人邮箱域名并转小写
3. 严格主域匹配（默认）；子域匹配可配置开启
4. 收件人在 customer_emails 表中 → match_type=manual_email

禁止反向包含（evilabc.com 不应匹配 abc.com）。
"""
import re
from typing import List, Optional, Tuple

# 常见二级公共后缀（含此类后缀时主域 = 三级，如 co.uk / com.cn）
_PUBLIC_SUFFIXES = {
    "co.uk", "org.uk", "gov.uk", "ac.uk", "me.uk", "net.uk",
    "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "com.hk",
    "com.sg", "com.my", "com.au", "net.au", "org.au", "com.tw",
    "co.jp", "ne.jp", "or.jp", "co.kr", "or.kr", "co.in",
    "com.br", "com.mx", "com.ar", "com.co", "co.nz", "net.nz",
    "com.sa", "co.za", "com.eg", "com.tr", "com.ae", "co.il",
    "com.pl", "co.at", "com.ru", "com.de", "com.fr", "com.es",
    "com.it", "com.pt", "com.nl", "com.be", "com.se", "com.no",
    "com.fi", "com.gr", "com.ro", "com.ua", "com.vn", "com.ph",
}

_COMMON_TLDS = {
    "com", "net", "org", "io", "co", "info", "biz", "tv", "me",
    "xyz", "online", "site", "tech", "store", "cloud", "app", "dev",
    "ai", "us", "uk", "de", "fr", "es", "it", "nl", "ru", "cn",
    "jp", "kr", "br", "mx", "au", "ca", "in", "ae", "sa", "sg",
    "hk", "tw", "tr", "eg", "za", "pl", "il", "vn", "th", "id",
}

_SUFFIX = "|".join(sorted(_PUBLIC_SUFFIXES, key=len, reverse=True))


def extract_registrable_domain(url_or_domain: str) -> Optional[str]:
    """从网站 URL 或域名中提取 registrable domain（如 www.aquatech.co.uk → aquatech.co.uk）"""
    if not url_or_domain:
        return None
    domain = url_or_domain.strip().lower()
    domain = re.sub(r"^https?://", "", domain)
    domain = domain.split("/")[0].split("?")[0].split("#")[0]
    domain = domain.split(":")[0]
    if domain.startswith("www."):
        domain = domain[4:]

    parts = domain.split(".")
    if len(parts) < 2:
        return None

    # 公共后缀表：末两位组成二级后缀时，主域为三级
    if len(parts) >= 3:
        two = ".".join(parts[-2:])
        if two in _PUBLIC_SUFFIXES:
            return ".".join(parts[-3:])

    # 常见 TLD：主域 = 最后两位
    if parts[-1] in _COMMON_TLDS or len(parts[-1]) <= 3:
        return ".".join(parts[-2:])

    return ".".join(parts[-2:])


def extract_email_domain(email: str) -> Optional[str]:
    """提取邮箱域名（转小写），非法返回 None"""
    if not email or "@" not in email:
        return None
    domain = email.strip().lower().rsplit("@", 1)[1]
    if "." not in domain:
        return None
    return domain


def match_domain(customer_domain: str, recipient_domain: str, allow_subdomain: bool = False) -> bool:
    """判断收件人域名是否命中客户主域（严格主域匹配，默认不开启子域）"""
    if not customer_domain or not recipient_domain:
        return False
    customer_domain = customer_domain.strip().lower().lstrip(".")
    recipient_domain = recipient_domain.strip().lower()
    if recipient_domain == customer_domain:
        return True
    # 子域匹配：recipient 以 .customer_domain 结尾（可配置）
    if allow_subdomain and recipient_domain.endswith("." + customer_domain):
        return True
    return False


def match_recipients(
    to_emails: List[str],
    customer_registrable_domain: Optional[str],
    allow_subdomain: bool = False,
) -> Optional[str]:
    """在收件人列表中查找第一个命中客户主域的邮箱，返回其域名。

    命中：严格主域匹配。未命中返回 None。
    """
    if not to_emails or not customer_registrable_domain:
        return None
    for email in to_emails:
        recipient_domain = extract_email_domain(email)
        if recipient_domain and match_domain(
            customer_registrable_domain, recipient_domain, allow_subdomain
        ):
            return recipient_domain
    return None


def extract_recipients(emails: list) -> Tuple[List[str], List[str]]:
    """拆分 (To 列表, CC 列表)，过滤无效地址并小写"""
    to_list = []
    cc_list = []
    for e in emails or []:
        if not e or not isinstance(e, str):
            continue
        addr = e.strip().lower()
        if addr and "@" in addr:
            to_list.append(addr)
    return to_list, cc_list

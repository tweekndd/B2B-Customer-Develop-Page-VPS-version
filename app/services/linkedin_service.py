"""
LinkedIn 公司主页发现服务（V5.1 新增）

发现链路：已保存候选 → 搜索引擎查询（site:linkedin.com/company ...）→
URL 标准化与过滤 → 候选评分 → 保存候选（用户确认后才置 is_verified=1）。

搜索引擎复用 google_discovery.search_google()（运行时切换 SearXNG/Tavily/SerpAPI），
不重复实现搜索引擎请求。
"""
import datetime
import re
from typing import List, Optional

from sqlalchemy.orm import Session

from app.database import Customer, CustomerSocialProfile

LINKEDIN_DOMAIN = "linkedin.com"
_COMPANY_PATH_RE = re.compile(r"^/company/([^/?#]+)")
_PERSONAL_PATHS = ("/in/", "/jobs/", "/learning/", "/posts/", "/people/", "/feed/", "/school/")

# 搜索引擎查询模板（候选发现）
_DISCOVERY_QUERIES = [
    'site:linkedin.com/company "{company_name}" "{domain}"',
    'site:linkedin.com/company "{company_name}" "{country}"',
    "site:linkedin.com/company {domain}",
]


def _extract_domain_root(website: Optional[str]) -> str:
    """从官网提取主域名根（如 https://www.abc-co.com → abc-co）"""
    if not website:
        return ""
    domain = website.strip().lower()
    domain = re.sub(r"^https?://", "", domain)
    domain = domain.split("/")[0].split("?")[0]
    domain = domain.split(":")[0]
    if domain.startswith("www."):
        domain = domain[4:]
    # 去掉 TLD，取域名主体（abc-co.com → abc-co）
    parts = domain.split(".")
    if len(parts) > 1:
        return parts[0]
    return domain


def normalize_company_url(url: str) -> Optional[str]:
    """标准化 LinkedIn 公司页 URL：
    仅保留 https://www.linkedin.com/company/{vanity}，去掉追踪参数。
    非公司页 / 非法地址返回 None。
    """
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url:
        return None

    # 兼容缺协议写法
    if url.startswith("www.linkedin.com") or url.startswith("linkedin.com"):
        url = "https://" + url
    if "linkedin.com" not in url:
        return None

    match = re.search(r"linkedin\.com(/company/[^/?#]+|/companies/[^/?#]+)", url)
    if not match:
        return None
    path = match.group(1)
    if any(path.startswith(p) for p in _PERSONAL_PATHS):
        return None
    if "/company/" not in path and "/companies/" not in path:
        return None

    vanity = re.sub(r"^/(company|companies)/", "", path).strip("/")
    if not vanity:
        return None
    return f"https://www.linkedin.com/company/{vanity}"


def extract_vanity_name(url: str) -> Optional[str]:
    """从 LinkedIn 公司页 URL 提取 vanity name"""
    normalized = normalize_company_url(url)
    if not normalized:
        return None
    match = re.search(r"/company/([^/?#]+)", normalized)
    return match.group(1) if match else None


def score_company_page_candidate(candidate: dict, customer: Customer) -> float:
    """候选置信度评分（0-100，仅用于排序，不证明事实）

    - 域名根命中 +50（candidate URL/vanity 与客户官网域名主体匹配）
    - 公司名命中 +30（标题/URL 中出现公司名中的显著词）
    - 国家/城市命中 +10（标题/摘要中出现）
    - 发现关键词命中 +10（摘要中出现行业关键词）
    """
    score = 0.0
    profile_url = candidate.get("profile_url") or candidate.get("url") or ""
    title = (candidate.get("title") or "").lower()
    snippet = (candidate.get("snippet") or "").lower()
    vanity = (candidate.get("vanity_name") or extract_vanity_name(profile_url) or "").lower()
    haystack = f"{title} {snippet} {profile_url} {vanity}"

    # 1) 域名根命中
    domain_root = _extract_domain_root(customer.website)
    if domain_root and len(domain_root) >= 3:
        normalized_root = re.sub(r"[-_.]", "", domain_root)
        if normalized_root and normalized_root in re.sub(r"[-_.]", "", haystack):
            score += 50

    # 2) 公司名命中（取 ≥3 字符的词）
    name_tokens = [
        w.strip(".,()").lower()
        for w in re.split(r"[\s\-_/]", customer.company_name or "")
        if len(w.strip(".,()")) >= 3
    ]
    if name_tokens and any(tok in haystack for tok in name_tokens):
        score += 30

    # 3) 国家/城市命中
    location_parts = [x for x in (customer.country, customer.city) if x]
    if location_parts and any(part.lower() in haystack for part in location_parts):
        score += 10

    # 4) 发现关键词命中
    if customer.discovery_keyword:
        kw_tokens = [w.lower() for w in re.split(r"[\s,，]+", customer.discovery_keyword) if len(w) >= 3]
        if kw_tokens and any(tok in snippet or tok in title for tok in kw_tokens):
            score += 10

    return min(score, 100.0)


async def discover_company_pages(
    company_name: str,
    website: Optional[str] = None,
    country: Optional[str] = None,
    city: Optional[str] = None,
    discovery_keyword: Optional[str] = None,
    user_id: Optional[int] = None,
    db: Session = None,
) -> List[dict]:
    """搜索 LinkedIn 公司主页候选（复用统一搜索引擎，运行时切换）"""
    from app.services.google_discovery import search_google

    domain_root = _extract_domain_root(website)
    queries = []
    for q in _DISCOVERY_QUERIES:
        q = q.replace("{company_name}", company_name or "")
        q = q.replace("{domain}", domain_root or "")
        q = q.replace("{country}", country or "")
        q = q.replace("{city}", city or "")
        q = re.sub(r'\s+', ' ', q).strip()
        if q and "site:linkedin.com/company" in q:
            queries.append(q)

    seen = {}
    for query in queries:
        try:
            results = await search_google(
                query,
                country=country or "",
                max_results=20,
                user_id=user_id,
                db=db,
            )
        except Exception:
            continue

        for r in results:
            url = normalize_company_url(r.get("website", ""))
            if not url:
                continue
            key = url
            if key in seen:
                continue
            seen[key] = {
                "profile_url": url,
                "vanity_name": extract_vanity_name(url),
                "title": (r.get("title") or "").strip()[:300],
                "snippet": (r.get("snippet") or "").strip()[:500],
                "source": "search",
            }

    candidates = list(seen.values())
    # 评分排序（仅排序，不自动确认）
    if candidates:
        scores = [score_company_page_candidate(c, Customer(
            company_name=company_name,
            website=website,
            country=country,
            city=city,
            discovery_keyword=discovery_keyword,
        )) for c in candidates]
        for c, s in zip(candidates, scores):
            c["confidence"] = s
        candidates.sort(key=lambda c: c["confidence"], reverse=True)
    return candidates


def upsert_social_profile(
    db: Session,
    customer_id: int,
    profile_url: str,
    source: str = "search",
    title: Optional[str] = None,
    snippet: Optional[str] = None,
    confidence: float = 0.0,
    is_verified: bool = False,
    created_by_user_id: Optional[int] = None,
    display_name: Optional[str] = None,
    website_url: Optional[str] = None,
) -> CustomerSocialProfile:
    """新增/更新客户社交主页候选（customer_id+platform+profile_type+profile_url 幂等）"""
    normalized = normalize_company_url(profile_url)
    if not normalized:
        raise ValueError("无效的 LinkedIn 公司主页 URL")

    record = (
        db.query(CustomerSocialProfile)
        .filter(
            CustomerSocialProfile.customer_id == customer_id,
            CustomerSocialProfile.platform == "linkedin",
            CustomerSocialProfile.profile_type == "company",
            CustomerSocialProfile.profile_url == normalized,
        )
        .first()
    )
    now = datetime.datetime.utcnow()
    if record:
        record.last_fetched_at = now
        record.updated_at = now
        if snippet:
            record.raw_json = snippet
        if confidence:
            record.confidence = confidence
        if display_name:
            record.display_name = display_name
        if website_url:
            record.website_url = website_url
        profile = record
    else:
        profile = CustomerSocialProfile(
            customer_id=customer_id,
            platform="linkedin",
            profile_type="company",
            profile_url=normalized,
            vanity_name=extract_vanity_name(normalized),
            display_name=display_name or title or "",
            source=source,
            confidence=confidence,
            is_verified=1 if is_verified else 0,
            raw_json=snippet,
            created_by_user_id=created_by_user_id,
            last_fetched_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(profile)

    if is_verified and not profile.is_verified:
        # 唯一已确认主页：清除同客户其他已确认标记
        db.query(CustomerSocialProfile).filter(
            CustomerSocialProfile.customer_id == customer_id,
            CustomerSocialProfile.id != profile.id,
        ).update({CustomerSocialProfile.is_verified: 0})
        profile.is_verified = 1
        profile.updated_at = now
    db.flush()
    return profile


def get_customer_social_profiles(db: Session, customer_id: int) -> List[CustomerSocialProfile]:
    """获取客户社交主页列表（已确认优先）"""
    return (
        db.query(CustomerSocialProfile)
        .filter(CustomerSocialProfile.customer_id == customer_id)
        .order_by(CustomerSocialProfile.is_verified.desc(), CustomerSocialProfile.confidence.desc())
        .all()
    )


def set_profile_verified(db: Session, profile_id: int, verified: bool) -> CustomerSocialProfile:
    """确认/取消确认候选主页（唯一已确认约束）"""
    profile = db.query(CustomerSocialProfile).filter(CustomerSocialProfile.id == profile_id).first()
    if not profile:
        raise LookupError("社交主页记录不存在")
    if verified and not profile.is_verified:
        db.query(CustomerSocialProfile).filter(
            CustomerSocialProfile.customer_id == profile.customer_id,
            CustomerSocialProfile.id != profile.id,
        ).update({CustomerSocialProfile.is_verified: 0})
        profile.is_verified = 1
    elif not verified and profile.is_verified:
        profile.is_verified = 0
    profile.updated_at = datetime.datetime.utcnow()
    db.flush()
    return profile


def delete_social_profile(db: Session, profile_id: int) -> bool:
    """删除社交主页记录"""
    profile = db.query(CustomerSocialProfile).filter(CustomerSocialProfile.id == profile_id).first()
    if not profile:
        return False
    db.delete(profile)
    db.flush()
    return True


def get_verified_linkedin_url(db: Session, customer_id: int) -> Optional[str]:
    """获取客户已确认的 LinkedIn 公司页 URL（Excel 导出等场景）"""
    profile = (
        db.query(CustomerSocialProfile)
        .filter(
            CustomerSocialProfile.customer_id == customer_id,
            CustomerSocialProfile.platform == "linkedin",
            CustomerSocialProfile.profile_type == "company",
            CustomerSocialProfile.is_verified == 1,
        )
        .first()
    )
    return profile.profile_url if profile else None

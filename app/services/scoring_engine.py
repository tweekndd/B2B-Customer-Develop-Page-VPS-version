"""
评分引擎（V4.0 — 全行业通用 Lead Qualification）

行业无关：对客户进行 7 维度 100 分制 AI Lead Scoring。
系统默认不绑定任何行业，行业专属内容通过 industry_config.json 的可选字段注入：
  - product_keywords：我方产品/方案关键词（并入 Product Fit 规则评分）
  - direct_competitors / existing_partner_signals：竞品与现有渠道名单（默认空）
  - product_mapping：场景 → 产品映射（默认空）

评分维度（与文档一致）：
    Product Fit                  30
    Application Fit              20
    Customer Type                15
    Commercial Purchase Prob.    15
    Technical / Business Fit     10
    Geographic Fit                5
    Company Credibility           5
    --------------------------------
    Total                       100

设计原则：
  - 评分配置从 industry_config.json 读取，无需修改代码即可调整
  - 优先采用 AI 返回的 score_breakdown（AI 必须给出 evidence），
    由本引擎按配置上下限做 clamp，再计算总分与 Lead Grade，保证结果可复现
  - AI 不可用时回退到规则关键词匹配，保证离线/降级仍可评分
  - 保留旧维度字段（industry_score/project_score/company_type_score/
    country_score/contact_score）以向后兼容旧 UI 与同步逻辑
"""
import json
import os
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple


def invalidate_config_cache():
    """清除评分配置缓存（API 写入配置后调用，使下次评分使用新规则）"""
    _load_config.cache_clear()
    _load_country_weights.cache_clear()


@lru_cache(maxsize=1)
def _load_config() -> dict:
    """加载行业配置中心（结果自动缓存，首次读取后不再访问磁盘）"""
    config_path = os.path.join(os.path.dirname(__file__), "industry_config.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"scoring": {}}


@lru_cache(maxsize=1)
def _load_country_weights() -> Dict[str, int]:
    """加载国家权重配置（结果自动缓存，首次读取后不再访问磁盘）"""
    config_path = os.path.join(os.path.dirname(__file__), "country_weights.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"Other": 3}


# ═══════════════════════════════════════════
# 基础匹配工具
# ═══════════════════════════════════════════

def _term_present(text_lower: str, term: str) -> bool:
    """判断关键词是否出现；多词用子串匹配，单词用词边界匹配"""
    if not term:
        return False
    term = term.strip().lower()
    if " " in term or "-" in term:
        return term in text_lower
    return re.search(r'\b' + re.escape(term) + r'\b', text_lower) is not None


def _match_terms(text_lower: str, terms: List[str]) -> List[str]:
    """返回文本中命中的所有关键词"""
    return [t for t in (terms or []) if _term_present(text_lower, t)]


def _clamp(value: Any, max_value: int) -> int:
    try:
        value = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(value, max_value))


def _ai_dim(ai_result: Optional[Dict[str, Any]], keys: List[str], max_value: int) -> Optional[int]:
    """从 AI 返回的 score_breakdown（或顶层）读取某维度分数（clamp 到上限）"""
    if not ai_result:
        return None
    breakdown = ai_result.get("score_breakdown") or {}
    for key in keys:
        if isinstance(breakdown, dict) and breakdown.get(key) is not None:
            return _clamp(breakdown[key], max_value)
        if ai_result.get(key) is not None:
            return _clamp(ai_result[key], max_value)
    return None


# ═══════════════════════════════════════════
# 旧版规则维度（保留向后兼容）
# ═══════════════════════════════════════════

def _score_industry_match(website_text: str) -> Tuple[int, str]:
    """行业匹配度（最高30分），从配置读取关键词权重（旧接口，保留兼容）"""
    config = _load_config()
    industry_cfg = config.get("scoring", {}).get("industry_match", {})
    max_score = industry_cfg.get("max_score", 30)
    keyword_weights = industry_cfg.get("keywords", {})

    if not website_text or not keyword_weights:
        return 0, "无官网内容"

    total_score = 0
    hit_details = []
    text_lower = website_text.lower()

    for keyword, weight in keyword_weights.items():
        pattern = re.compile(
            (re.escape(keyword) if " " in keyword else r'\b' + re.escape(keyword) + r'\b'),
            re.IGNORECASE,
        )
        count = len(pattern.findall(text_lower))
        if count > 0:
            points = min(weight, 5)
            total_score += points
            hit_details.append(f"{keyword}(+{points})")

    total_score = min(total_score, max_score)
    detail = "、".join(hit_details) if hit_details else "未命中目标关键词"
    return total_score, detail


def _score_project_match(website_text: str) -> Tuple[int, str]:
    """项目匹配度（旧接口，保留兼容），从配置读取"""
    config = _load_config()
    proj_cfg = config.get("scoring", {}).get("project_match", {})
    max_score = proj_cfg.get("max_score", 25)
    detection_kw = proj_cfg.get("detection_keywords", [])
    content_kw = proj_cfg.get("content_keywords", [])

    if not website_text:
        return 0, "无官网内容"

    score = 0
    details = []
    text_lower = website_text.lower()

    if any(kw.lower() in text_lower for kw in detection_kw):
        score += proj_cfg.get("has_project_base", 10)
        details.append(f"{proj_cfg.get('has_project_label', '存在项目案例页面')}(+{proj_cfg.get('has_project_base', 10)})")

    has_content = any(
        re.search(r'\b' + re.escape(kw) + r'\b', text_lower) for kw in content_kw
    )
    if has_content:
        score += proj_cfg.get("has_content_match", 15)
        details.append(f"{proj_cfg.get('has_content_label', '项目涉足目标行业')}(+{proj_cfg.get('has_content_match', 15)})")
    elif score > 0:
        score += proj_cfg.get("low_relevance", 5)
        details.append(f"{proj_cfg.get('low_relevance_label', '项目与目标行业相关度低')}(+{proj_cfg.get('low_relevance', 5)})")

    score = min(score, max_score)
    detail = "、".join(details) if details else "未识别到项目信息"
    return score, detail


def _score_company_type(company_type: Optional[str]) -> Tuple[int, str]:
    """公司类型分数（旧接口，公司类型 → 配置分数）"""
    config = _load_config()
    type_cfg = config.get("scoring", {}).get("company_type", {})
    type_scores = type_cfg.get("types", {})

    if not company_type:
        return 0, "未识别"

    score = type_scores.get(company_type, 0)
    if score == 0:
        ct_lower = company_type.lower()
        for key, val in type_scores.items():
            if key.lower() in ct_lower or ct_lower in key.lower():
                score = val
                break

    return score, f"{company_type}(+{score})"


def _score_country(country: Optional[str]) -> Tuple[int, str]:
    """国家优先级分数（Geographic Fit）"""
    if not country:
        return 0, "未指定国家"

    weights = _load_country_weights()
    score = weights.get(country)

    if score is None:
        country_lower = country.lower()
        score = 0
        matched = False
        for key, val in weights.items():
            if key.lower() in country_lower or country_lower in key.lower():
                score = val
                matched = True
                break
        if not matched:
            score = weights.get("Other", 1)

    max_score = _load_config().get("scoring", {}).get("country", {}).get("max_score", 5)
    score = min(score, max_score)
    return score, f"{country}(+{score})"


def _score_contact(emails: List[str]) -> Tuple[int, str]:
    """联系方式完整度分数（旧维度，保留兼容；不计入 7 维总分）"""
    contact_cfg = _load_config().get("scoring", {}).get("contact", {})
    tiers = contact_cfg.get("tiers", [])
    count = len(emails) if emails else 0

    score = 0
    for tier in tiers:
        if count >= tier.get("min_emails", 0):
            score = tier.get("score", 0)

    detail = f"{count}个邮箱(+{score})" if count > 0 else "无邮箱(+0)"
    return score, detail


def _score_price_inquiry(is_price_inquiry: bool) -> Tuple[int, str]:
    """价格询盘加成（旧维度，保留兼容）"""
    cfg = _load_config().get("scoring", {}).get("price_inquiry", {})
    max_score = cfg.get("max_score", 5)
    bonus = cfg.get("bonus", 5)

    if not is_price_inquiry:
        return 0, "非价格询盘"

    score = min(bonus, max_score)
    return score, f"价格询盘/采购信号(+{score})"


# ═══════════════════════════════════════════
# V3.0 新版 7 维评分
# ═══════════════════════════════════════════

def _score_product_fit(website_text: str, ai_result: Optional[dict]) -> Tuple[int, str]:
    """Product Fit — 30 分：公司是否存在与目标产品/方案直接相关的采购/分销需求"""
    ai_val = _ai_dim(ai_result, ["product_fit", "product_fit_score"], 30)
    if ai_val is not None:
        return ai_val, f"AI评估 Product Fit({ai_val}/30)"

    cfg = dict(_load_config().get("product_fit", {}))
    max_score = cfg.get("max_score", 30)
    text = (website_text or "").lower()
    if not text:
        return 0, "无官网内容"

    # 将用户配置的「我方产品/方案关键词」并入强关键词，实现全行业可插拔
    custom_products = [
        str(k).strip().lower()
        for k in (_load_config().get("product_keywords") or [])
        if str(k).strip()
    ]
    strong_cfg = list(cfg.get("strong_keywords", []))

    strong = _match_terms(text, strong_cfg + custom_products) if custom_products else _match_terms(text, strong_cfg)
    mid = _match_terms(text, cfg.get("mid_keywords", []))
    weak = _match_terms(text, cfg.get("weak_keywords", []))
    has_action = any(_term_present(text, v) for v in cfg.get("action_verbs", []))

    if strong:
        if len(strong) >= 2 or has_action:
            score = 30 if len(strong) >= 2 and has_action else 27
        else:
            score = 22
    elif len(mid) >= 2:
        score = 17
    elif len(mid) == 1:
        score = 12
    elif weak:
        score = 6
    else:
        score = 0

    score = min(score, max_score)
    detail = f"命中产品/需求关键词: {', '.join(strong or mid or weak[:3])}" if score else "未命中目标产品/需求关键词"
    return score, detail


def _score_application_fit(website_text: str, ai_result: Optional[dict]) -> Tuple[int, str]:
    """Application Fit — 20 分：公司是否存在实际的采购/应用/项目场景"""
    ai_val = _ai_dim(ai_result, ["application_fit", "application_fit_score"], 20)
    if ai_val is not None:
        return ai_val, f"AI评估 Application Fit({ai_val}/20)"

    cfg = _load_config().get("application_fit", {})
    max_score = cfg.get("max_score", 20)
    text = (website_text or "").lower()
    if not text:
        return 0, "无官网内容"

    strong = _match_terms(text, cfg.get("strong_keywords", []))
    mid = _match_terms(text, cfg.get("mid_keywords", []))
    weak = _match_terms(text, cfg.get("weak_keywords", []))

    if len(strong) >= 2:
        score = 20
    elif len(strong) == 1:
        score = 16
    elif len(mid) >= 2:
        score = 14
    elif len(mid) == 1:
        score = 10
    elif weak:
        score = 5
    else:
        score = 0

    score = min(score, max_score)
    detail = f"命中采购/应用场景: {', '.join(strong or mid or weak[:3])}" if score else "未识别采购/应用场景"
    return score, detail


def _score_customer_type(
    company_type: Optional[str],
    ai_result: Optional[dict],
) -> Tuple[int, str, str]:
    """Customer Type — 15 分。返回 (分数, 说明, 归一化客户类型)"""
    cfg = _load_config().get("scoring", {}).get("company_type", {})
    type_scores = cfg.get("types", {})
    max_score = cfg.get("max_score", 15)

    customer_type = ""
    if ai_result:
        customer_type = (ai_result.get("customer_type") or "").strip()
    if not customer_type:
        customer_type = (company_type or "").strip()

    if not customer_type:
        return 0, "未识别客户类型", ""

    # 先精确，再模糊匹配
    score = type_scores.get(customer_type)
    matched_type = customer_type if score is not None else None
    if score is None:
        ct_lower = customer_type.lower()
        for key, val in type_scores.items():
            if key.lower() in ct_lower or ct_lower in key.lower():
                score = val
                matched_type = key
                break
    if score is None:
        score = 0
        matched_type = customer_type

    score = _clamp(score, max_score)
    return score, f"{matched_type}(+{score})", matched_type


def _score_commercial_probability(
    website_text: str,
    ai_result: Optional[dict],
    is_price_inquiry: bool,
) -> Tuple[int, str]:
    """Commercial Purchase Probability — 15 分"""
    ai_val = _ai_dim(ai_result, ["commercial_probability", "commercial_probability_score", "commercial"], 15)
    if ai_val is not None:
        return ai_val, f"AI评估购买可能性({ai_val}/15)"

    cfg = _load_config().get("commercial_probability", {})
    max_score = cfg.get("max_score", 15)
    text = (website_text or "").lower()

    strong = _match_terms(text, cfg.get("strong_keywords", []))
    mid = _match_terms(text, cfg.get("mid_keywords", []))
    n = len(strong)

    if n >= 3:
        score = 15
    elif n == 2:
        score = 12
    elif n == 1:
        score = 9
    elif len(mid) >= 3:
        score = 6
    elif mid:
        score = 4
    else:
        score = 0

    if is_price_inquiry:
        score += 2

    score = min(score, max_score)
    detail = f"商业采购信号: {', '.join(strong[:3])}" if strong else ("商业解决方案信号" if score else "无明显采购信号")
    return score, detail


def _score_technical_fit(website_text: str, ai_result: Optional[dict]) -> Tuple[int, str]:
    """Technical / Business Capability — 10 分"""
    ai_val = _ai_dim(ai_result, ["technical_fit", "technical_fit_score", "technical"], 10)
    if ai_val is not None:
        return ai_val, f"AI评估技术集成能力({ai_val}/10)"

    cfg = _load_config().get("technical_fit", {})
    max_score = cfg.get("max_score", 10)
    text = (website_text or "").lower()
    if not text:
        return 0, "无官网内容"

    hits = _match_terms(text, cfg.get("keywords", []))
    strong_hits = _match_terms(text, cfg.get("strong_keywords", []))
    n = len(hits)

    if n >= 6 or (len(strong_hits) >= 2 and n >= 3):
        score = 10
    elif n >= 4:
        score = 8
    elif n >= 2:
        score = 6
    elif n == 1:
        score = 3
    else:
        score = 0

    score = min(score, max_score)
    detail = f"技术/业务关键词: {', '.join(hits[:5])}" if hits else "未识别技术/业务能力"
    return score, detail


def _score_credibility(website_text: str, ai_result: Optional[dict]) -> Tuple[int, str]:
    """Company Credibility — 5 分（最高 5，避免“大公司=高分客户”误区）"""
    ai_val = _ai_dim(ai_result, ["credibility", "credibility_score", "company_credibility"], 5)
    if ai_val is not None:
        return ai_val, f"AI评估可信度({ai_val}/5)"

    cfg = _load_config().get("credibility", {})
    max_score = cfg.get("max_score", 5)
    text = (website_text or "").lower()
    if not text:
        return 0, "无官网内容，无法验证"

    score = 0
    matched = []
    for signal in cfg.get("signals", []):
        if any(_term_present(text, kw) for kw in signal.get("keywords", [])):
            score += signal.get("score", 1)
            matched.append(signal.get("name", ""))
    if not matched:
        score = 1  # 有官网但缺少可验证信息
    score = min(score, max_score)
    detail = f"可信度信号: {', '.join(matched)}" if matched else "信息有限"
    return score, detail


def _detect_negative_flags(
    website_text: str,
    ai_result: Optional[dict],
    customer_type: str,
) -> Tuple[List[str], str]:
    """检测负面标记 / 排除逻辑。

    Returns:
        (negative_flags, lead_status_override)
        lead_status_override ∈ {None, "EXCLUDE", "EXISTING_CHANNEL"}
    """
    cfg = _load_config()
    text = (website_text or "").lower()
    flags: List[str] = []

    # 1. AI 直接给出的负面标记
    ai_flags = (ai_result or {}).get("negative_flags") or []
    if isinstance(ai_flags, list):
        flags.extend(str(f).strip() for f in ai_flags if str(f).strip())

    # 2. 直接竞争对手
    for comp in cfg.get("direct_competitors", []):
        if _term_present(text, comp):
            flags.append(f"DIRECT_COMPETITOR: {comp}")
    if customer_type and "competitor" in customer_type.lower():
        flags.append("DIRECT_COMPETITOR")

    # 3. 现有渠道 / 合作方（若配置了 existing_partner_signals，默认空）
    for sig in cfg.get("existing_partner_signals", []):
        if _term_present(text, sig):
            flags.append(f"EXISTING_PARTNER: {sig}")

    # 4. 默认低优先级类别
    for rule in cfg.get("exclusion_signals", []):
        if any(_term_present(text, kw) for kw in rule.get("keywords", [])):
            flags.append(f"EXCLUDED_CATEGORY: {rule.get('name')}")

    # 去重（保序）
    seen = set()
    unique_flags = []
    for f in flags:
        if f.lower() not in seen:
            seen.add(f.lower())
            unique_flags.append(f)

    override = None
    if any(f.startswith("DIRECT_COMPETITOR") for f in unique_flags):
        override = "EXCLUDE"
    elif any(f.startswith("EXISTING_PARTNER") for f in unique_flags):
        override = "EXISTING_CHANNEL"

    return unique_flags, override


def _classify_lead(total_score: int, override: Optional[str]) -> str:
    """Lead Classification：A / B / C / D / EXCLUDE

    override 为 EXCLUDE（竞争对手）或 EXISTING_CHANNEL（现有渠道伙伴）时，
    均不进入普通 Lead 池 → 返回 EXCLUDE（渠道伙伴由 recommended_action 区分）。
    """
    if override:
        return "EXCLUDE"
    rules = _load_config().get("priority_rules", {
        "A": {"min": 80}, "B": {"min": 65}, "C": {"min": 50}, "D": {"min": 30}, "EXCLUDE": {"min": 0},
    })
    for level, rule in sorted(rules.items(), key=lambda x: -x[1].get("min", 0)):
        if level == "EXCLUDE":
            continue
        if total_score >= rule.get("min", 0):
            return level
    return "EXCLUDE"


def _recommended_action(lead_grade: str, override: Optional[str]) -> str:
    """推荐的下一步动作"""
    if override == "EXISTING_CHANNEL":
        return "CHANNEL_PARTNER"
    if lead_grade == "EXCLUDE":
        return "EXCLUDE"
    rules = _load_config().get("recommended_action_rules", {})
    return rules.get(lead_grade, "RESEARCH_MORE")


def _recommend_products(website_text: str, customer_type: str, ai_result: Optional[dict]) -> List[str]:
    """根据网页证据推荐产品方向（未配置我方产品/映射时返回 []）"""
    if ai_result and isinstance(ai_result.get("recommended_products"), list):
        products = [str(p).strip() for p in ai_result["recommended_products"] if str(p).strip()]
        if products:
            return products

    text = (website_text or "").lower()
    if not text:
        return []

    config = _load_config()

    # 1) 命中我方产品关键词 → 直接作为推荐产品（product_keywords 常即产品/方案名）
    custom_products = [
        str(k).strip() for k in (config.get("product_keywords") or []) if str(k).strip()
    ]
    hits = [p for p in custom_products if _term_present(text, p)]
    if hits:
        return hits[:5]

    # 2) 场景 → 产品映射（键名按 /、, 拆分为匹配词，默认空映射时跳过）
    mapping = config.get("product_mapping") or {}
    for bucket, products in mapping.items():
        bucket_terms = [t.strip() for t in re.split(r"[/,、|]+", str(bucket)) if t.strip()]
        if bucket_terms and any(_term_present(text, t) for t in bucket_terms):
            return [str(p).strip() for p in products if str(p).strip()]

    return []


def _likely_use_cases(website_text: str, ai_result: Optional[dict]) -> List[str]:
    """推断可能的使用场景（优先 AI，其次通用 B2B 采购场景）"""
    if ai_result and isinstance(ai_result.get("likely_use_cases"), list):
        cases = [str(c).strip() for c in ai_result["likely_use_cases"] if str(c).strip()]
        if cases:
            return cases

    text = (website_text or "").lower()
    mapping = [
        ("distribution / reselling", ["distributor", "distributorship", "reseller", "dealer", "wholesaler", "distribution"]),
        ("import / export trade", ["importer", "import", "export", "trading company", "trading"]),
        ("project supply / EPC", ["epc", "contractor", "turnkey", "project supply", "tender", "bidding"]),
        ("system integration", ["system integrator", "system integration", "integration"]),
        ("procurement / sourcing", ["procurement", "purchasing", "sourcing", "supplier portal"]),
        ("after-sales & service", ["after-sales", "after sales", "spare parts", "maintenance", "service center"]),
    ]
    cases = [name for name, terms in mapping if any(_term_present(text, t) for t in terms)]
    return cases


def _gather_evidence(ai_result: Optional[dict]) -> List[str]:
    """提取 AI 给出的证据（AI 必须给出证据）"""
    if not ai_result:
        return []
    evidence = ai_result.get("evidence")
    if isinstance(evidence, list):
        return [str(e).strip() for e in evidence if str(e).strip()]
    # 兼容旧字段
    out = []
    for key in ("buyer_intent_reason", "analysis_reason"):
        val = ai_result.get(key)
        if val:
            out.append(str(val).strip())
    return out


def _confidence(ai_result: Optional[dict], evidence: List[str]) -> float:
    if ai_result and ai_result.get("confidence") is not None:
        try:
            return max(0.0, min(1.0, float(ai_result["confidence"])))
        except (TypeError, ValueError):
            pass
    return round(min(0.9, 0.4 + 0.1 * len(evidence)), 2)


# ═══════════════════════════════════════════
# 综合评分入口
# ═══════════════════════════════════════════

def calculate_scores(
    website_text: Optional[str],
    positive_keywords: Optional[Dict[str, int]],
    company_type: Optional[str],
    country: Optional[str],
    emails: List[str],
    is_price_inquiry: bool = False,
    buyer_intent_score: Optional[int] = None,
    ai_result: Optional[Dict[str, Any]] = None,
) -> Dict:
    """综合计算 7 维 Lead Score 与旧维度兼容字段。

    ai_result: AI 分析结果（含 score_breakdown / evidence / negative_flags 等）。
               若提供，则采用 AI 的维度分（按配置上限 clamp），否则回退规则匹配。
    """
    text = website_text or ""
    ai_result = ai_result or {}

    product_score, product_detail = _score_product_fit(text, ai_result)
    application_score, application_detail = _score_application_fit(text, ai_result)
    customer_type_score, customer_type_detail, customer_type = _score_customer_type(company_type, ai_result)
    commercial_score, commercial_detail = _score_commercial_probability(text, ai_result, is_price_inquiry)
    technical_score, technical_detail = _score_technical_fit(text, ai_result)
    geographic_ai = _ai_dim(ai_result, ["geographic_fit", "geographic", "country_fit"], 5)
    if geographic_ai is not None:
        geographic_score, geographic_detail = geographic_ai, f"AI评估地理匹配({geographic_ai}/5)"
    else:
        geographic_score, geographic_detail = _score_country(country)
    credibility_score, credibility_detail = _score_credibility(text, ai_result)

    total_score = (
        product_score + application_score + customer_type_score + commercial_score
        + technical_score + geographic_score + credibility_score
    )
    total_score = min(total_score, 100)

    # 排除 / 负面标记
    negative_flags, override = _detect_negative_flags(text, ai_result, customer_type)
    lead_grade = _classify_lead(total_score, override)
    recommended_action = _recommended_action(lead_grade, override)

    # 旧维度兼容
    contact_score, contact_detail = _score_contact(emails)
    price_score, price_detail = _score_price_inquiry(is_price_inquiry)
    priority = lead_grade if lead_grade in ("A", "B", "C", "D") else "D"

    recommended_products = _recommend_products(text, customer_type, ai_result)
    likely_use_cases = _likely_use_cases(text, ai_result)
    evidence = _gather_evidence(ai_result)
    confidence = _confidence(ai_result, evidence)
    sales_angle = (ai_result.get("sales_angle") or ai_result.get("sales_hook") or "")

    return {
        # ── V3.0 七维评分 ──
        "product_fit_score": product_score,
        "product_fit_detail": product_detail,
        "application_fit_score": application_score,
        "application_fit_detail": application_detail,
        "customer_type_score": customer_type_score,
        "customer_type_detail": customer_type_detail,
        "commercial_probability_score": commercial_score,
        "commercial_probability_detail": commercial_detail,
        "technical_fit_score": technical_score,
        "technical_fit_detail": technical_detail,
        "geographic_score": geographic_score,
        "geographic_detail": geographic_detail,
        "credibility_score": credibility_score,
        "credibility_detail": credibility_detail,
        "lead_grade": lead_grade,
        "recommended_action": recommended_action,
        "negative_flags": negative_flags,
        "recommended_products": recommended_products,
        "likely_use_cases": likely_use_cases,
        "evidence": evidence,
        "sales_angle": sales_angle,
        "confidence": confidence,
        "customer_type": customer_type,

        # ── 旧维度兼容字段 ──
        "industry_score": product_score,
        "industry_detail": product_detail,
        "project_score": application_score,
        "project_detail": application_detail,
        "company_type_score": customer_type_score,
        "company_type_detail": customer_type_detail,
        "country_score": geographic_score,
        "country_detail": geographic_detail,
        "contact_score": contact_score,
        "contact_detail": contact_detail,
        "price_inquiry_score": price_score,
        "price_inquiry_detail": price_detail,
        "buyer_intent_score": buyer_intent_score,

        "total_score": total_score,
        "priority": priority,
    }

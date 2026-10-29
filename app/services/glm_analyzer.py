"""
GLM AI分析服务（V6.5 — 全行业通用 Lead Qualification）

调用统一 LLM 接口（app.llm.manager）分析客户官网文本，行业无关：
识别「可能真实采购、分销、转售、集成我方产品/方案的 B2B 客户、渠道伙伴或项目伙伴」，
输出结构化、可审计的 Lead Qualification 结果（含 7 维评分明细 + 证据）。

设计原则（对应需求文档）：
  - AI 判断的是「这家公司是否可能成为我方产品的潜在商业客户/渠道/项目伙伴？」
    而不是「这是不是一家好公司？」
  - 只依据网页真实内容判断，不得臆测客户/项目/产品/营收/采购行为
  - 严格区分「提供产品/方案的公司」与「可能采购产品/方案的公司」——
    经销商/进口商/贸易商属于「为转售而采购」的高价值客户，不得因非终端用户而降分
  - 竞争对手 / 现有渠道伙伴仅当 industry_config.json 配置了对应名单或我方产品关键词时判断
  - 必须输出证据（evidence），不能只有一个分数
  - 证据不足时降低评分并显式说明
"""
import json
import logging
import os
from functools import lru_cache
from typing import Optional, Dict, Any, List

from app.llm.manager import get_llm_manager
from app.llm.utils import extract_json

logger = logging.getLogger("glm_analyzer")

_ANALYZER_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "industry_config.json")


@lru_cache(maxsize=1)
def _load_analyzer_config() -> dict:
    """读取行业配置（拿 product_keywords 等可选字段），读取失败返回空 dict"""
    try:
        with open(_ANALYZER_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def invalidate_analyzer_config_cache():
    """配置写入后清除缓存（config API 保存时调用）"""
    _load_analyzer_config.cache_clear()


SYSTEM_PROMPT = (
    "你是一位全行业通用的 B2B Lead Qualification 专家。"
    "你的任务不是找『所有相关公司』，而是找出『可能真实向我方采购、分销、转售、集成或"
    "在项目中采用我方产品/方案的 B2B 客户、渠道伙伴或项目合作伙伴』。"
    "必须严格区分：①「提供产品/方案的服务方」与「可能采购产品/方案的买家」——"
    "制造商是同类产品的供应商而我们也是卖家时不加分；经销商/进口商/贸易商属于「为转售而采购」"
    "的高价值客户，不得因非终端用户而降分。"
    "必须在负面标记中区分：潜在客户 vs 同类产品制造商（竞争对手）vs 我方现有渠道。"
    "只依据所给网页内容判断，绝不臆造客户、项目、产品、营收或采购行为。"
    "每个重要评分都必须给出网页证据（evidence）。只返回JSON格式数据。"
)



def _build_prompt(website_text: str) -> str:
    """构建发送给AI的提示词，控制在5000字符以内"""
    truncated_text = (website_text or "")[:5000]

    prompt = f"""请分析以下公司网页内容，判断这家公司是否可能成为我方产品/方案的潜在商业客户、渠道客户或项目合作伙伴，并返回严格的JSON（不要包含其他文字）：

网页内容：
{truncated_text}

【判断对象】
{_product_line_for_prompt()}

【评分维度（总分100，必须给出 score_breakdown）】
- product_fit (0-30)：网页是否明确出现与我方产品/方案直接相关的采购、分销、销售、应用业务，
  且公司在自己做产品的采购、分销、转售、集成或批量应用 → 25-30；
  明确从事相关产品的经销/进口/批发/集成/工程，证据充分但未指出具体型号层面 → 15-24；
  有泛商业/泛贸易/泛工程信号，证据较弱 → 5-14；与我方产品/方案基本无交叉点 → 0-4。
- application_fit (0-20)：存在明确的产品采购/应用/销售场景（经销网络、进口转售、
  工程项目供货、招标采购、批量应用面、售后服务与备件体系）→ 16-20；
  有项目/案例且业务与我方产品有可能的应用交叉 → 10-15；泛通用信号 → 5-9；无 → 0-4。
- customer_type (0-15)：Distributor / Importer / Trading Company / Wholesaler / Dealer /
  EPC Contractor / System Integrator / 系统集成商等「为转售或项目而采购」的主体 → 13-15；
  大型企业终端用户且有明确采购需求 → 12；零售商/中型终端 → 8-11；
  University/Research Lab → 3-5；媒体/招聘/咨询等无关主体 → 0。
- commercial_probability (0-15)：出现 request quote/RFQ/tender/bid/procurement/purchase order/
  become a distributor/dealer network/partner program/supplier portal/年度采购计划等真实商业信号 → 13-15；
  明确向商业客户销售/采购相关产品 → 9-12；业务能力明显但无采购/商业项目证据 → 5-8；
  主要为资讯/咨询/教育/个人主体 → 1-4；明显无 → 0。
- technical_fit (0-10)：业务配套与交付能力（ISO/CE 认证、质量管理、工程团队、
  OEM/ODM、售后服务体系、出口与物流、仓储供应能力等）。
- geographic_fit (0-5)：目标市场对产品出口/销售的通用吸引力；无特殊优势信息时给 2-3。
- credibility (0-5)：依据官网/成立时间/规模/客户/案例/认证/办公地点等证据。

【负面/排除逻辑（negative_flags）】
- 若我方产品/方案已明确（见【判断对象】），且证据显示该公司本身制造同类产品、与我方形成直接竞争
  → negative_flags 加入 "DIRECT_COMPETITOR"，recommended_action="EXCLUDE"。
- 若证据显示该公司已是我方现有授权分销/经销/合作渠道 → negative_flags 加入 "EXISTING_PARTNER"，
  recommended_action="CHANNEL_PARTNER"，customer_type="EXISTING_CHANNEL"。
- 招聘/媒体/新闻/营销代理/泛咨询/个人网站/纯软件/纯教育/明显无关主体 → negative_flags 加入对应类别。
- 若我方产品未明确，则不要臆断「同类产品制造商」，仅在证据非常明确时才加 DIRECT_COMPETITOR。

【硬性要求】
1. 只使用网页中真实出现的信息。
2. 不得编造客户、项目、产品、营收或采购行为。
3. 区分「公司提供解决方案/产品」与「公司可能采购产品/方案」。
4. 经销商/进口商/贸易商是高价值采购方（为转售而采购），不应因非终端用户而降分。
5. 区分竞争对手、现有渠道与潜在客户；证据不足时不要下竞品/渠道结论。
6. 证据不足时降低评分并明确写 "insufficient evidence"。
7. 不要仅凭公司所属行业宽泛背景就判定需求，必须以采购/分销/应用的具体证据为准。
8. 每个主要评分决策都要有 evidence。
9. 返回结构化 JSON。

【推荐产品（recommended_products，证据不足或我方产品未明确时返回空数组）】
- 仅当【判断对象】中给出了具体的我方产品/方案时，才从其中推荐最匹配该公司业务的产品；
- 不要推荐清单之外的任何产品名称。

请返回JSON，格式如下：
{{
    "company_type": "公司类型（Distributor / Importer / Trading Company / Wholesaler / Dealer / Exclusive Agent / EPC Contractor / System Integrator / End User / Retailer / Contractor / Manufacturer / University/Research Lab / Media/Consulting/Recruitment / Direct Competitor / EXISTING_CHANNEL / Other）",
    "customer_type": "同上，客户类型",
    "industry": "所属行业（英文，尽量具体，如 Industrial Automation / Construction Machinery / Food & Beverage 等）",
    "total_score": 0,
    "score_breakdown": {{
        "product_fit": 0,
        "application_fit": 0,
        "customer_type": 0,
        "commercial_probability": 0,
        "technical_fit": 0,
        "geographic_fit": 0,
        "credibility": 0
    }},
    "recommended_products": [],
    "likely_use_cases": [],
    "evidence": ["支持评分的关键网页证据1", "证据2", "证据3"],
    "sales_angle": "推荐开发切入点（中文，50字以内）",
    "negative_flags": [],
    "confidence": 0.0,
    "recommended_action": "CONTACT / RESEARCH_MORE / CHANNEL_PARTNER / EXISTING_PARTNER / EXCLUDE",
    "buyer_intent_score": 0,
    "buyer_intent_reason": "买家意向评分原因（中文，50字以内）",
    "is_price_inquiry": false,
    "needs_identified": ["客户可能需要的产品/方案1"],
    "product_match": "与我们产品最匹配的具体产品名（英文，无则空字符串）",
    "analysis_reason": "分析原因（中文，50字以内）",
    "sales_hook": "推荐开发切入点（中文，50字以内）",
    "target_position": "推荐联系职位（中文，如CEO / Procurement Manager / Project Manager）",
    "summary": "英文客户摘要，50字以内",
    "identified_projects": "如果页面中有项目/案例/采购信息，请提取描述（中文，100字以内），没有则返回空字符串",
    "address_city": "如果网页内容中包含公司地址/城市信息，提取城市名称（英文优先），没有则返回空字符串"
}}"""

    return prompt


def _product_line_for_prompt() -> str:
    """构建【判断对象】段落"""
    cfg = _load_analyzer_config()
    kws = [str(k).strip() for k in (cfg.get("product_keywords") or []) if str(k).strip()]
    if kws:
        return (
            "我方销售的产品/方案关键词如下（用自己的关键词判断客户匹配度，不要替换单一行业视角）：\n"
            + "、".join(kws)
        )
    return (
        "通用口径：未指定具体产品。判断该公司是否可能向我方采购、分销、转售、集成或在项目中采购产品/方案——"
        "重点看其是否为经销商/进口商/贸易商/批发商/集成商/承包商等「为转售或项目而采购」的主体，"
        "或存在明确的设备/产品采购需求。不要因为公司所属行业与某个特定行业不符而直接排除。"
    )


async def analyze_company(
    website_text: str,
    user_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """调用统一 LLM 接口分析公司，返回解析后的JSON字典。

    user_id: Round 3 支持按用户使用其自有 API Key（当前为环境变量配置）。
    全部模型失败或解析失败时返回 None（兼容旧逻辑）。
    """
    prompt = _build_prompt(website_text)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    result = await get_llm_manager().chat(
        messages,
        user_id=user_id,
        temperature=0.3,
        max_tokens=4096,
    )
    if result is None:
        logger.warning("AI 分析调用失败（所有模型均失败或未配置 Key）")
        return None

    parsed = extract_json(result.content)
    if isinstance(parsed, dict):
        return parsed

    logger.warning("AI 分析结果 JSON 解析失败: %s", result.content[:200])
    return None


# ═══════════════════════════════════════════
# 字段提取工具（供持久化/前端使用）
# ═══════════════════════════════════════════

def get_buyer_intent_score(ai_result: Optional[Dict[str, Any]]) -> Optional[int]:
    """从AI分析结果中提取买家意向分（0-10），无则返回None"""
    if not ai_result:
        return None
    score = ai_result.get("buyer_intent_score")
    if score is None:
        return None
    try:
        score = int(round(float(score)))
    except (TypeError, ValueError):
        return None
    return max(0, min(10, score))


def get_price_inquiry(ai_result: Optional[Dict[str, Any]]) -> bool:
    """从AI分析结果中判断是否价格询盘"""
    if not ai_result:
        return False
    return bool(ai_result.get("is_price_inquiry", False))


def get_customer_type(ai_result: Optional[Dict[str, Any]]) -> Optional[str]:
    if not ai_result:
        return None
    return (ai_result.get("customer_type") or ai_result.get("company_type") or "").strip() or None


def get_industry(ai_result: Optional[Dict[str, Any]]) -> Optional[str]:
    if not ai_result:
        return None
    return (ai_result.get("industry") or "").strip()[:100] or None


def _as_str_list(value: Any, limit: int = 20) -> List[str]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text[:500])
    return out[:limit]


def get_recommended_products(ai_result: Optional[Dict[str, Any]]) -> List[str]:
    return _as_str_list((ai_result or {}).get("recommended_products"))


def get_likely_use_cases(ai_result: Optional[Dict[str, Any]]) -> List[str]:
    return _as_str_list((ai_result or {}).get("likely_use_cases"))


def get_evidence(ai_result: Optional[Dict[str, Any]]) -> List[str]:
    return _as_str_list((ai_result or {}).get("evidence"))


def get_negative_flags(ai_result: Optional[Dict[str, Any]]) -> List[str]:
    return _as_str_list((ai_result or {}).get("negative_flags"))


def get_sales_angle(ai_result: Optional[Dict[str, Any]]) -> Optional[str]:
    if not ai_result:
        return None
    angle = (ai_result.get("sales_angle") or ai_result.get("sales_hook") or "").strip()
    return angle[:2000] or None


def get_recommended_action(ai_result: Optional[Dict[str, Any]]) -> Optional[str]:
    if not ai_result:
        return None
    action = (ai_result.get("recommended_action") or "").strip().upper()
    valid = {"CONTACT", "RESEARCH_MORE", "CHANNEL_PARTNER", "EXISTING_PARTNER", "EXCLUDE"}
    return action if action in valid else None


def get_confidence(ai_result: Optional[Dict[str, Any]]) -> Optional[float]:
    if not ai_result or ai_result.get("confidence") is None:
        return None
    try:
        return max(0.0, min(1.0, float(ai_result["confidence"])))
    except (TypeError, ValueError):
        return None


def generate_summary(ai_result: Dict[str, Any], customer_info: Dict[str, str]) -> str:
    """根据AI分析结果生成150字以内的英文摘要"""
    summary = ai_result.get("summary", "")
    if summary and len(summary) <= 150:
        return summary

    company_type = ai_result.get("customer_type") or ai_result.get("company_type", "")
    country = customer_info.get("country", "")
    parts = []
    if country:
        parts.append(country)
    if company_type and company_type != "Other":
        parts.append(company_type)

    summary = " ".join(parts) if parts else "Company"
    if len(summary) > 150:
        summary = summary[:147] + "..."

    return summary

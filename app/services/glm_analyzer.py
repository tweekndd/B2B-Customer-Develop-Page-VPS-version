"""
GLM AI分析服务（V5.1 重构）
调用统一 LLM 接口（app.llm.manager）分析客户官网文本
生成客户类型、采购概率、开发切入点、买家意向评分等分析结果
API Key 由 app.llm.config 统一解析（环境变量 / 用户配置）

V5.1 说明：吸收参考项目（b2b-buyer-discovery）的评分分级思想：
  - 供应商/制造商/工厂直销页 → 意向分 0（不是我们的客户）
  - 经销商/分销商/代理商/贸易商 → 高分（批量采购再转售，是高价值采购方）
  - 矿场/终端用户/EPC/承包商/招标 → 高分
"""
import logging
from typing import Optional, Dict, Any

from app.llm.manager import get_llm_manager
from app.llm.utils import extract_json

logger = logging.getLogger("glm_analyzer")

SYSTEM_PROMPT = (
    "你是一位专业的外贸客户分析专家。你的核心任务是区分「买家」和「供应商」："
    "供应商（制造商/工厂/卖家）不是客户，必须给低分；"
    "真正的买家（矿场/终端用户/EPC/承包商/经销商/分销商）才是客户，要给高分。"
    "特别注意：经销商/分销商/代理商/贸易商虽然自己不使用产品，但他们批量采购再转售，"
    "是高价值采购方，绝不能因为他们不是终端用户就降分。只返回JSON格式数据。"
)


def _build_prompt(website_text: str) -> str:
    """构建发送给AI的提示词，控制在4000字符以内"""
    truncated_text = website_text[:4000]

    prompt = f"""请分析以下公司网页内容，判断这家公司是不是潜在买家，并返回严格的JSON格式（不要包含其他文字）：

网页内容：
{truncated_text}

【评分标准 — 买家意向分 buyer_intent_score（0-10）】
只有明确是供应商/卖家才给 0 分：
- 制造商/工厂在推销自己的设备 → 0 分
- 工厂直销 / 产品目录 / 电商页面 → 0 分
- 供应商角度的报价单 → 0 分
- Alibaba / Made-in-China / 黄页 → 0 分

除此之外给 5-10 分（这些才是潜在客户）：
- 矿场/采石场/终端用户要采购设备 → 8-10 分
- EPC 总包 / 承包商有采购需求 → 7-9 分
- 政府/项目招标 → 8-10 分
- 经销商 / 分销商 / 代理商 / 贸易商寻求供货 → 7-9 分（高价值采购方，不要降分）
- 二手设备买家询盘 → 7-8 分
- 一般行业讨论 → 5-6 分
- 无法判断 → 5 分

【需要识别的其他信息】
- 公司类型 company_type：EPC / Contractor / Distributor / Dealer / Trader / Importer / End User / Mining Company / Manufacturer / Consultant / Other
- 采购需求 needs_identified：从网页中提取他们可能需要的产品/设备（数组）
- 产品匹配 product_match：最匹配我们产品的具体设备名（英文）
- 是否价格询盘 is_price_inquiry：网页中是否出现 price/how much/quotation/tender/procurement/RFQ 等采购询价信号（true/false）

请分析并返回JSON，格式如下：
{{
    "company_type": "公司类型（EPC / Contractor / Distributor / Dealer / Trader / Importer / End User / Mining Company / Manufacturer / Consultant / Other）",
    "buyer_intent_score": 0,
    "buyer_intent_reason": "评分原因（中文，50字以内）",
    "is_price_inquiry": false,
    "needs_identified": ["客户可能需要的设备1", "设备2"],
    "product_match": "最匹配我们产品的设备名（英文）",
    "analysis_reason": "分析原因（中文，50字以内）",
    "sales_hook": "推荐开发切入点（中文，50字以内）",
    "target_position": "推荐联系职位（中文，如CEO / Procurement Manager / Project Manager）",
    "summary": "英文客户摘要，50字以内",
    "identified_projects": "如果页面中有项目案例信息，请提取描述（中文，100字以内），没有则返回空字符串",
    "address_city": "如果网页内容中包含公司地址/城市信息，提取城市名称（英文优先，如无英文可用中文），没有则返回空字符串",
}}"""

    return prompt


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


def generate_summary(ai_result: Dict[str, Any], customer_info: Dict[str, str]) -> str:
    """根据AI分析结果生成150字以内的英文摘要"""
    # 优先使用AI返回的summary字段
    summary = ai_result.get("summary", "")
    if summary and len(summary) <= 150:
        return summary

    # 备用：手动拼接
    company_type = ai_result.get("company_type", "")
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

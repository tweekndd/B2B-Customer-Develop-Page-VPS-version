"""
GLM AI分析服务（V6.0 — Robotics Lead Qualification）

调用统一 LLM 接口（app.llm.manager）分析客户官网文本，面向
Robotics / Automation / AMR / AGV / Mobile Robot / Robotic Arm / Embodied AI 行业，
输出结构化、可审计的 Lead Qualification 结果（含 7 维评分明细 + 证据）。

设计原则（对应需求文档）：
  - AI 判断的是「这家公司是否可能成为机器人平台产品的潜在商业客户/渠道/项目伙伴？」
    而不是「这是不是一家好公司？」
  - 只依据网页真实内容判断，不得臆测客户/项目/产品/营收/采购行为
  - 严格区分「提供解决方案的公司」与「可能采购解决方案的公司」
  - 严格区分现有 AgileX 合作方 / 竞争对手 / 潜在客户
  - 必须输出证据（evidence），不能只有一个分数
  - 证据不足时降低评分并显式说明
"""
import logging
from typing import Optional, Dict, Any, List

from app.llm.manager import get_llm_manager
from app.llm.utils import extract_json

logger = logging.getLogger("glm_analyzer")

SYSTEM_PROMPT = (
    "你是一位专注于 Robotics / Automation / AMR / AGV / Mobile Robot / Robotic Arm / "
    "Embodied AI 行业的 B2B Lead Qualification 专家。"
    "你的任务不是找『所有机器人公司』，而是找出『可能真实采购、集成、分销、部署或指定开放机器人平台"
    "（如 AgileX 松灵机器人）的 B2B 客户、渠道伙伴或项目合作伙伴』。"
    "必须严格区分：潜在客户 vs 竞争对手 vs 现有渠道。"
    "只依据所给网页内容判断，绝不臆造客户、项目、产品、营收或采购行为。"
    "每个重要评分都必须给出网页证据（evidence）。只返回JSON格式数据。"
)


def _build_prompt(website_text: str) -> str:
    """构建发送给AI的提示词，控制在5000字符以内"""
    truncated_text = (website_text or "")[:5000]

    prompt = f"""请分析以下公司网页内容，判断这家公司是否可能成为 Robotics / AMR / AGV / mobile robot / robotic arm / mobile manipulation 平台类产品的潜在商业客户、渠道客户或项目合作伙伴，并返回严格的JSON（不要包含其他文字）：

网页内容：
{truncated_text}

【判断对象】
目标产品参考 AgileX Robotics（松灵机器人）这类开放式机器人平台，包括：
Mobile Robot Chassis、AMR/AGV Development Platform、UGV、Robotic Arm、Mobile Manipulation Platform、
Teleoperation Platform、Embodied AI / Data Collection Platform、Robotics Development Kit、OEM Robotics Platform。
不要把目标市场理解成传统 Industrial Robot（KUKA/FANUC/ABB/Yaskawa 等）。

【评分维度（总分100，必须给出 score_breakdown）】
- product_fit (0-30)：网页是否明确出现 AMR/AGV/mobile robot/mobile robot chassis/UGV/robotic arm/
  mobile manipulation/teleoperation/robot data collection/embodied AI/robotics development platform/robotics OEM，
  且公司自己在提供、开发、集成或采购相关产品 → 25-30；
  明确从事 robotics integration / industrial automation / warehouse automation / machine vision / smart factory / robotics engineering 但未明确目标产品 → 15-24；
  泛自动化、证据较弱 → 5-14；与 robotics/automation 基本无关 → 0-4。
- application_fit (0-20)：实际机器人应用场景（AMR/AGV deployment、autonomous inspection、mobile manipulation、
  autonomous delivery、warehouse robotics、outdoor UGV、robot data collection、embodied AI）→ 18-20；
  logistics/factory automation、smart warehouse、industrial inspection → 12-17；泛工业自动化 → 5-11；无 → 0-4。
- customer_type (0-15)：Robotics System Integrator / Automation SI / Robotics OEM / Mobile Robot OEM /
  Robotics Distributor / Robotics Solution Provider → 15；大型企业终端用户且有明确自动化项目 → 12；
  中型制造/物流/仓储企业有自动化需求 → 8；University/Research Lab/Research Institute → 5；
  普通教育机构 → 3；个人/媒体/招聘/咨询等无关主体 → 0。
- commercial_probability (0-15)：出现 current project/deployment/procurement/system integration project/
  automation project/robotics project/customer deployment/commercial solution/turnkey/pilot project → 13-15；
  明确向商业客户销售机器人/自动化方案 → 9-12；技术能力明显但无采购/商业项目证据 → 5-8；
  主要研究/咨询/教育/媒体 → 1-4；明显无 → 0。
- technical_fit (0-10)：ROS/ROS2/Python/C++/SLAM/Navigation/LiDAR/Computer Vision/AI/Machine Vision/
  Sensor Integration/Embedded Systems/Mechanical/Robotics Engineering/Autonomous Navigation 等。
- geographic_fit (0-5)：Singapore=5, Thailand=5, Malaysia=4, Vietnam/Indonesia/Philippines=3, Other=1。
- credibility (0-5)：依据官网/成立时间/规模/客户/项目/案例/认证/办公地点等证据。

【负面/排除逻辑（negative_flags）】
- 若确认是直接竞争对手（KUKA/FANUC/ABB/Yaskawa/Universal Robots/MiR/Geek+/Hai Robotics/Hikrobot/
  OTTO Motors 等机器人平台制造商）→ negative_flags 加入 "DIRECT_COMPETITOR"，recommended_action="EXCLUDE"。
- 若网页明确说明是 AgileX distributor/authorized distributor/partner/reseller → negative_flags 加入
  "EXISTING_PARTNER"，recommended_action="CHANNEL_PARTNER"，customer_type="EXISTING_CHANNEL"。
- 招聘/媒体/新闻/营销代理/泛咨询/个人网站/招聘板/纯软件/纯教育/完全无关制造商 → negative_flags 加入对应类别。

【硬性要求】
1. 只使用网页中真实出现的信息。
2. 不得编造客户、项目、产品、营收或采购行为。
3. 区分「公司提供解决方案」与「公司可能采购解决方案」。
4. 区分现有 AgileX 合作方与潜在客户。
5. 区分竞争对手与潜在客户。
6. 证据不足时降低评分并明确写 "insufficient evidence"。
7. 不要仅凭 "automation" 一词推断机器人需求。
8. 不要把所有 robotic-arm 公司都当成潜在客户。
9. 每个主要评分决策都要有 evidence。
10. 返回结构化 JSON。

【推荐产品映射（recommended_products，证据不足时返回空数组）】
- AMR/Warehouse → Mobile Robot Chassis / TRACER / SCOUT
- Outdoor/Inspection → SCOUT / RANGER / BUNKER / Outdoor UGV
- Mobile Manipulation → NERO / PiPER / Mobile Manipulation Platform
- Embodied AI → NERO / PiPER / Universal Master Arm / Teleoperation Platform / Data Collection Platform
- Robotics Education/R&D → PiPER / NERO / LIMO / SCOUT / Development Kit
- OEM/Robotics SI → Mobile Robot Chassis / Robotic Arm / OEM Platform / Customized Robotics Platform

请返回JSON，格式如下：
{{
    "company_type": "公司类型（Robotics System Integrator / Automation System Integrator / Robotics OEM / Mobile Robot OEM / AMR/AGV Integrator / Robotics Distributor / Robotics Solution Provider / Robotics Startup / Embodied AI Company / Enterprise End User / Manufacturer / Logistics/Warehouse Operator / University/Research Lab / Education Provider / Recruitment/Media/Consulting / Direct Competitor / Other）",
    "customer_type": "同上，客户类型",
    "industry": "所属行业（英文，如 Robotics / Industrial Automation / Warehouse Logistics 等）",
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
    "needs_identified": ["客户可能需要的产品/设备1"],
    "product_match": "最匹配我们产品的具体产品名（英文，无则空字符串）",
    "analysis_reason": "分析原因（中文，50字以内）",
    "sales_hook": "推荐开发切入点（中文，50字以内）",
    "target_position": "推荐联系职位（中文，如CEO / Procurement Manager / Project Manager）",
    "summary": "英文客户摘要，50字以内",
    "identified_projects": "如果页面中有机器人/自动化项目案例信息，请提取描述（中文，100字以内），没有则返回空字符串",
    "address_city": "如果网页内容中包含公司地址/城市信息，提取城市名称（英文优先），没有则返回空字符串"
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

"""
Lead Qualification 字段持久化助手（V6.0）

把 AI 分析结果与评分引擎输出统一写入 Customer 主表，避免在多个调用点重复代码。
所有字段均为 V6.0 新增的向后兼容列；旧字段（company_type / industry_score /
total_score / priority 等）继续同步，保证旧 UI 与同步逻辑不受影响。
"""
import json
from typing import Any, Dict, Optional

from app.services.glm_analyzer import (
    get_buyer_intent_score,
    get_price_inquiry,
    get_customer_type,
    get_industry,
    get_recommended_products,
    get_likely_use_cases,
    get_evidence,
    get_negative_flags,
    get_sales_angle,
    get_recommended_action,
    get_confidence,
)


def _dump(value) -> str:
    return json.dumps(value or [], ensure_ascii=False)


def apply_ai_lead_fields(customer, ai_result: Optional[Dict[str, Any]]) -> str:
    """把 AI 分析结果写入 Customer 的 Lead Qualification 字段。

    Returns:
        AI 提取的城市（可能为空字符串，供调用方按需回填 customer.city）。
    """
    if not ai_result:
        return ""

    # 旧字段（保持兼容）
    customer.company_type = ai_result.get("company_type") or ai_result.get("customer_type") or ""
    customer.sales_hook = ai_result.get("sales_hook", "")
    customer.target_position = ai_result.get("target_position", "")
    customer.identified_projects = ai_result.get("identified_projects", "")

    # V6.0 新字段
    customer.customer_type = get_customer_type(ai_result) or customer.company_type
    customer.industry = get_industry(ai_result)
    customer.sales_angle = get_sales_angle(ai_result) or customer.sales_hook
    customer.recommended_products = _dump(get_recommended_products(ai_result))
    customer.likely_use_cases = _dump(get_likely_use_cases(ai_result))
    customer.evidence = _dump(get_evidence(ai_result))
    customer.negative_flags = _dump(get_negative_flags(ai_result))
    customer.confidence = get_confidence(ai_result)
    customer.recommended_action = get_recommended_action(ai_result)

    # 买家意向 / 价格询盘（V4.6 兼容字段）
    customer.buyer_intent_score = get_buyer_intent_score(ai_result)
    customer.is_price_inquiry = 1 if get_price_inquiry(ai_result) else 0

    return (ai_result.get("address_city") or "").strip()


def apply_score_fields(customer, scores: Dict[str, Any]) -> None:
    """把评分引擎输出写入 Customer 的 7 维评分列与旧维度兼容列。"""
    # V6.0 七维
    customer.product_fit_score = scores.get("product_fit_score")
    customer.application_fit_score = scores.get("application_fit_score")
    customer.customer_type_score = scores.get("customer_type_score")
    customer.commercial_probability_score = scores.get("commercial_probability_score")
    customer.technical_fit_score = scores.get("technical_fit_score")
    customer.geographic_score = scores.get("geographic_score")
    customer.credibility_score = scores.get("credibility_score")
    customer.lead_grade = scores.get("lead_grade")
    customer.recommended_action = scores.get("recommended_action") or customer.recommended_action
    customer.total_score = scores.get("total_score")
    customer.priority = scores.get("priority")

    # 旧维度兼容
    customer.industry_score = scores.get("industry_score")
    customer.project_score = scores.get("project_score")
    customer.company_type_score = scores.get("company_type_score")
    customer.country_score = scores.get("country_score")
    customer.contact_score = scores.get("contact_score")

    # 结构化输出（评分引擎已优先采用 AI，缺失时回退规则推断）
    if scores.get("recommended_products"):
        customer.recommended_products = _dump(scores["recommended_products"])
    if scores.get("likely_use_cases"):
        customer.likely_use_cases = _dump(scores["likely_use_cases"])
    if scores.get("evidence"):
        customer.evidence = _dump(scores["evidence"])
    if scores.get("negative_flags") is not None:
        customer.negative_flags = _dump(scores["negative_flags"])
    if scores.get("sales_angle"):
        customer.sales_angle = scores["sales_angle"]
    if scores.get("confidence") is not None:
        customer.confidence = scores["confidence"]
    if scores.get("customer_type") and not customer.customer_type:
        customer.customer_type = scores["customer_type"]


def build_run_ai_result(ai_result: Optional[Dict[str, Any]], scores: Dict[str, Any]) -> Dict[str, Any]:
    """合并 AI 原始结果与评分引擎结果，用于写入 AnalysisRun（含最终 grade/action）。"""
    merged: Dict[str, Any] = dict(ai_result or {})
    merged["total_score"] = scores.get("total_score")
    merged["lead_grade"] = scores.get("lead_grade")
    merged["recommended_action"] = scores.get("recommended_action")
    merged["negative_flags"] = scores.get("negative_flags") or merged.get("negative_flags") or []
    if scores.get("recommended_products"):
        merged["recommended_products"] = scores["recommended_products"]
    if scores.get("likely_use_cases"):
        merged["likely_use_cases"] = scores["likely_use_cases"]
    if scores.get("evidence"):
        merged["evidence"] = scores["evidence"]
    if scores.get("sales_angle"):
        merged["sales_angle"] = scores["sales_angle"]
    if scores.get("confidence") is not None:
        merged["confidence"] = scores["confidence"]
    if not merged.get("customer_type") and scores.get("customer_type"):
        merged["customer_type"] = scores["customer_type"]
    return merged

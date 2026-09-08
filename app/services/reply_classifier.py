"""回复意图分类服务（Phase2 新增：方案 9.1 结构化输出）

流程：规则预判（退信/退订/自动回复）→ LLM 分类（结构化 JSON）→ 规则兜底 → Guard 校验。

输出分类（classification/intent）：
    rfq / interest / question / not_interested / unsubscribe / out_of_office /
    bounce / complaint / other

待办动作映射（Phase2 只建待办，RFQ 落库留 Phase3）：
    rfq/question  → 创建「询价/回复」待办
    interest      → 创建「跟进」待办
    complaint     → 创建「投诉处理」待办
"""
import datetime
import json
import logging
import re
from typing import Any, Dict, List, Optional

from app.llm.manager import get_llm_manager
from app.llm.utils import extract_json

logger = logging.getLogger("reply_classifier")

# 分类枚举（与 app/models/inbox.py 及前端展示一致）
INTENT_RFQ = "rfq"
INTENT_INTEREST = "interest"
INTENT_QUESTION = "question"
INTENT_NOT_INTERESTED = "not_interested"
INTENT_UNSUBSCRIBE = "unsubscribe"
INTENT_OUT_OF_OFFICE = "out_of_office"
INTENT_BOUNCE = "bounce"
INTENT_COMPLAINT = "complaint"
INTENT_OTHER = "other"

_ALLOWED_INTENTS = {
    INTENT_RFQ, INTENT_INTEREST, INTENT_QUESTION, INTENT_NOT_INTERESTED,
    INTENT_UNSUBSCRIBE, INTENT_OUT_OF_OFFICE, INTENT_BOUNCE, INTENT_COMPLAINT,
    INTENT_OTHER,
}

# 分类 → 待办类型（app/models/inbox.py TODO_*）
_REQUIRED_FIELDS = ("intent", "confidence", "needs_human_review")

# ── 规则预判（不调 LLM，快且稳） ─────────────────────────────────

# 退信：退信常见主题/内容
_BOUNCE_PATTERNS = [
    r"(?i)\b(mail delivery failed|delivery status notification|undeliverable|undelivered mail)",
    r"(?i)\b(mailer[- ]daemon|postmaster)\b",
    r"(?i)\b(returned mail|return to sender)\b",
    r"(?i)\b(recipient address rejected|invalid recipient|no such user|user unknown|host not found)",
    r"(?i)^failed|^undeliverable|^returned mail:",
]
_BOUNCE_HEADERS = ("x-failed-recipients", "x-failure-reason", "diagnostic-code", "x-priority-flag")
_BOUNCE_CONTENT_TYPES = ("multipart/report", "message/delivery-status")

# 退订
_UNSUBSCRIBE_PATTERNS = [
    r"(?i)\b(unsubscribe|remove me|stop (the )?emails|opt[- ]?out)\b",
    r"退订|不再需要|请不要(再)?发",
    r"please (do not|don.t) (send|email)",
]

# 自动回复（Out of office / vacation）
_OOO_PATTERNS = [
    r"(?i)\b(out of office|vacation aut(a|o)reply|on (the )?go|unavailable)\b",
    r"出差|休假|不在办公室|自动回复",
]

# 询价/采购关键词
_RFQ_PATTERNS = [
    r"(?i)\b(quote|quotation|price|cost|offer|moq|payment terms)\b",
    r"(?i)\b(delivery|lead time|shipping|fob|cif|incoterms)\b",
    r"(?i)\b(catalog|sample|catalogue|spec|specification)\b",
    r"(?i)\b(please (send|provide|share|quote))\b",
    r"(?i)\b(buy|purchase|order|unit|qty|quantity)\b",
    r"询价|报价|价格|样品|交期|起订量|想买",
]

# 合作意向
_INTEREST_PATTERNS = [
    r"(?i)\b(interested|would like to (know|learn|hear)|great (product|info))\b",
    r"(?i)\b(please (call|contact|tell) me)\b",
    r"(?i)\b(further information|more details)\b",
    r"感兴趣|想了解|详细说明",
]

# 投诉
_COMPLAINT_PATTERNS = [
    r"(?i)\b(complain|complaint|unacceptable|disappointed|terrible|bad service)\b",
    r"投诉|不满|差评|问题严重",
]

# 冷淡/婉拒
_NOT_INTERESTED_PATTERNS = [
    r"(?i)\b(not interested|no (longer )?need|don.t need|stop contacting)\b",
    r"(?i)\b(not (a )?good fit|remove (me )?from (the )?list)\b",
    r"不需要|不感兴趣|别再联系",
]


def _scan(text: str, patterns: List[str]) -> bool:
    if not text:
        return False
    return any(re.search(p, text) for p in patterns)


def rule_preclassify(subject: str, body: str, headers: Dict[str, str], content_type: str) -> Optional[Dict[str, Any]]:
    """纯规则预判退信/退订/自动回复/MOO 兜底（无 LLM 时的最后防线）。

    返回与 LLM 同构的 dict（intent/confidence/needs_human_review），未命中返回 None。
    """
    text = f"{subject or ''}\n{body or ''}"
    # 退信（报文结构 + 头 + 内容）
    ct = (content_type or "").lower()
    if ct.startswith(_BOUNCE_CONTENT_TYPES) or any(h in headers for h in _BOUNCE_HEADERS):
        return {"intent": INTENT_BOUNCE, "confidence": 0.98, "needs_human_review": False,
                "next_action": "blacklist", "reason": "bounce_report"}
    if _scan(text, _BOUNCE_PATTERNS):
        return {"intent": INTENT_BOUNCE, "confidence": 0.95, "needs_human_review": False,
                "next_action": "blacklist", "reason": "bounce_keyword"}
    # 退订
    if _scan(text, _UNSUBSCRIBE_PATTERNS):
        return {"intent": INTENT_UNSUBSCRIBE, "confidence": 0.92, "needs_human_review": False,
                "next_action": "blacklist", "reason": "unsubscribe_keyword"}
    # 自动回复
    if _scan(text, _OOO_PATTERNS):
        return {"intent": INTENT_OUT_OF_OFFICE, "confidence": 0.9, "needs_human_review": False,
                "next_action": "none", "reason": "ooo_keyword"}
    return None


# ── LLM 分类 ───────────────────────────────────────────────────────

_CLASSIFY_SYSTEM_PROMPT = """你是一个 B2B 外贸客户回复意图分类器。
根据客户回复邮件（主题 + 正文），输出唯一 JSON，字段：
- "intent": 取值限定为 rfq|interest|question|not_interested|unsubscribe|out_of_office|bounce|complaint|other
- "confidence": 0~1 的置信度
- "product": 客户提及的产品/品类（无则 null，英文）
- "quantity": 客户询的数量/数量词（无则 null）
- "destination": 客户提及的目的地国家/港口（无则 null）
- "missing_fields": 你判断还需向客户确认的关键字段数组（如 moq, payment, delivery）
- "next_action": 取值 create_todo|manual_reply|none
- "needs_human_review": 布尔，是否必须人工介入
- "summary": 一句话中文摘要（该邮件要做什么）
只输出 JSON，不要任何解释。"""


def _fallback_classify(subject: str, body: str) -> Dict[str, Any]:
    """LLM 不可用时的规则兜底（粗略但可用）。"""
    text = f"{subject or ''}\n{body or ''}"
    if _scan(text, _COMPLAINT_PATTERNS):
        return {"intent": INTENT_COMPLAINT, "confidence": 0.7, "needs_human_review": True,
                "next_action": "create_todo", "missing_fields": []}
    if _scan(text, _RFQ_PATTERNS):
        return {"intent": INTENT_RFQ, "confidence": 0.7, "needs_human_review": False,
                "next_action": "create_todo", "missing_fields": ["moq", "delivery", "payment"]}
    if _scan(text, _INTEREST_PATTERNS):
        return {"intent": INTENT_INTEREST, "confidence": 0.65, "needs_human_review": False,
                "next_action": "create_todo", "missing_fields": []}
    if _scan(text, _NOT_INTERESTED_PATTERNS):
        return {"intent": INTENT_NOT_INTERESTED, "confidence": 0.7, "needs_human_review": True,
                "next_action": "none", "missing_fields": []}
    return {"intent": INTENT_OTHER, "confidence": 0.5, "needs_human_review": True,
            "next_action": "manual_reply", "missing_fields": []}


def _sanitize(parsed: Any) -> Optional[Dict[str, Any]]:
    """结构校验 + 白名单归一化。非法返回 None。"""
    if not isinstance(parsed, dict):
        return None
    intent = str(parsed.get("intent") or "").strip().lower()
    if intent not in _ALLOWED_INTENTS:
        return None
    try:
        conf = float(parsed.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0
    conf = max(0.0, min(1.0, conf))
    next_action = str(parsed.get("next_action") or "create_todo").strip().lower()
    if next_action not in ("create_todo", "manual_reply", "none"):
        next_action = "create_todo"
    for f in ("product", "quantity", "destination"):
        if not isinstance(parsed.get(f), str):
            parsed[f] = None
    mf = parsed.get("missing_fields")
    if not isinstance(mf, list):
        mf = []
    return {
        "intent": intent,
        "confidence": round(conf, 3),
        "product": parsed.get("product"),
        "quantity": parsed.get("quantity"),
        "destination": parsed.get("destination"),
        "missing_fields": mf,
        "next_action": next_action,
        "needs_human_review": bool(parsed.get("needs_human_review")),
        "summary": str(parsed.get("summary") or "")[:500],
    }


async def classify_reply(
    subject: str,
    body: str,
    headers: Optional[Dict[str, str]] = None,
    content_type: Optional[str] = None,
    user_id: Optional[int] = None,
    llm_available: bool = True,
) -> Dict[str, Any]:
    """对单封回复邮件分类，返回结构化 dict。

    顺序：规则预判 → LLM → 规则兜底。任何阶段失败都不抛异常（分类失败降级为 other）。
    """
    headers = headers or {}
    rule = rule_preclassify(subject, body, headers, content_type or "")
    if rule:
        rule.update({"product": None, "quantity": None, "destination": None,
                     "missing_fields": [], "summary": ""})
        return rule

    if llm_available:
        try:
            result = await get_llm_manager().chat(
                [
                    {"role": "system", "content": _CLASSIFY_SYSTEM_PROMPT},
                    {"role": "user", "content": f"主题: {subject or ''}\n---\n正文:\n{body or '(空)'}"},
                ],
                user_id=user_id,
                temperature=0.1,
                max_tokens=512,
            )
            raw = result.output_text if result and getattr(result, "output_text", None) else None
            parsed = extract_json(raw) if raw else None
            sanitized = _sanitize(parsed)
            if sanitized:
                return sanitized
            logger.warning("回复分类输出非法，回退规则兜底: %.120s", raw)
        except Exception as exc:  # 分类不阻断主流程
            logger.warning("回复分类 LLM 调用失败，回退规则兜底: %s", exc)

    fallback = _fallback_classify(subject, body)
    return fallback


def classification_to_dict(cls_dict: Dict[str, Any]) -> Dict[str, Any]:
    return cls_dict


def parse_classification_json(raw: str) -> Optional[Dict[str, Any]]:
    """解析存储的 classification_json 文本（容错）。"""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None
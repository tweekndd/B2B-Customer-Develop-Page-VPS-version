"""
Generation Guard（Phase1 新增）— 生成输出的规则检查

方案 9.6「执行事实、长度、敏感词、退订和品牌规则检查」、9.5 自由度约束：

在 AI 输出结构化解析后、保存/进入审核队列前执行：
- 结构化字段必须齐全（subject/body/language/facts_used/...）
- 长度与语言合规
- 禁止承诺（价格/交期/认证/库存）类高危词提示
- 退订/停止联系相关措辞不被删除（发信内容完整性由人工确认，这里仅提示）
- 敏感词 / 品牌冒充（禁止自称他人品牌、政府授权等）
- 输出中夹带提示注入指令的拦截

Guard 只做「标记 + 阻断严重项」，最终是否发送仍由人工审批决定。
"""
import json
import re
from typing import Any, Dict, List

# 高危承诺词（出现即 needs_review，但文案可能是「我们不承诺价格」等否定句式，
# 交由审核人判断 —— 故只标 risk 不阻断）
_CLAIM_PATTERNS = [
    (r"(?i)\b(price|pricing|quote)\b[^\n]{0,40}\b(usd|€|\$|rmb|yuan|per\s*(unit|piece|kg))", "疑似价格承诺"),
    (r"(?i)\b(lead\s*time|delivery|shipment)\b[^\n]{0,30}\b\d{1,3}\s*(days|weeks)", "疑似交期承诺"),
    (r"(?i)\b(ce|rohs|iso\s*9001|ul\s*listed|atex|sabs)\b", "认证陈述需人工核实"),
    (r"(?i)\b(in\s*stock|inventory|available\s*now|ready\s*to\s*ship)\b", "疑似库存承诺"),
]

# 退订/停止联系等必须保留的合规措辞（出现则提示勿删；缺失不阻断——发信内容由人工审批）
_COMPLIANCE_TERMS = [
    (r"(?i)\b(unsubscribe|opt\s*out|stop\s*receiving|退订)\b", "退订措辞"),
]

# 品牌冒充/权威背书
_BRAND_FLAG_PATTERNS = [
    (r"(?i)\bofficial\s+(agent|distributor|partner)\s+of\s+\b", "疑似冒充官方授权"),
    (r"(?i)\b(government|un|who|unicef|world\s+bank)\s*(approved|authorized|endorsed)\b", "疑似冒充权威背书"),
    (r"(?i)\bguarantee(d)?\s+(price|delivery|quality)\b", "绝对化保证"),
]

# Prompt 注入（输出内容中试图改写规则）
_INJECTION_PATTERNS = [
    r"(?i)ignore\s+(previous|all|above|your)",
    r"(?i)forget\s+(previous|all|your)",
    r"(?i)you\s+are\s+now",
    r"忽略(之前|以上|所有)",
    r"不要遵守",
]


class GuardResult:
    def __init__(self, checks: Dict[str, Dict[str, Any]], risk_flags: List[str], needs_human_review: bool):
        self.checks = checks
        self.risk_flags = risk_flags
        self.needs_human_review = needs_human_review

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checks": self.checks,
            "risk_flags": self.risk_flags,
            "needs_human_review": self.needs_human_review,
        }


def validate_structured_output(parsed: Any) -> List[str]:
    """检查 AI 输出是否满足固定 JSON Schema，返回缺失字段列表（空=通过）"""
    if not isinstance(parsed, dict):
        return ["output_not_object"]
    missing = []
    for field in ("subject", "body"):
        if not isinstance(parsed.get(field), str) or not parsed.get(field).strip():
            missing.append(field)
    if "language" in parsed and not isinstance(parsed.get("language"), str):
        missing.append("language")
    return missing


def _scan(text: str, patterns: List, flags: List[str], tag: str) -> List[str]:
    for regex, label in patterns:
        if re.search(regex, text or ""):
            flags.append(f"{label}（{tag}）")
    return flags


def run_guard(
    parsed: Dict[str, Any],
    *,
    max_subject_len: int = 150,
    max_body_len: int = 8000,
) -> GuardResult:
    """对结构化输出执行规则检查，返回 GuardResult。

    严重问题（缺字段/超长/注入）→ needs_human_review=True + 记录；不阻断落库。
    发送层另有硬性校验。
    """
    checks: Dict[str, Dict[str, Any]] = {}
    risk_flags: List[str] = []

    # 1) 结构
    missing = validate_structured_output(parsed)
    checks["structure"] = {"passed": not missing, "message": "缺少字段: " + ", ".join(missing) if missing else "字段齐全"}
    if missing:
        risk_flags.append("结构化字段缺失")

    subject = str(parsed.get("subject", "") or "")
    body = str(parsed.get("body", "") or "")
    full_text = subject + "\n" + body

    # 2) 长度
    checks["length"] = {
        "passed": len(subject) <= max_subject_len and len(body) <= max_body_len,
        "message": f"subject={len(subject)}/{max_subject_len} body={len(body)}/{max_body_len}",
    }
    if len(subject) > max_subject_len or len(body) > max_body_len:
        risk_flags.append("内容超长，需人工截断")

    # 3) 高危承诺 / 认证（仅标记，审核判断）
    _scan(full_text, _CLAIM_PATTERNS, risk_flags, "高危承诺")
    _scan(full_text, _BRAND_FLAG_PATTERNS, risk_flags, "品牌/背书")

    # 4) 提示注入
    injection_hits = [p for p in _INJECTION_PATTERNS if re.search(p, full_text)]
    checks["injection"] = {
        "passed": not injection_hits,
        "message": "命中注入模式" if injection_hits else "无注入迹象",
    }
    if injection_hits:
        risk_flags.append("输出疑似夹带提示注入指令")

    # 5) 退订等合规词（提示勿删）
    compliance_hits = [label for regex, label in _COMPLIANCE_TERMS if re.search(regex, full_text)]
    checks["compliance_terms"] = {
        "passed": True,
        "message": "包含: " + ", ".join(compliance_hits) if compliance_hits else "未包含退订类措辞（如需可人工补充）",
    }

    # 6) facts_used 必须来自输入快照的白名单字段（无法深度校验，此处仅统计）
    facts_used = parsed.get("facts_used") if isinstance(parsed.get("facts_used"), list) else []
    checks["facts_used"] = {"passed": len(facts_used) > 0, "message": f"引用事实 {len(facts_used)} 条"}

    # 7) claims_requiring_review —— AI 自报的可疑陈述
    claims = parsed.get("claims_requiring_review") if isinstance(parsed.get("claims_requiring_review"), list) else []
    if claims:
        risk_flags.append(f"AI 自报 {len(claims)} 条需核实陈述")

    needs_human_review = bool(risk_flags) or not checks["structure"]["passed"]
    return GuardResult(checks, risk_flags, needs_human_review)


def guard_to_json(guard: GuardResult) -> str:
    return json.dumps(guard.to_dict(), ensure_ascii=False)

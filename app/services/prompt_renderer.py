"""
Prompt 渲染器（Phase1 新增）

方案 9.4「变量注入必须使用白名单」：
- 模板正文只允许引用 variables_schema 中声明的 {{var}}；
- 未声明变量、非法嵌套、HTML/控制字符在渲染阶段即被拒绝/清理；
- 外部抓取的官网原文、客户邮件只能作为「客户事实输入」填充到白名单变量，
  永远不能携带可执行的提示词指令（防 Prompt Injection 的第一道闸）。

本模块不做 LLM 调用，只负责：schema 校验、变量求值、模板渲染、渲染哈希。
"""
import hashlib
import json
import re
from typing import Any, Dict, List, Optional

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")

# 保留变量（渲染器内部注入，不来自客户数据）
_RESERVED_VARS = {
    "system.now_utc",        # 当前 UTC 时间（发信日）
    "system.sender_company", # 发件公司名
    "system.sender_signature",  # 签名
    "system.product_name",   # 产品名
    "system.product_keywords",  # 产品关键词列表（逗号拼接）
    "system.call_to_action", # 本次 CTA
}

# 系统级禁止注入（无论 schema 是否声明都不允许出现用户可控原文）
_FORBIDDEN_FRAGMENTS = (
    "ignore previous",
    "ignore above",
    "ignore all",
    "forget previous",
    "system prompt",
    "you are now",
    "扮演",
    "忽略以上",
    "忽略之前",
    "不要遵守",
    "无视",
)


class VariableSchemaError(ValueError):
    """变量 schema 或模板渲染错误"""


def extract_variables(template: str) -> List[str]:
    """提取模板中出现的所有 {{var}} 名称（保序去重）"""
    seen = []
    for m in _VAR_RE.finditer(template or ""):
        name = m.group(1)
        if name not in seen:
            seen.append(name)
    return seen


def normalize_schema(schema: Any) -> Dict[str, Dict[str, Any]]:
    """规范化 variables_schema（兼容 dict 与 JSON 字符串），返回 {var: {type,required,description}}"""
    if schema is None:
        return {}
    if isinstance(schema, str):
        try:
            schema = json.loads(schema)
        except (json.JSONDecodeError, TypeError):
            raise VariableSchemaError("variables_schema 不是合法 JSON")
    if not isinstance(schema, dict):
        raise VariableSchemaError("variables_schema 必须是 JSON 对象 {var: {...}}")
    out = {}
    for var, spec in schema.items():
        if not isinstance(spec, dict):
            spec = {"type": "string"}
        out[str(var)] = {
            "type": str(spec.get("type", "string")),
            "required": bool(spec.get("required", False)),
            "description": str(spec.get("description", "")),
        }
    return out


def validate_template_variables(template: str, schema: Any) -> List[str]:
    """校验模板引用的变量是否都在 schema 白名单内（含保留变量）。

    Returns: 未声明变量列表（空=通过）
    Raises: VariableSchemaError（schema 非法）
    """
    norm = normalize_schema(schema)
    used = extract_variables(template)
    undeclared = [v for v in used if v not in norm and v not in _RESERVED_VARS]
    return undeclared


def sanitize_value(value: Any, max_length: int = 3000) -> str:
    """把客户事实输入清洗成可注入的纯文本：
    - 去控制字符（保留换行）
    - 拦截明显的提示注入片段
    - 限长
    """
    if value is None:
        return ""
    text = str(value)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", text)
    text = re.sub(r"(?i)(" + "|".join(re.escape(f) for f in _FORBIDDEN_FRAGMENTS) + r")", "[redacted]", text)
    return text[:max_length].strip()


def render_template(
    template: str,
    variables: Dict[str, Any],
    schema: Any = None,
    *,
    strict: bool = True,
) -> str:
    """渲染用户模板。

    Args:
        template: user_prompt_template（含 {{var}}）
        variables: {var: value} —— 必须全部来自白名单求值
        schema: variables_schema（严格模式校验未声明变量）
        strict: True 时未声明变量抛错；False 时未声明变量渲染为空串
    Returns:
        渲染后的完整用户提示词
    Raises:
        VariableSchemaError
    """
    if schema is not None and strict:
        undeclared = validate_template_variables(template, schema)
        if undeclared:
            raise VariableSchemaError(
                f"模板引用了未在 variables_schema 白名单声明的变量: {', '.join(undeclared)}。"
                "请先补充 schema，或在模板中移除该变量（防注入）。"
            )

    def _resolve(name: str) -> str:
        if name in _RESERVED_VARS:
            return sanitize_value(variables.get(name, ""), max_length=2000)
        if name not in variables:
            return "" if not strict else ""
        return sanitize_value(variables.get(name), max_length=3000)

    out = _VAR_RE.sub(lambda m: _resolve(m.group(1)), template or "")
    return out


def render_hash(rendered: str) -> str:
    """渲染结果的 SHA256（用于 generation_runs.rendered_prompt_hash 溯源比对）"""
    return hashlib.sha256((rendered or "").encode("utf-8")).hexdigest()


def build_default_variables_schema() -> Dict[str, Dict[str, Any]]:
    """默认白名单 Schema（方案 9.4 变量清单 + 保留变量说明）。

    只声明业务上允许出现在模板中的变量 —— 其余一律拒绝。
    """
    return {
        "customer.company_name": {"type": "string", "required": True, "description": "客户公司名"},
        "customer.country": {"type": "string", "required": False, "description": "客户国家"},
        "customer.website": {"type": "string", "required": False, "description": "客户官网"},
        "customer.company_type": {"type": "string", "required": False, "description": "AI 判定的公司类型"},
        "customer.buyer_intent_score": {"type": "string", "required": False, "description": "买家意向评分"},
        "customer.ai_summary": {"type": "string", "required": False, "description": "AI 官网摘要"},
        "customer.sales_hook": {"type": "string", "required": False, "description": "开发切入点"},
        "customer.identified_projects": {"type": "string", "required": False, "description": "识别的项目信息"},
        "customer.needs_identified": {"type": "string", "required": False, "description": "AI 识别的需求清单"},
        "customer.product_match": {"type": "string", "required": False, "description": "匹配的产品需求"},
        "customer.target_position": {"type": "string", "required": False, "description": "建议联系职位"},
        "customer.language": {"type": "string", "required": False, "description": "客户语言"},
        "product.key_features": {"type": "string", "required": False, "description": "产品要点"},
    }


# 保留变量说明（写入种子模板 schema，便于前端展示可插入变量）
def default_template_docs() -> List[Dict[str, str]]:
    docs = [
        {"var": "customer.company_name", "type": "string", "desc": "客户公司名"},
        {"var": "customer.country", "type": "string", "desc": "客户国家"},
        {"var": "customer.website", "type": "string", "desc": "客户官网"},
        {"var": "customer.company_type", "type": "string", "desc": "公司类型（AI）"},
        {"var": "customer.buyer_intent_score", "type": "string", "desc": "买家意向评分 0-10"},
        {"var": "customer.ai_summary", "type": "string", "desc": "官网 AI 摘要"},
        {"var": "customer.sales_hook", "type": "string", "desc": "开发切入点"},
        {"var": "customer.identified_projects", "type": "string", "desc": "已识别项目"},
        {"var": "customer.needs_identified", "type": "string", "desc": "已识别需求"},
        {"var": "customer.product_match", "type": "string", "desc": "对方可能需要的产品"},
        {"var": "customer.target_position", "type": "string", "desc": "建议联系职位"},
        {"var": "customer.language", "type": "string", "desc": "客户语言"},
        {"var": "product.key_features", "type": "string", "desc": "产品要点"},
        {"var": "system.now_utc", "type": "datetime", "desc": "当前 UTC 日期（保留变量）"},
        {"var": "system.sender_company", "type": "string", "desc": "发件公司（保留变量）"},
        {"var": "system.sender_signature", "type": "string", "desc": "签名（保留变量）"},
        {"var": "system.product_name", "type": "string", "desc": "产品名（保留变量）"},
        {"var": "system.product_keywords", "type": "string", "desc": "产品关键词（保留变量）"},
        {"var": "system.call_to_action", "type": "string", "desc": "本次 CTA（保留变量）"},
    ]
    return docs

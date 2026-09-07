"""
外联生成编排服务（Phase1 新增）

闭环：客户事实 → Prompt(active 版本) → 白名单渲染 → LLM 结构化输出
    → generation_guard 规则检查 → generation_runs 溯源 → outreach_drafts 草稿

方案 9.6 Prompt 运行流程的服务侧实现；草稿进入人工审核队列（pending 由前端/API 触发）。
"""
import datetime
import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.database import (
    Customer,
    GenerationRun,
    OutreachDraft,
    PromptTemplate,
    PromptVersion,
)
from app.models.prompts import FREEDOM_L0, FREEDOM_L1, FREEDOM_L2
from app.models.outreach_send import DRAFT_STATUS_DRAFT, DRAFT_STATUS_PENDING
from app.llm.manager import get_llm_manager
from app.llm.utils import extract_json
from app.services import prompt_service, prompt_renderer
from app.services.generation_guard import run_guard, guard_to_json
from app.services.country_language_map import get_language_info

logger = logging.getLogger("outreach_generation")

# 每个自由度的输出要求差异见 generation prompt 构造
_FREEDOM_HUMAN_READABLE = {
    FREEDOM_L0: "L0 严格模板",
    FREEDOM_L1: "L1 受控改写",
    FREEDOM_L2: "L2 个性化创作",
}


class GenerationError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


# ── 客户事实构建（白名单） ────────────────────────────────────────

def _safe_json(raw, default=None):
    if not raw:
        return default
    try:
        v = json.loads(raw)
        return v if v is not None else default
    except (json.JSONDecodeError, TypeError):
        return default


def _list_to_text(value, limit: int = 300) -> str:
    if not value:
        return ""
    if isinstance(value, list):
        return "、".join(str(v) for v in value)[:limit]
    return str(value)[:limit]


def _latest_analysis(db: Session, customer: Customer) -> Dict[str, Any]:
    """读取客户最新成功 AI 分析（analysis_runs 表；无则回退主表字段）"""
    from app.services.intelligence_service import list_analysis_runs
    try:
        runs = list_analysis_runs(db, customer.id, limit=1)
    except Exception:  # noqa: BLE001 - 分析历史读取失败不应阻断草稿
        runs = []
    raw = {}
    if runs and runs[0].raw_json:
        raw = _safe_json(runs[0].raw_json, {}) or {}
    if not raw and customer.ai_raw_json:
        raw = _safe_json(customer.ai_raw_json, {}) or {}
    return raw if isinstance(raw, dict) else {}


def build_customer_facts(db: Session, customer: Customer,
                         product_keywords: Optional[List[str]] = None,
                         extra_context: str = "") -> Dict[str, str]:
    """把客户记录 + 最新分析整理成「白名单变量求值结果」（供 renderer 注入）。

    任何外部原文只出现在既定字段中；返回 dict 中每个键都应在默认 schema 内。
    """
    ai = _latest_analysis(db, customer)
    needs = ai.get("needs_identified") or []
    projects = ai.get("identified_projects") or customer.identified_projects or ""
    product_match = ai.get("product_match") or ""

    country = customer.country or ""
    lang_info = get_language_info(country)
    language_code = (lang_info or {}).get("hl", "en")

    keywords = [k.strip() for k in (product_keywords or []) if k and k.strip()]
    if not keywords:
        # 无显式关键词时优先 AI 匹配的产品需求，其次命中正向行业词
        kws_from_ai = (ai.get("product_match") or "").strip()
        if kws_from_ai:
            keywords = [kws_from_ai]
    if not keywords and (customer.positive_keywords):
        pos = _safe_json(customer.positive_keywords, [])
        if isinstance(pos, list):
            keywords = [str(k) for k in pos[:3]]

    facts: Dict[str, str] = {
        "customer.company_name": customer.company_name or "",
        "customer.country": country,
        "customer.website": customer.website or "",
        "customer.company_type": customer.company_type or "",
        "customer.buyer_intent_score": str(customer.buyer_intent_score) if customer.buyer_intent_score is not None else "",
        "customer.ai_summary": customer.ai_summary or "",
        "customer.sales_hook": customer.sales_hook or "",
        "customer.identified_projects": _list_to_text(projects) if not isinstance(projects, str) else projects[:500],
        "customer.needs_identified": _list_to_text(needs),
        "customer.product_match": product_match or "",
        "customer.target_position": customer.target_position or "",
        "customer.language": language_code,
    }
    # 只保留非空，减少模板意外注入空壳
    if extra_context:
        facts["customer.product_match"] = ((facts.get("customer.product_match") or "") + "；" + extra_context).strip("；")[:800]
    return facts


def detect_language_code(country: Optional[str]) -> str:
    info = get_language_info(country or "")
    return (info or {}).get("hl", "en")


# ── 生成 prompt（system 按自由度） ────────────────────────────────

def _build_system_with_freedom(base_system: str, freedom_level: str) -> str:
    """按自由度注入输出边界（方案 9.5）。"""
    extra = {
        FREEDOM_L0: (
            "\n【本次自由度 L0 严格模板】只允许调整明显的语法问题与占位，"
            "句式与内容尽量保持模板原样，禁止增加任何模板外的事实或说法。"
        ),
        FREEDOM_L1: (
            "\n【本次自由度 L1 受控改写】可以调整句子顺序、语气与长度，"
            "但不得增加未经证实的客户事实；事实仅来自【目标客户事实】。"
        ),
        FREEDOM_L2: (
            "\n【本次自由度 L2 个性化创作】可以基于【目标客户事实】生成更具针对性的"
            "切入点、主题与 CTA，但每一处个性化表述都必须引用上面的客户事实作为依据，"
            "不得虚构项目/需求/数据。"
        ),
    }
    return (base_system or "") + extra.get(freedom_level, extra[FREEDOM_L1])


# ── 主入口 ────────────────────────────────────────────────────────

async def generate_outreach_draft(
    db: Session,
    customer: Customer,
    *,
    user_id: Optional[int] = None,
    template_id: Optional[int] = None,
    product_keywords: Optional[List[str]] = None,
    extra_context: str = "",
    product_name: str = "",
    sender_company: str = "",
    sender_signature: str = "",
    call_to_action: str = "",
    language: str = "auto",
    auto_submit: bool = False,
) -> Dict[str, Any]:
    """生成一封可溯源的外联草稿（走 Prompt 模板体系）。

    Returns:
        {"draft": {...}, "generation_run": {...}}
    Raises:
        GenerationError
    """
    template = prompt_service.get_active_template(db, template_id)
    if template is None:
        raise GenerationError(
            "尚未配置可用的 Prompt 模板：请先在「Prompt 管理」发布一个 active 模板",
            status_code=400,
        )
    version = (
        db.query(PromptVersion)
        .filter(PromptVersion.template_id == template.id,
                PromptVersion.version == template.current_version)
        .first()
    )
    if version is None:
        # 模板从未发布但有内容：仍以模板当前内容生成（记录 template 无版本）
        version = None

    # 1) 语言
    lang_code = language if language and language != "auto" else detect_language_code(customer.country)

    # 2) 渲染白名单变量
    facts = build_customer_facts(db, customer, product_keywords=product_keywords, extra_context=extra_context)
    reserved_vars = {
        "system.now_utc": datetime.datetime.utcnow().strftime("%Y-%m-%d"),
        "system.sender_company": sender_company,
        "system.sender_signature": sender_signature,
        "system.product_name": product_name,
        "system.product_keywords": "、".join([k for k in (product_keywords or []) if k]) or product_name,
        "system.call_to_action": call_to_action,
    }
    all_vars = {**facts, **reserved_vars}
    schema = template.variables_schema_json
    try:
        rendered_user = prompt_renderer.render_template(
            template.user_prompt_template, all_vars, schema, strict=True
        )
    except prompt_renderer.VariableSchemaError as e:
        raise GenerationError(f"Prompt 渲染失败: {e}", status_code=400)

    # 语言强制注入
    rendered_user = rendered_user.replace("{{language_hint}}", lang_code)
    system_prompt = _build_system_with_freedom(template.system_prompt, template.freedom_level)
    # 追加结构化输出要求（版本或模板内容已含 JSON schema 描述；此处兜底不强改）
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": rendered_user},
    ]

    # 3) 模型配置
    model_cfg = prompt_service._parse_json(template.model_config_json) or {}
    temperature = float(model_cfg.get("temperature", 0.7))
    max_tokens = int(model_cfg.get("max_tokens", 2048))

    # 4) 调用 LLM
    result = await get_llm_manager().chat(
        messages,
        user_id=user_id,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    run = GenerationRun(
        customer_id=customer.id,
        prompt_template_id=template.id,
        prompt_version_id=version.id if version else None,
        freedom_level=template.freedom_level,
        provider="",
        model="",
        model_config_json=json.dumps(model_cfg, ensure_ascii=False) if model_cfg else None,
        input_snapshot_json=json.dumps(facts, ensure_ascii=False),
        rendered_prompt_hash=prompt_renderer.render_hash(rendered_user),
        created_by_user_id=user_id,
    )

    if result is None or result.content is None:
        run.result_status = "model_failed"
        run.error_message = "LLM 调用失败（未配置 Key 或所有模型降级失败）"
        run.raw_output_text = ""
        db.add(run)
        db.commit()
        db.refresh(run)
        raise GenerationError("开发信生成失败：LLM 调用失败，请检查 AI 设置页的 API Key/模型", status_code=502)

    run.provider = result.provider or ""
    run.model = result.model or ""
    run.raw_output_text = result.content or ""

    parsed = extract_json(result.content)
    if not isinstance(parsed, dict):
        run.result_status = "parse_failed"
        run.error_message = "LLM 输出不是结构化 JSON"
        db.add(run)
        db.commit()
        db.refresh(run)
        raise GenerationError("开发信生成失败：AI 输出无法解析为 JSON，请重试或更换模板", status_code=502)

    run.output_json = json.dumps(parsed, ensure_ascii=False)

    # 5) Guard 规则检查
    guard = run_guard(parsed)
    run.checks_json = guard_to_json(guard)
    run.risk_flags_json = json.dumps(guard.risk_flags, ensure_ascii=False)
    run.result_status = "success"
    db.add(run)
    db.commit()
    db.refresh(run)

    subject = str(parsed.get("subject", "")).strip()
    body = str(parsed.get("body", "")).strip()
    if not subject or not body:
        raise GenerationError("生成结果缺少主题或正文", status_code=502)

    # 6) 草稿落库（待审批或草稿）
    recipient_email = None
    if auto_submit:
        # 自动提交审批时优先带出客户主邮箱（无邮箱也不阻断，审批阶段再要求）
        from app.services.outreach_service import get_customer_primary_email
        recipient_email = get_customer_primary_email(db, customer.id) or None

    draft = OutreachDraft(
        customer_id=customer.id,
        recipient_email=recipient_email,
        subject=subject,
        body_text=body,
        language=parsed.get("language") or lang_code or "en",
        tone=str(parsed.get("tone", "")).strip() or None,
        generation_run_id=run.id,
        prompt_template_id=template.id,
        prompt_version_id=version.id if version else None,
        model=run.model or None,
        facts_used_json=json.dumps(parsed.get("facts_used", []), ensure_ascii=False),
        risk_flags_json=run.risk_flags_json,
        status=DRAFT_STATUS_PENDING if auto_submit else DRAFT_STATUS_DRAFT,
        created_by_user_id=user_id,
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)

    return {
        "draft": _draft_to_dict(draft),
        "generation_run": _run_to_dict(run),
        "guard": guard.to_dict(),
    }


# ── 序列化 ────────────────────────────────────────────────────────

def _run_to_dict(r: GenerationRun) -> Dict[str, Any]:
    return {
        "id": r.id,
        "customer_id": r.customer_id,
        "prompt_template_id": r.prompt_template_id,
        "prompt_version_id": r.prompt_version_id,
        "freedom_level": r.freedom_level,
        "provider": r.provider,
        "model": r.model,
        "input_snapshot": prompt_renderer.sanitize_value(
            prompt_service._parse_json(r.input_snapshot_json) if r.input_snapshot_json else {}, max_length=4000
        ),
        "rendered_prompt_hash": r.rendered_prompt_hash,
        "checks": prompt_service._parse_json(r.checks_json),
        "risk_flags": prompt_service._parse_json(r.risk_flags_json) or [],
        "result_status": r.result_status,
        "error_message": r.error_message,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _draft_to_dict(d: OutreachDraft) -> Dict[str, Any]:
    return {
        "id": d.id,
        "customer_id": d.customer_id,
        "sender_account_id": d.sender_account_id,
        "recipient_email": d.recipient_email,
        "recipient_name": d.recipient_name,
        "subject": d.subject,
        "body": d.body_text,
        "language": d.language,
        "tone": d.tone,
        "generation_run_id": d.generation_run_id,
        "prompt_template_id": d.prompt_template_id,
        "prompt_version_id": d.prompt_version_id,
        "model": d.model,
        "facts_used": prompt_service._parse_json(d.facts_used_json) or [],
        "risk_flags": prompt_service._parse_json(d.risk_flags_json) or [],
        "status": d.status,
        "human_edited": bool(d.human_edited),
        "review_note": d.review_note,
        "reviewed_by_user_id": d.reviewed_by_user_id,
        "reviewed_at": d.reviewed_at.isoformat() if d.reviewed_at else None,
        "approved_at": d.approved_at.isoformat() if d.approved_at else None,
        "created_by_user_id": d.created_by_user_id,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }

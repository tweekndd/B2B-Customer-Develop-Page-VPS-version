"""
Prompt 模板管理服务（Phase1 新增）

方案 9.4：Prompt 是业务配置与可审计资产，不是代码字符串。支持：
- 模板 CRUD（draft/active/archived 状态）
- 每次发布生成不可变版本快照（prompt_versions）
- 历史版本回滚（生成新版本而非修改旧记录）
- 白名单变量 schema 校验
"""
import datetime
import json
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.database import PromptTemplate, PromptVersion
from app.models.prompts import (
    TEMPLATE_STATUS_DRAFT,
    TEMPLATE_STATUS_ACTIVE,
    TEMPLATE_STATUS_ARCHIVED,
    FREEDOM_L0, FREEDOM_L1, FREEDOM_L2, FREEDOM_L3,
    ALL_FREEDOM_LEVELS,
)
from app.services.prompt_renderer import (
    VariableSchemaError,
    normalize_schema,
    validate_template_variables,
    default_template_docs,
)


class PromptError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


# ── 序列化辅助 ─────────────────────────────────────────────────────

def template_to_dict(t: PromptTemplate) -> Dict[str, Any]:
    return {
        "id": t.id,
        "name": t.name,
        "purpose": t.purpose,
        "language": t.language,
        "customer_segment": t.customer_segment,
        "product_name": t.product_name,
        "sender_company": t.sender_company,
        "sender_signature": t.sender_signature,
        "system_prompt": t.system_prompt,
        "user_prompt_template": t.user_prompt_template,
        "variables_schema": _parse_json(t.variables_schema_json) or {},
        "model_config": _parse_json(t.model_config_json) or {},
        "freedom_level": t.freedom_level,
        "status": t.status,
        "current_version": t.current_version,
        "created_by_user_id": t.created_by_user_id,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


def version_to_dict(v: PromptVersion) -> Dict[str, Any]:
    return {
        "id": v.id,
        "template_id": v.template_id,
        "version": v.version,
        "system_prompt": v.system_prompt,
        "user_prompt_template": v.user_prompt_template,
        "variables_schema": _parse_json(v.variables_schema_json) or {},
        "model_config": _parse_json(v.model_config_json) or {},
        "freedom_level": v.freedom_level,
        "change_summary": v.change_summary,
        "created_by_user_id": v.created_by_user_id,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }


def _parse_json(raw: Optional[str]) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


# ── 校验 ───────────────────────────────────────────────────────────

def validate_freedom_level(level: str) -> str:
    level = (level or "").strip().upper()
    if level not in ALL_FREEDOM_LEVELS:
        raise PromptError(f"freedom_level 必须是 {', '.join(ALL_FREEDOM_LEVELS)} 之一")
    return level


def _validate_template_content(
    system_prompt: str,
    user_prompt_template: str,
    variables_schema: Any,
) -> None:
    if not system_prompt or not system_prompt.strip():
        raise PromptError("system_prompt 不能为空")
    if not user_prompt_template or not user_prompt_template.strip():
        raise PromptError("user_prompt_template 不能为空")
    try:
        undeclared = validate_template_variables(user_prompt_template, variables_schema)
    except VariableSchemaError as e:
        raise PromptError(str(e))
    if undeclared:
        raise PromptError(
            f"模板引用了未声明变量: {', '.join(undeclared)}。"
            "请先补充 variables_schema 白名单（防 Prompt 注入）。"
        )


def _dump_json(data: Any) -> Optional[str]:
    if data is None:
        return None
    if isinstance(data, str):
        return data
    return json.dumps(data, ensure_ascii=False)


# ── 模板 CRUD ──────────────────────────────────────────────────────

def list_templates(db: Session, status: Optional[str] = None, user_id: Optional[int] = None) -> List[Dict]:
    q = db.query(PromptTemplate)
    if status:
        q = q.filter(PromptTemplate.status == status)
    if user_id:
        # 非管理员场景：管理员可见全部；普通用户过滤归属。此处 user_id 表示「仅看自己的」
        q = q.filter(PromptTemplate.created_by_user_id == user_id)
    rows = q.order_by(PromptTemplate.updated_at.desc()).all()
    return [template_to_dict(r) for r in rows]


def get_template(db: Session, template_id: int) -> PromptTemplate:
    t = db.query(PromptTemplate).filter(PromptTemplate.id == template_id).first()
    if t is None:
        raise PromptError("Prompt 模板不存在", status_code=404)
    return t


def get_active_template(db: Session, template_id: Optional[int] = None) -> Optional[PromptTemplate]:
    """取指定 id 的模板（若传），否则取最新发布的一个 active 模板"""
    if template_id:
        return get_template(db, template_id)
    return (
        db.query(PromptTemplate)
        .filter(PromptTemplate.status == TEMPLATE_STATUS_ACTIVE)
        .order_by(PromptTemplate.updated_at.desc())
        .first()
    )


def create_template(
    db: Session,
    *,
    name: str,
    purpose: str,
    language: str,
    system_prompt: str,
    user_prompt_template: str,
    variables_schema: Any = None,
    model_config: Any = None,
    freedom_level: str = FREEDOM_L1,
    customer_segment: Optional[str] = None,
    product_name: Optional[str] = None,
    sender_company: Optional[str] = None,
    sender_signature: Optional[str] = None,
    created_by_user_id: Optional[int] = None,
) -> Dict:
    name = (name or "").strip()
    if not name:
        raise PromptError("模板名称不能为空")
    if db.query(PromptTemplate).filter(PromptTemplate.name == name).first():
        raise PromptError(f"模板名称已存在: {name}")

    freedom = validate_freedom_level(freedom_level)
    # 未提供 schema 时：宽松采用默认白名单，但校验通过才允许
    schema = variables_schema if variables_schema is not None else default_variables_schema()
    _validate_template_content(system_prompt, user_prompt_template, schema)

    t = PromptTemplate(
        name=name,
        purpose=purpose or "first_contact",
        language=language or "auto",
        customer_segment=customer_segment,
        product_name=product_name,
        sender_company=sender_company,
        sender_signature=sender_signature,
        system_prompt=system_prompt,
        user_prompt_template=user_prompt_template,
        variables_schema_json=_dump_json(schema),
        model_config_json=_dump_json(model_config),
        freedom_level=freedom,
        status=TEMPLATE_STATUS_DRAFT,
        current_version=0,
        created_by_user_id=created_by_user_id,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return template_to_dict(t)


def update_template(
    db: Session,
    template_id: int,
    *,
    name: Optional[str] = None,
    purpose: Optional[str] = None,
    language: Optional[str] = None,
    customer_segment: Optional[str] = None,
    product_name: Optional[str] = None,
    sender_company: Optional[str] = None,
    sender_signature: Optional[str] = None,
    system_prompt: Optional[str] = None,
    user_prompt_template: Optional[str] = None,
    variables_schema: Any = None,
    model_config: Any = None,
    freedom_level: Optional[str] = None,
    status: Optional[str] = None,
    updated_by_user_id: Optional[int] = None,
) -> Dict:
    """更新 draft 模板。active/archived 模板不允许直接改（须走「新建草稿版本→发布」）。"""
    t = get_template(db, template_id)
    if t.status == TEMPLATE_STATUS_ACTIVE:
        raise PromptError("已发布的模板禁止直接编辑：请先复制为草稿或通过「基于当前版本新建草稿」修改")

    if name is not None and (name or "").strip() != t.name:
        new_name = (name or "").strip()
        dup = db.query(PromptTemplate).filter(
            PromptTemplate.name == new_name, PromptTemplate.id != template_id
        ).first()
        if dup:
            raise PromptError(f"模板名称已存在: {new_name}")
        t.name = new_name

    if purpose is not None:
        t.purpose = purpose
    if language is not None:
        t.language = language
    if customer_segment is not None:
        t.customer_segment = customer_segment or None
    if product_name is not None:
        t.product_name = product_name or None
    if sender_company is not None:
        t.sender_company = sender_company or None
    if sender_signature is not None:
        t.sender_signature = sender_signature or None
    if freedom_level is not None:
        t.freedom_level = validate_freedom_level(freedom_level)
    if model_config is not None:
        t.model_config_json = _dump_json(model_config)
    if status is not None:
        if status not in (TEMPLATE_STATUS_DRAFT, TEMPLATE_STATUS_ACTIVE, TEMPLATE_STATUS_ARCHIVED):
            raise PromptError("非法 status")
        t.status = status

    # 内容变更：先校验（schema 用新值或原值）
    if system_prompt is not None or user_prompt_template is not None:
        new_sys = system_prompt if system_prompt is not None else t.system_prompt
        new_user = user_prompt_template if user_prompt_template is not None else t.user_prompt_template
        schema = variables_schema if variables_schema is not None else _parse_json(t.variables_schema_json)
        _validate_template_content(new_sys, new_user, schema)
        t.system_prompt = new_sys
        t.user_prompt_template = new_user
    if variables_schema is not None:
        # 变量 schema 变更也必须重新校验模板
        _validate_template_content(t.system_prompt, t.user_prompt_template, variables_schema)
        t.variables_schema_json = _dump_json(variables_schema)

    t.updated_at = datetime.datetime.utcnow()
    db.commit()
    db.refresh(t)
    return template_to_dict(t)


def archive_template(db: Session, template_id: int) -> Dict:
    t = get_template(db, template_id)
    t.status = TEMPLATE_STATUS_ARCHIVED
    db.commit()
    db.refresh(t)
    return template_to_dict(t)


def delete_template(db: Session, template_id: int) -> bool:
    """删除模板。限制：从未发布过且未产生 generation_runs 的草稿可删；
    有版本历史的模板仅归档。"""
    t = get_template(db, template_id)
    from app.database import GenerationRun
    used = db.query(GenerationRun).filter(GenerationRun.prompt_template_id == template_id).first()
    versions = list_versions(db, template_id)
    if used or versions:
        raise PromptError("模板已被生成记录或版本历史引用，不允许物理删除（请归档）")
    db.delete(t)
    db.commit()
    return True


# ── 版本管理 ───────────────────────────────────────────────────────

def list_versions(db: Session, template_id: int) -> List[Dict]:
    rows = (
        db.query(PromptVersion)
        .filter(PromptVersion.template_id == template_id)
        .order_by(PromptVersion.version.desc())
        .all()
    )
    return [version_to_dict(r) for r in rows]


def get_version(db: Session, version_id: int) -> PromptVersion:
    v = db.query(PromptVersion).filter(PromptVersion.id == version_id).first()
    if v is None:
        raise PromptError("Prompt 版本不存在", status_code=404)
    return v


def publish_version(
    db: Session,
    template_id: int,
    *,
    change_summary: Optional[str] = None,
    created_by_user_id: Optional[int] = None,
) -> Dict:
    """将模板当前内容发布为新版本，并把模板置为 active。

    历史版本永不修改/删除（方案 9.4：禁止直接修改已产生邮件的历史 Prompt 记录）。
    """
    t = get_template(db, template_id)
    schema = _parse_json(t.variables_schema_json)
    _validate_template_content(t.system_prompt, t.user_prompt_template, schema)

    next_version = t.current_version + 1
    v = PromptVersion(
        template_id=t.id,
        version=next_version,
        system_prompt=t.system_prompt,
        user_prompt_template=t.user_prompt_template,
        variables_schema_json=t.variables_schema_json,
        model_config_json=t.model_config_json,
        freedom_level=t.freedom_level,
        change_summary=change_summary,
        created_by_user_id=created_by_user_id,
    )
    db.add(v)
    t.current_version = next_version
    t.status = TEMPLATE_STATUS_ACTIVE
    t.updated_at = datetime.datetime.utcnow()
    # 允许多个 active 模板按 purpose/segment 并存（实验对比需要），
    # 生成入口默认取最新 active（或前端显式指定 template_id）。
    db.commit()
    db.refresh(t)
    return {"template": template_to_dict(t), "version": version_to_dict(v)}


def rollback_to_version(db: Session, template_id: int, version_id: int,
                        change_summary: Optional[str] = None,
                        created_by_user_id: Optional[int] = None) -> Dict:
    """回滚：把历史版本内容复制为模板当前内容并发布为新版本（历史不动）。"""
    t = get_template(db, template_id)
    v = get_version(db, version_id)
    if v.template_id != t.id:
        raise PromptError("版本不属于该模板")
    schema = _parse_json(v.variables_schema_json)
    _validate_template_content(v.system_prompt, v.user_prompt_template, schema)
    t.system_prompt = v.system_prompt
    t.user_prompt_template = v.user_prompt_template
    t.variables_schema_json = v.variables_schema_json
    t.model_config_json = v.model_config_json
    t.freedom_level = v.freedom_level
    t.updated_at = datetime.datetime.utcnow()
    db.commit()
    db.refresh(t)
    return publish_version(
        db, template_id,
        change_summary=change_summary or f"回滚到版本 {v.version}",
        created_by_user_id=created_by_user_id,
    )


def fork_from_version(db: Session, template_id: int, version_id: int,
                      new_name: str, created_by_user_id: Optional[int] = None) -> Dict:
    """基于历史版本复制为新模板草稿（用于改造已发布模板）。"""
    t = get_template(db, template_id)
    v = get_version(db, version_id)
    if v.template_id != t.id:
        raise PromptError("版本不属于该模板")
    new_name = (new_name or "").strip() or (t.name + "（副本）")
    if db.query(PromptTemplate).filter(PromptTemplate.name == new_name).first():
        raise PromptError(f"模板名称已存在: {new_name}")
    nt = PromptTemplate(
        name=new_name,
        purpose=t.purpose,
        language=t.language,
        customer_segment=t.customer_segment,
        product_name=t.product_name,
        sender_company=t.sender_company,
        sender_signature=t.sender_signature,
        system_prompt=v.system_prompt,
        user_prompt_template=v.user_prompt_template,
        variables_schema_json=v.variables_schema_json,
        model_config_json=v.model_config_json,
        freedom_level=v.freedom_level,
        status=TEMPLATE_STATUS_DRAFT,
        current_version=0,
        created_by_user_id=created_by_user_id,
    )
    db.add(nt)
    db.commit()
    db.refresh(nt)
    return template_to_dict(nt)


def default_variables_schema() -> Dict:
    """默认白名单 schema（与模板文档一致）"""
    return {
        var["var"]: {"type": var["type"], "required": False, "description": var["desc"]}
        for var in default_template_docs()
        if not var["var"].startswith("system.")
    }


# ── 种子模板 ───────────────────────────────────────────────────────

DEFAULT_SYSTEM_PROMPT = (
    "你是一位专业的外贸开发信（cold email）撰写专家，擅长针对 B2B 海外买家撰写高回复率的开发信。\n"
    "你必须遵守以下规则：\n"
    "1. 只使用【目标客户事实】与【产品信息】中提供的内容，不得虚构客户不存在的项目、需求或数据；\n"
    "2. 禁止给出具体价格、交期、认证或库存承诺；如需提及只能说明「欢迎询价/可提供规格书」；\n"
    "3. 不得冒用发件人之外的品牌或声称任何官方授权；\n"
    "4. 语气专业、简洁、有温度，面向真实采购/技术负责人；\n"
    "5. 正文末尾保留自然的回复引导（CTA），不要删除客户退订或停止联系的选项；\n"
    "6. 输出必须是严格的 JSON（不要 markdown 代码块、不要额外文字）。"
)

DEFAULT_USER_TEMPLATE = """请用 {{customer.language}} 为下面的潜在买家撰写一封专业的外贸开发信。

【我方产品/卖点】
产品名：{{system.product_name}}
产品关键词/要点：{{system.product_keywords}}
补充：{{product.key_features}}

【目标客户事实】（只能引用以下信息，不得虚构）
公司名称：{{customer.company_name}}
国家：{{customer.country}}
官网：{{customer.website}}
公司类型：{{customer.company_type}}
买家意向评分：{{customer.buyer_intent_score}}
AI 官网摘要：{{customer.ai_summary}}
开发切入点：{{customer.sales_hook}}
潜在需求：{{customer.needs_identified}}
匹配产品：{{customer.product_match}}
建议联系职位：{{customer.target_position}}

【发件信息】
公司：{{system.sender_company}}
签名：{{system.sender_signature}}
本次行动号召：{{system.call_to_action}}

【撰写要求】
1. 主题行（subject）简洁有力，包含核心产品方向，能引起对方打开兴趣；
2. 正文结合【目标客户事实】做到个性化，不要泛泛而谈；经销商/贸易商侧重供货稳定与 OEM/ODM，
   终端/工程公司侧重方案与交期服务；
3. 篇幅 120-220 词（非英文语言按同等信息量）；
4. 不得虚构价格/交期/认证/项目数据；只能使用上面给出的事实。

返回严格 JSON：
{
  "subject": "主题",
  "body": "正文（多行，用 \\n）",
  "language": "语言代码",
  "tone": "professional/friendly 等",
  "facts_used": ["引用的客户事实列表"],
  "claims_requiring_review": ["需人工核实或有风险的说法，无则空数组"],
  "call_to_action": "期望客户回复的动作",
  "risk_flags": ["风险提示，无则空数组"],
  "needs_human_review": true
}"""


def ensure_seed_templates(db: Session) -> None:
    """幂等创建内置默认模板（首次部署自动生成，用户可编辑/归档）。

    找不到任何模板时创建一份 L1「通用首封开发信」。
    """
    from app.models.prompts import FREEDOM_L1
    count = db.query(PromptTemplate).count()
    if count > 0:
        return
    try:
        create_template(
            db,
            name="通用首封开发信（默认 L1）",
            purpose="first_contact",
            language="auto",
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            user_prompt_template=DEFAULT_USER_TEMPLATE,
            variables_schema=default_variables_schema(),
            model_config={"temperature": 0.7, "max_tokens": 2048},
            freedom_level=FREEDOM_L1,
            customer_segment="通用（EPC/工程商/经销商/终端均可）",
        )
        db.commit()
    except PromptError:
        db.rollback()

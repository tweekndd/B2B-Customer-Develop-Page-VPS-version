"""
Prompt 管理 API（Phase1 新增）

方案 9.4 Prompt 编辑页面应支持：新建/复制、编辑、预览变量、测试生成、
对比版本、发布 active、回滚历史、查看使用记录。
"""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import require_admin, require_user
from app.database import Customer, get_db
from app.services import prompt_service as ps

router = APIRouter(tags=["prompts"])

# 管理员才能编辑 Prompt（模板是业务资产，避免普通用户篡改发信话术）
# 查看权限所有登录用户；写操作 require_admin。
_EDIT_PERMISSION = require_admin


def _err(e: ps.PromptError):
    return HTTPException(status_code=e.status_code, detail=str(e))


# ── 模板 CRUD ─────────────────────────────────────────────────────

@router.get("/prompts")
def list_prompts(
    status: Optional[str] = Query(None, description="draft/active/archived"),
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    try:
        rows = ps.list_templates(db, status=status)
    except ps.PromptError as e:
        raise _err(e)
    return {"templates": rows, "count": len(rows)}


@router.get("/prompts/{template_id}")
def get_prompt(template_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    try:
        t = ps.get_template(db, template_id)
    except ps.PromptError as e:
        raise _err(e)
    versions = ps.list_versions(db, template_id)
    return {"template": ps.template_to_dict(t), "versions": versions}


@router.post("/prompts")
def create_prompt(
    body: dict,
    db: Session = Depends(get_db),
    user=Depends(_EDIT_PERMISSION),
):
    """新建 Prompt 草稿。

    body: {name, purpose, language, system_prompt, user_prompt_template,
           variables_schema?, model_config?, freedom_level?, customer_segment?,
           product_name?, sender_company?, sender_signature?}
    """
    try:
        t = ps.create_template(
            db,
            name=body.get("name", ""),
            purpose=body.get("purpose", "first_contact"),
            language=body.get("language", "auto"),
            system_prompt=body.get("system_prompt", ""),
            user_prompt_template=body.get("user_prompt_template", ""),
            variables_schema=body.get("variables_schema"),
            model_config=body.get("model_config"),
            freedom_level=body.get("freedom_level", "L1"),
            customer_segment=body.get("customer_segment"),
            product_name=body.get("product_name"),
            sender_company=body.get("sender_company"),
            sender_signature=body.get("sender_signature"),
            created_by_user_id=user.id,
        )
    except ps.PromptError as e:
        raise _err(e)
    return {"message": "Prompt 模板已创建", "template": t}


@router.put("/prompts/{template_id}")
def update_prompt(
    template_id: int,
    body: dict,
    db: Session = Depends(get_db),
    user=Depends(_EDIT_PERMISSION),
):
    try:
        t = ps.update_template(
            db,
            template_id,
            name=body.get("name"),
            purpose=body.get("purpose"),
            language=body.get("language"),
            customer_segment=body.get("customer_segment"),
            product_name=body.get("product_name"),
            sender_company=body.get("sender_company"),
            sender_signature=body.get("sender_signature"),
            system_prompt=body.get("system_prompt"),
            user_prompt_template=body.get("user_prompt_template"),
            variables_schema=body.get("variables_schema"),
            model_config=body.get("model_config"),
            freedom_level=body.get("freedom_level"),
            status=body.get("status"),
            updated_by_user_id=user.id,
        )
    except ps.PromptError as e:
        raise _err(e)
    return {"message": "Prompt 模板已更新", "template": t}


@router.post("/prompts/{template_id}/publish")
def publish_prompt(
    template_id: int,
    body: dict = None,
    db: Session = Depends(get_db),
    user=Depends(_EDIT_PERMISSION),
):
    """发布为新版本并置 active（同库同时只有一个 active）"""
    body = body or {}
    try:
        result = ps.publish_version(
            db, template_id,
            change_summary=body.get("change_summary"),
            created_by_user_id=user.id,
        )
    except ps.PromptError as e:
        raise _err(e)
    return {"message": "已发布", **result}


@router.post("/prompts/{template_id}/rollback/{version_id}")
def rollback_prompt(
    template_id: int,
    version_id: int,
    body: dict = None,
    db: Session = Depends(get_db),
    user=Depends(_EDIT_PERMISSION),
):
    body = body or {}
    try:
        result = ps.rollback_to_version(
            db, template_id, version_id,
            change_summary=body.get("change_summary"),
            created_by_user_id=user.id,
        )
    except ps.PromptError as e:
        raise _err(e)
    return {"message": "已回滚并发布新版本", **result}


@router.post("/prompts/{template_id}/fork/{version_id}")
def fork_prompt(
    template_id: int,
    version_id: int,
    body: dict = None,
    db: Session = Depends(get_db),
    user=Depends(_EDIT_PERMISSION),
):
    """基于历史版本复制为新模板草稿"""
    body = body or {}
    try:
        t = ps.fork_from_version(
            db, template_id, version_id,
            new_name=body.get("new_name", ""),
            created_by_user_id=user.id,
        )
    except ps.PromptError as e:
        raise _err(e)
    return {"message": "已复制为新草稿模板", "template": t}


@router.post("/prompts/{template_id}/archive")
def archive_prompt(template_id: int, db: Session = Depends(get_db),
                   user=Depends(_EDIT_PERMISSION)):
    try:
        t = ps.archive_template(db, template_id)
    except ps.PromptError as e:
        raise _err(e)
    return {"message": "已归档", "template": t}


@router.delete("/prompts/{template_id}")
def delete_prompt(template_id: int, db: Session = Depends(get_db),
                  user=Depends(_EDIT_PERMISSION)):
    try:
        ps.delete_template(db, template_id)
    except ps.PromptError as e:
        raise _err(e)
    return {"message": "已删除"}


# ── 版本列表 ──────────────────────────────────────────────────────

@router.get("/prompts/{template_id}/versions")
def list_prompt_versions(template_id: int, db: Session = Depends(get_db),
                         user=Depends(require_user)):
    try:
        ps.get_template(db, template_id)
    except ps.PromptError as e:
        raise _err(e)
    return {"versions": ps.list_versions(db, template_id)}


# ── 变量文档 ──────────────────────────────────────────────────────

@router.get("/prompts/meta/variables")
def prompt_variables_doc(user=Depends(require_user)):
    """返回可插入变量说明（前端模板编辑器使用）"""
    from app.services.prompt_renderer import default_template_docs
    return {"variables": default_template_docs()}


# ── 预览（不调用 LLM） ────────────────────────────────────────────

@router.post("/prompts/preview")
def preview_prompt(
    body: dict,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """预览变量替换后的完整提示词（测试渲染，不消耗 AI）。

    body: {system_prompt, user_prompt_template, variables_schema, variables: {...}}
    """
    from app.services import prompt_renderer as pr
    variables = body.get("variables", {}) or {}
    schema = body.get("variables_schema")
    try:
        rendered = pr.render_template(
            body.get("user_prompt_template", ""), variables, schema, strict=True)
    except pr.VariableSchemaError as e:
        raise HTTPException(status_code=400, detail=str(e))
    undeclared = pr.validate_template_variables(body.get("user_prompt_template", ""), schema)
    return {
        "system_prompt": body.get("system_prompt", ""),
        "rendered_user_prompt": rendered,
        "undeclared_variables": undeclared,
        "hash": pr.render_hash(rendered),
    }

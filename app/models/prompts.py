"""Prompt 管理模型（Phase1 邮件外联基础新增）

按总体开发方案 9.4「开发信 Prompt 管理」：
- prompt_templates — 业务级模板资产（名称/用途/语言/状态/自由度）
- prompt_versions — 模板的历史版本（不可变快照，用于溯源与回滚）
- generation_runs    — 每次 AI 生成的可审计记录（哪个 Prompt/版本/客户事实/模型/输出）
"""
import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint

from app.core.database import Base

# 状态常量
TEMPLATE_STATUS_DRAFT = "draft"
TEMPLATE_STATUS_ACTIVE = "active"
TEMPLATE_STATUS_ARCHIVED = "archived"

# AI 自由度分级（方案 9.5）
FREEDOM_L0 = "L0"  # 严格模板：只填充白名单变量
FREEDOM_L1 = "L1"  # 受控改写：默认生产
FREEDOM_L2 = "L2"  # 个性化创作：高评分客户
FREEDOM_L3 = "L3"  # 代理式实验：默认不开放
ALL_FREEDOM_LEVELS = (FREEDOM_L0, FREEDOM_L1, FREEDOM_L2, FREEDOM_L3)


class PromptTemplate(Base):
    """Prompt 模板（业务资产，当前激活版本见 prompt_versions）

    一份模板可以产生多份邮件草稿；每份草稿必须能溯源到
    prompt_template.id + prompt_version.id。
    """
    __tablename__ = "prompt_templates"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(200), nullable=False, unique=True, index=True, comment="Prompt 名称，例如「德国 EPC 首封开发信」")
    purpose = Column(String(50), nullable=False, default="first_contact",
                     comment="适用场景: first_contact/followup/rfq/catalog 等")
    language = Column(String(20), nullable=False, default="auto",
                      comment="固定语言（en/de/es...）或 auto 按客户国家")
    customer_segment = Column(String(200), nullable=True, comment="适用客户细分描述（可空=全部）")
    # 产品/发件人上下文
    product_name = Column(String(200), nullable=True, comment="适用产品名（可空）")
    sender_company = Column(String(200), nullable=True, comment="发件公司名（可空，回退系统设置）")
    sender_signature = Column(Text, nullable=True, comment="默认签名（可空）")

    system_prompt = Column(Text, nullable=False, comment="系统 Prompt：角色/边界/输出规则")
    user_prompt_template = Column(Text, nullable=False, comment="用户模板：{{白名单变量}} 渲染")
    variables_schema_json = Column(Text, nullable=True,
                                   comment="白名单变量 Schema（JSON: {var: {type, required, description}}）")
    model_config_json = Column(Text, nullable=True,
                               comment="模型配置（JSON: {model, temperature, max_tokens, fallback}）")
    freedom_level = Column(String(4), nullable=False, default=FREEDOM_L1,
                           comment="AI 自由度等级 L0/L1/L2/L3（方案 9.5）")

    status = Column(String(20), nullable=False, default=TEMPLATE_STATUS_DRAFT,
                    comment="draft/active/archived")
    current_version = Column(Integer, nullable=False, default=0, comment="当前激活的版本号")
    created_by_user_id = Column(Integer, nullable=True, comment="创建人（users.id）")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.datetime.utcnow,
                        onupdate=datetime.datetime.utcnow, comment="更新时间")


class PromptVersion(Base):
    """Prompt 版本快照（不可变，历史记录禁止直接修改）

    版本内容由模板新建/发布时快照生成。产生过邮件的版本永不删除。
    """
    __tablename__ = "prompt_versions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    template_id = Column(Integer, ForeignKey("prompt_templates.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False, comment="版本号（从 1 递增）")
    system_prompt = Column(Text, nullable=False)
    user_prompt_template = Column(Text, nullable=False)
    variables_schema_json = Column(Text, nullable=True)
    model_config_json = Column(Text, nullable=True)
    freedom_level = Column(String(4), nullable=False, default=FREEDOM_L1)
    change_summary = Column(Text, nullable=True, comment="本次版本变更说明")
    created_by_user_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, comment="版本创建时间")

    __table_args__ = (
        UniqueConstraint("template_id", "version", name="uq_prompt_version"),
    )


class GenerationRun(Base):
    """AI 开发信生成运行记录（方案 9.4 generation_runs）

    回答「使用了哪个 Prompt、哪个版本、哪些客户事实、哪一个模型、
    为什么需要人工审核」。每一封可审的草稿都应关联一条运行记录。
    """
    __tablename__ = "generation_runs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True,
                         comment="目标客户（可空=批量/无客户上下文）")
    prompt_template_id = Column(Integer, ForeignKey("prompt_templates.id"), nullable=True, index=True)
    prompt_version_id = Column(Integer, ForeignKey("prompt_versions.id"), nullable=True, index=True)
    freedom_level = Column(String(4), nullable=False, default=FREEDOM_L1)

    # 模型与调用
    provider = Column(String(50), nullable=True, comment="LLM Provider 名")
    model = Column(String(100), nullable=True, comment="实际使用的模型")
    model_config_json = Column(Text, nullable=True, comment="本次生效模型配置快照")

    # 输入溯源
    input_snapshot_json = Column(Text, nullable=True, comment="客户事实快照（JSON，白名单变量求值结果）")
    rendered_prompt_hash = Column(String(64), nullable=True, comment="渲染后用户提示词 SHA256")
    raw_output_text = Column(Text, nullable=True, comment="LLM 原始输出文本")
    output_json = Column(Text, nullable=True, comment="结构化解析结果 JSON（subject/body/language/facts_used/...）")

    # 规则检查结果（generation_guard）
    checks_json = Column(Text, nullable=True, comment="规则检查结果 JSON: {check: {passed, message}}")
    risk_flags_json = Column(Text, nullable=True, comment="风险标记 JSON 数组")
    result_status = Column(String(20), nullable=False, default="success",
                           comment="success/parse_failed/rule_blocked/model_failed")
    error_message = Column(Text, nullable=True, comment="失败原因")

    # 人工编辑距离（评估用；0=未改动直接使用）
    human_edit_distance = Column(Integer, nullable=True, comment="相对原始输出的人工编辑字符距离")
    created_by_user_id = Column(Integer, nullable=True, comment="触发人（users.id）")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True, comment="运行时间")

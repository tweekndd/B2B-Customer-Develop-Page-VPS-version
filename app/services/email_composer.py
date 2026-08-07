"""
AI 外贸开发信生成服务（V4.6 新增）
参考项目：b2b-buyer-discovery 的西英双语开发信自动生成能力

核心特性：
1. 多语种自动检测 — 根据目标国家自动选择开发信语言（country_language_map 覆盖 130+ 国家）
2. 产品关键词驱动 — 由用户提供的产品关键词（或客户发现关键词）构建 prompt，
   让 AI 生成贴合具体产品的开发信（而非泛泛而谈）
3. 个性化 — 结合公司名、公司类型、官网摘要、开发切入点、产品匹配等信息
4. 统一 LLM 接口调用（app.llm.manager），自动 Fallback + 重试
"""
import json
import logging
from typing import Optional, Dict, Any, List

from app.llm.manager import get_llm_manager
from app.llm.utils import extract_json
from app.services.country_language_map import get_language_info

logger = logging.getLogger("email_composer")

SYSTEM_PROMPT = (
    "你是一位专业的外贸开发信（cold email）撰写专家，擅长针对 B2B 海外买家撰写高回复率的开发信。"
    "你的开发信必须：紧密结合客户的具体产品需求、突出我方产品优势、语气专业且有温度、结尾引导回复。"
    "只返回JSON格式数据。"
)

# 语言名称 → 开发信语言代码映射（用于前端展示与 prompt 提示）
_LANGUAGE_NAMES = {
    "English": "English",
    "Spanish": "Spanish",
    "Portuguese": "Portuguese",
    "French": "French",
    "German": "German",
    "Italian": "Italian",
    "Russian": "Russian",
    "Arabic": "Arabic",
    "Hindi": "Hindi",
    "Indonesian": "Indonesian",
    "Vietnamese": "Vietnamese",
    "Turkish": "Turkish",
    "Thai": "Thai",
    "Japanese": "Japanese",
    "Korean": "Korean",
    "Dutch": "Dutch",
    "Polish": "Polish",
    "Ukrainian": "Ukrainian",
    "Malay": "Malay",
    "Greek": "Greek",
    "Czech": "Czech",
    "Hungarian": "Hungarian",
    "Romanian": "Romanian",
    "Swedish": "Swedish",
    "Norwegian": "Norwegian",
    "Finnish": "Finnish",
    "Danish": "Danish",
    "Hebrew": "Hebrew",
    "Urdu": "Urdu",
    "Bengali": "Bengali",
    "Swahili": "Swahili",
    "Zulu": "Zulu",
    "Hausa": "Hausa",
    "Yoruba": "Yoruba",
    "Filipino": "Filipino",
    "Burmese": "Burmese",
    "Khmer": "Khmer",
    "Lao": "Lao",
    "Nepali": "Nepali",
    "Sinhala": "Sinhala",
    "Mongolian": "Mongolian",
    "Kazakh": "Kazakh",
    "Uzbek": "Uzbek",
    "Georgian": "Georgian",
    "Armenian": "Armenian",
    "Azerbaijani": "Azerbaijani",
    "Tajik": "Tajik",
    "Kyrgyz": "Kyrgyz",
    "Turkmen": "Turkmen",
    "Persian": "Persian",
    "Pashto": "Pashto",
}


def detect_email_language(country: Optional[str]) -> Dict[str, str]:
    """根据目标国家自动检测开发信语言

    Returns: {"language": "Spanish", "code": "es"}
    """
    if not country:
        return {"language": "English", "code": "en"}

    lang_info = get_language_info(country)
    if not lang_info or not lang_info.get("language"):
        return {"language": "English", "code": "en"}

    language = lang_info["language"]
    # 语言代码：hl 参数即语言代码（es/pt/fr/de/...）
    return {"language": language, "code": lang_info.get("hl", "en")}


def _build_prompt(
    company_name: str,
    country: str,
    product_keywords: List[str],
    company_type: str = "",
    ai_summary: str = "",
    sales_hook: str = "",
    target_position: str = "",
    identified_projects: str = "",
    language: str = "English",
    needs_identified: Optional[List[str]] = None,
    product_match: str = "",
    extra_context: str = "",
) -> str:
    """构建开发信生成提示词，产品关键词为核心驱动"""
    products = "、".join([k for k in product_keywords if k]) or "我们的主打产品"
    needs = "、".join(needs_identified) if needs_identified else ""
    project_info = identified_projects or ""

    ctx_lines = [f"公司名称：{company_name}"]
    if country:
        ctx_lines.append(f"所在国家：{country}")
    if company_type:
        ctx_lines.append(f"公司类型：{company_type}")
    if product_match:
        ctx_lines.append(f"对方可能需要的产品：{product_match}")
    if needs:
        ctx_lines.append(f"对方潜在需求：{needs}")
    if ai_summary:
        ctx_lines.append(f"公司简介：{ai_summary}")
    if sales_hook:
        ctx_lines.append(f"开发切入点：{sales_hook}")
    if project_info:
        ctx_lines.append(f"项目信息：{project_info}")
    if target_position:
        ctx_lines.append(f"建议联系职位：{target_position}")
    if extra_context:
        ctx_lines.append(f"补充信息：{extra_context}")
    ctx_text = "\n".join(ctx_lines)

    prompt = f"""请用{language}为下面的潜在买家撰写一封专业的外贸开发信（cold email）。

【我们提供的产品/关键词】——这是开发信的核心，必须紧密围绕这些产品展开：
{products}

【目标客户信息】
{ctx_text}

【撰写要求】
1. 使用{language}撰写，语气专业、有温度，不要生硬推销
2. 主题行（subject）简洁有力，包含核心产品关键词，能引起对方打开兴趣
3. 正文必须结合【产品关键词】和【目标客户信息】，做到个性化，不要泛泛而谈
4. 突出我方产品的优势（可从产品关键词推导，如型号、参数、交期、认证等，不要虚构不存在的具体数据）
5. 不要编造未提供的具体数据（如具体价格），如需数字用占位说明
6. 如果客户是经销商/分销商/贸易商，侧重强调「供货稳定、支持 OEM/ODM、长期合作」；
   如果客户是终端用户/工程公司，侧重强调「解决方案、交期、售后支持」
7. 篇幅控制在 150-250 词（非英文语言按同等信息量）
8. 结尾引导对方回复（如询价/索取资料/安排会议）

返回严格JSON格式：
{{
    "subject": "邮件主题",
    "body": "邮件正文（多行文本，使用 \\n 换行）",
    "language": "{language}"
}}

只返回JSON，不要包含其他文字。"""

    return prompt


async def generate_email_draft(
    company_name: str,
    country: str,
    product_keywords: Optional[List[str]] = None,
    company_type: str = "",
    ai_summary: str = "",
    sales_hook: str = "",
    target_position: str = "",
    identified_projects: str = "",
    needs_identified: Optional[List[str]] = None,
    product_match: str = "",
    language: str = "",
    user_id: Optional[int] = None,
    extra_context: str = "",
) -> Optional[Dict[str, Any]]:
    """生成开发信草稿

    Args:
        company_name: 客户公司名
        country: 客户国家（用于语言自动检测）
        product_keywords: 产品关键词列表（开发信核心驱动）
        language: 指定语言；为空则根据 country 自动检测
        user_id: 按用户使用其 API Key（Round 3）

    Returns:
        {"subject": str, "body": str, "language": str} 或 None（失败）
    """
    # 语言自动检测
    if not language or language in ("auto", "Auto", "自动"):
        lang = detect_email_language(country)
    else:
        lang = {"language": language, "code": "auto"}

    keywords = [k.strip() for k in (product_keywords or []) if k and k.strip()]
    if not keywords:
        keywords = [product_match] if product_match else []

    prompt = _build_prompt(
        company_name=company_name,
        country=country,
        product_keywords=keywords,
        company_type=company_type,
        ai_summary=ai_summary,
        sales_hook=sales_hook,
        target_position=target_position,
        identified_projects=identified_projects,
        language=lang["language"],
        needs_identified=needs_identified,
        product_match=product_match,
        extra_context=extra_context,
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    result = await get_llm_manager().chat(
        messages,
        user_id=user_id,
        temperature=0.7,
        max_tokens=2048,
    )
    if result is None:
        logger.warning("开发信生成失败（所有模型均失败或未配置 Key）")
        return None

    parsed = extract_json(result.content if result.content else "")
    if not isinstance(parsed, dict):
        logger.warning("开发信生成结果 JSON 解析失败: %s", (result.content or "")[:200])
        return None

    draft = {
        "subject": str(parsed.get("subject", "")).strip(),
        "body": str(parsed.get("body", "")).strip(),
        "language": parsed.get("language") or lang["language"],
    }
    if not draft["subject"] and not draft["body"]:
        logger.warning("开发信内容为空")
        return None
    return draft


def load_email_draft(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """安全解析客户记录的开发信草稿（JSON）"""
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and ("subject" in data or "body" in data):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    return None

"""
AI关键词扩展服务（V6.5 — 全行业通用）

支持两种模式：
1. 空关键词模式：当基础关键词为空时，返回一组通用 B2B 采购方角色搜索词
   （distributor / importer / wholesaler / trading company 等），适用于任何行业。
2. AI 扩展模式：给定具体关键词时，调用统一 LLM 接口扩展为 10~20 个相关关键词，
   并按目标国家语言本地化（翻译 + 扩展一次完成）。不绑定任何特定行业。
"""
import logging
from typing import Optional, List

from app.llm.manager import get_llm_manager
from app.llm.utils import extract_json
from app.services.country_language_map import get_language_info

logger = logging.getLogger("keyword_expander")

# 基础关键词为空时的通用采购方角色搜索词（行业无关）
_DEFAULT_BUYER_ROLE_QUERIES = [
    "distributor",
    "importer",
    "wholesaler",
    "trading company",
    "dealer",
    "procurement company",
]


async def expand_keywords(
    base_keyword: str,
    country: str = "",
    user_id: Optional[int] = None,
) -> Optional[List[str]]:
    """
    扩展关键词。返回关键词列表；全部失败时返回 [base_keyword]（兼容旧逻辑）。

    - 基础关键词为空 → 返回通用采购方角色搜索词（适用于任何行业）
    - 其他关键词 → 走 AI 扩展（行业无关）
    """
    base_keyword = (base_keyword or "").strip()

    # ── 空关键词：退回通用采购方角色搜索词 ──
    if not base_keyword:
        logger.info("关键词为空，使用通用采购方角色搜索词")
        return list(_DEFAULT_BUYER_ROLE_QUERIES)

    # 获取目标国家的语言信息
    lang_info = get_language_info(country) if country else None

    if lang_info and lang_info["language"] != "English":
        # ── 多语言模式：用目标国家的语言扩展关键词 ──
        language_name = lang_info["language"]
        prompt = f"""请根据用户输入的关键词和指定的目标国家，完成以下任务：

1. 将关键词翻译成{language_name}
2. 扩展出10~20个与翻译后关键词相关的{language_name}搜索词

这些{language_name}关键词将用于在Google搜索{country}的潜在 B2B 客户
（经销商、进口商、批发商、贸易公司、代理商、终端采购方、工程承包商、系统集成商，
或该关键词所属行业的其它采购方类型）。

用户输入的关键词（英文）：{base_keyword}
目标国家：{country}
目标语言：{language_name}

要求：
1. 所有关键词必须用{language_name}书写
2. 扩展10~20个相关关键词
3. 每个关键词应涵盖不同角度（不同客户类型、不同应用场景）
4. 确保关键词是{country}本地企业在Google搜索时会使用的自然词汇
5. 优先组合「客户类型」与「产品/行业」的采购导向词，避免只产生资讯/媒体/招聘结果
6. 返回JSON数组格式

返回格式：
["keyword1", "keyword2", "keyword3", ...]

只返回JSON数组，不要包含其他文字。"""

        system_prompt = f"你是一个专业的B2B营销关键词扩展专家，精通{language_name}。返回严格的JSON数组格式。"
    else:
        # ── 英文模式 ──
        country_hint = f"\n这些关键词将用于在Google搜索{country}的潜在 B2B 客户，请确保关键词符合{country}本地市场特点。" if country else ""
        prompt = f"""请根据用户输入的关键词，扩展出10~20个相关的Google搜索关键词。
这些关键词将用于搜索潜在 B2B 客户（经销商、进口商、批发商、贸易公司、代理商、
终端采购方、工程承包商、系统集成商，或该关键词所属行业的其它采购方类型）。

用户输入的关键词：{base_keyword}{country_hint}

要求：
1. 扩展10~20个相关关键词
2. 每个关键词应是与原词相关的不同搜索词
3. 包含不同角度（不同客户类型、不同应用场景）
4. 优先组合「客户类型」与「产品/行业」的采购导向词，避免只产生资讯/媒体/招聘/电商零售等无关结果的泛词
5. 返回JSON数组格式

返回格式：
["keyword1", "keyword2", "keyword3", ...]

只返回JSON数组，不要包含其他文字。"""

        system_prompt = "你是一个专业的B2B营销关键词扩展专家。返回严格的JSON数组格式。"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    result = await get_llm_manager().chat(
        messages,
        user_id=user_id,
        temperature=0.7,
        max_tokens=4096,
    )
    if result is None:
        logger.warning("关键词扩展调用失败（所有模型均失败或未配置 Key），回退到基础关键词")
        return [base_keyword]

    keywords = _parse_keyword_list(result.content)
    if keywords:
        return keywords

    logger.warning("关键词扩展结果解析失败: %s", result.content[:200])
    return [base_keyword]


def _parse_keyword_list(content: str) -> Optional[List[str]]:
    """解析AI返回的关键词列表"""
    parsed = extract_json(content)
    if isinstance(parsed, list) and len(parsed) > 0:
        # 去重并限制数量
        unique = []
        seen = set()
        for kw in parsed:
            if not isinstance(kw, str):
                continue
            kw_lower = kw.strip().lower()
            if kw_lower not in seen and kw_lower:
                seen.add(kw_lower)
                unique.append(kw.strip())
        return unique[:20]
    return None

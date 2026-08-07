"""
AI关键词扩展服务（V5.0 重构）
支持多语言扩展：根据目标国家自动将关键词翻译并扩展为当地语言
通过统一 LLM 接口（app.llm.manager）调用，自动 Fallback
"""
import logging
from typing import Optional, List

from app.llm.manager import get_llm_manager
from app.llm.utils import extract_json
from app.services.country_language_map import get_language_info

logger = logging.getLogger("keyword_expander")


async def expand_keywords(
    base_keyword: str,
    country: str = "",
    user_id: Optional[int] = None,
) -> Optional[List[str]]:
    """
    调用统一 LLM 接口将基础关键词扩展为10~20个相关关键词
    如果指定了国家，会使用该国家的本地语言进行扩展（翻译+扩展一次完成）
    用于Google搜索发现客户
    返回关键词列表；全部失败时返回 [base_keyword]（兼容旧逻辑）
    """
    # 获取目标国家的语言信息
    lang_info = get_language_info(country) if country else None

    if lang_info and lang_info["language"] != "English":
        # ── 多语言模式：用目标国家的语言扩展关键词 ──
        language_name = lang_info["language"]
        prompt = f"""请根据用户输入的行业关键词和指定的目标国家，完成以下任务：

1. 将关键词翻译成{language_name}
2. 扩展出10~20个与翻译后关键词相关的{language_name}搜索词

这些{language_name}关键词将用于在Google搜索{country}的潜在客户。

用户输入的关键词（英文）：{base_keyword}
目标国家：{country}
目标语言：{language_name}

要求：
1. 所有关键词必须用{language_name}书写
2. 扩展10~20个相关关键词
3. 每个关键词应涵盖不同角度（如不同业务类型、不同应用场景）
4. 确保关键词是{country}本地企业在Google搜索时会使用的自然词汇
5. 返回JSON数组格式

返回格式：
["keyword1", "keyword2", "keyword3", ...]

只返回JSON数组，不要包含其他文字。"""

        system_prompt = f"你是一个专业的B2B营销关键词扩展专家，精通{language_name}和外贸行业术语。返回严格的JSON数组格式。"
    else:
        # ── 英文模式（原逻辑，增加国家限制提示） ──
        country_hint = f"\n这些关键词将用于在Google搜索{country}的潜在客户，请确保关键词符合{country}本地市场特点。" if country else ""
        prompt = f"""请根据用户输入的行业关键词，扩展出10~20个相关的搜索关键词。
这些关键词将用于在Google搜索潜在客户。

用户输入的关键词：{base_keyword}{country_hint}

要求：
1. 扩展10~20个相关关键词
2. 每个关键词应是与原词相关的不同搜索词
3. 包含不同角度（如不同业务类型、不同应用场景）
4. 返回JSON数组格式

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
        logger.warning("关键词扩展调用失败（所有模型均失败或未配置 Key），返回原始关键词")
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

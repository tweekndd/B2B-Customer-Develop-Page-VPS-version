"""
AI关键词扩展服务（V6.0 — Robotics）

支持两种模式：
1. 矩阵模式：当基础关键词为空或为机器人行业哨兵值（robotics / __robotics__ 等）时，
   直接由 Robotics Keyword Generator 按 Country × Customer Type × Application 生成多个 Query。
2. AI 扩展模式：给定具体关键词时，调用统一 LLM 接口扩展为 10~20 个相关关键词，
   并按目标国家语言本地化（翻译 + 扩展一次完成），同时注入 Robotics 行业上下文。
"""
import logging
from typing import Optional, List

from app.llm.manager import get_llm_manager
from app.llm.utils import extract_json
from app.services.country_language_map import get_language_info
from app.services.robotics_keyword_generator import generate_robotics_queries

logger = logging.getLogger("keyword_expander")

# 机器人行业矩阵模式哨兵（用户在搜索框输入这些词即触发矩阵查询生成）
_MATRIX_SENTINELS = {
    "", "robotics", "robot", "robotics matrix", "__robotics__", "__matrix__", "机器人",
}


async def expand_keywords(
    base_keyword: str,
    country: str = "",
    user_id: Optional[int] = None,
) -> Optional[List[str]]:
    """
    扩展关键词。返回关键词列表；全部失败时返回 [base_keyword]（兼容旧逻辑）。

    机器人行业默认行为：
      - 基础关键词为空或为哨兵值 → 返回 Country × Customer Type × Application 组合 Query
      - 其他关键词 → 走 AI 扩展（含 Robotics 行业上下文）
    """
    base_keyword = (base_keyword or "").strip()

    # ── 矩阵模式：生成多个 Robotics 组合 Query ──
    if base_keyword.lower() in _MATRIX_SENTINELS:
        queries = generate_robotics_queries(country, max_queries=20) if country else []
        if queries:
            logger.info("Robotics 矩阵模式：为 %s 生成 %d 个 Query", country, len(queries))
            return queries
        if not base_keyword:
            return [base_keyword] if base_keyword else ["robotics"]

    # 获取目标国家的语言信息
    lang_info = get_language_info(country) if country else None

    if lang_info and lang_info["language"] != "English":
        # ── 多语言模式：用目标国家的语言扩展关键词 ──
        language_name = lang_info["language"]
        prompt = f"""请根据用户输入的机器人行业关键词和指定的目标国家，完成以下任务：

1. 将关键词翻译成{language_name}
2. 扩展出10~20个与翻译后关键词相关的{language_name}搜索词

这些{language_name}关键词将用于在Google搜索{country}的机器人/自动化行业潜在客户
（机器人系统集成商、自动化集成商、AMR/AGV 集成商、机器人经销商、机器人创业公司、仓储物流机器人、巡检机器人等）。

用户输入的关键词（英文）：{base_keyword}
目标国家：{country}
目标语言：{language_name}

要求：
1. 所有关键词必须用{language_name}书写
2. 扩展10~20个相关关键词
3. 每个关键词应涵盖不同角度（不同客户类型、不同机器人应用场景）
4. 确保关键词是{country}本地企业在Google搜索时会使用的自然词汇
5. 不要在关键词中聚焦传统工业机器人品牌（KUKA/FANUC/ABB/Yaskawa）
6. 返回JSON数组格式

返回格式：
["keyword1", "keyword2", "keyword3", ...]

只返回JSON数组，不要包含其他文字。"""

        system_prompt = f"你是一个专业的B2B机器人行业营销关键词扩展专家，精通{language_name}和Robotics/AMR/AGV术语。返回严格的JSON数组格式。"
    else:
        # ── 英文模式 ──
        country_hint = f"\n这些关键词将用于在Google搜索{country}的机器人/自动化行业潜在客户，请确保关键词符合{country}本地市场特点。" if country else ""
        prompt = f"""请根据用户输入的机器人行业关键词，扩展出10~20个相关的Google搜索关键词。
这些关键词将用于搜索潜在 B2B 客户（机器人系统集成商、自动化集成商、AMR/AGV 集成商、机器人经销商、
机器人创业公司、仓储物流机器人公司、巡检机器人公司、具身智能公司等）。

用户输入的关键词：{base_keyword}{country_hint}

要求：
1. 扩展10~20个相关关键词
2. 每个关键词应是与原词相关的不同搜索词
3. 包含不同角度（不同客户类型、不同机器人应用场景）
4. 避免产生传统工业机器人品牌（KUKA/FANUC/ABB/Yaskawa）大量结果的泛词
5. 返回JSON数组格式

返回格式：
["keyword1", "keyword2", "keyword3", ...]

只返回JSON数组，不要包含其他文字。"""

        system_prompt = "你是一个专业的B2B机器人行业营销关键词扩展专家。返回严格的JSON数组格式。"

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
        logger.warning("关键词扩展调用失败（所有模型均失败或未配置 Key），回退到 Robotics 矩阵 Query")
        fallback = generate_robotics_queries(country, max_queries=20) if country else []
        return fallback or [base_keyword]

    keywords = _parse_keyword_list(result.content)
    if keywords:
        return keywords

    logger.warning("关键词扩展结果解析失败: %s", result.content[:200])
    fallback = generate_robotics_queries(country, max_queries=20) if country else []
    return fallback or [base_keyword]


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

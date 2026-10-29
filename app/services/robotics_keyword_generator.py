"""
Robotics Google Search Keyword Generator（V1.0）

根据需求文档，用 Country × Customer Type × Application 组合生成多个 Google 搜索 Query，
避免只搜索 "robotic arm" / "industrial robot" 而产生大量传统工业机器人（KUKA/FANUC/ABB/Yaskawa）
和无关结果。

用法：
    from app.services.robotics_keyword_generator import generate_robotics_queries
    generate_robotics_queries("Singapore", max_queries=10)
    generate_robotics_queries("Thailand", customer_types=["AMR / AGV Integrator"])
"""
import json
import os
from functools import lru_cache
from typing import Dict, List, Optional

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "robotics_keywords.json")


@lru_cache(maxsize=1)
def _load_config() -> dict:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def invalidate_keyword_generator_cache():
    """清除关键词生成器配置缓存（配置更新后调用）"""
    _load_config.cache_clear()


def list_customer_types() -> List[str]:
    return list(_load_config().get("customer_types", {}).keys())


def list_supported_countries() -> List[str]:
    return list(_load_config().get("countries", {}).keys())


def get_locations(country: str) -> List[str]:
    """获取国家对应的搜索地区列表（如 Thailand → [Thailand, Bangkok, Chonburi, Rayong]）"""
    if not country:
        return []
    countries = _load_config().get("countries", {})
    if country in countries:
        return list(countries[country])
    # 大小写不敏感匹配
    for key, locations in countries.items():
        if key.lower() == country.lower():
            return list(locations)
    return [country]


def generate_robotics_queries(
    country: str,
    customer_types: Optional[List[str]] = None,
    max_queries: int = 50,
    include_site_queries: bool = True,
) -> List[str]:
    """生成 Robotics 行业组合搜索 Query（Customer Type × Application × Country）。

    Args:
        country: 目标国家（Singapore/Thailand/Malaysia）
        customer_types: 指定客户类型；不传则使用 default_customer_types
        max_queries: 最多返回多少条 Query
        include_site_queries: 是否包含 site:.edu.sg 等高校定向查询

    Returns:
        去重后的 Query 列表（保持稳定顺序）
    """
    config = _load_config()
    locations = get_locations(country) or [country]
    type_defs = config.get("customer_types", {})
    templates = config.get("query_templates", ["{phrase} {location}"])

    if customer_types:
        selected = [t for t in customer_types if t in type_defs]
    else:
        selected = config.get("default_customer_types") or list(type_defs.keys())

    queries: List[str] = []
    for ctype in selected:
        spec = type_defs.get(ctype, {})
        keywords = list(spec.get("keywords", []))
        # 国家特定关键词（如 Thailand 的 automotive robotics）
        country_specific = spec.get("country_keywords", {})
        for key, extra in country_specific.items():
            if key.lower() == country.lower():
                keywords.extend(extra)

        for phrase in keywords:
            for location in locations:
                # 只为第一个地区生成全部模板，其余地区用主模板，控制数量
                for tpl in templates[:2]:
                    queries.append(tpl.format(phrase=phrase, location=location))

        if include_site_queries:
            for site_country, site_queries in spec.get("site_queries", {}).items():
                if site_country.lower() == country.lower():
                    queries.extend(site_queries)

    # 去重（保序）并截断
    seen = set()
    unique: List[str] = []
    for q in queries:
        q = " ".join(q.split())
        key = q.lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(q)
        if len(unique) >= max_queries:
            break
    return unique


def get_keyword_matrix() -> Dict:
    """返回完整的关键词矩阵配置（供前端展示 / API 返回）"""
    config = _load_config()
    return {
        "countries": config.get("countries", {}),
        "customer_types": {
            name: spec.get("keywords", [])
            for name, spec in config.get("customer_types", {}).items()
        },
        "default_customer_types": config.get("default_customer_types", []),
    }

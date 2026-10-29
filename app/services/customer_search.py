"""客户模糊搜索

用于「客户列表」与「客户发现」页的搜索框：输入任意片段（如网址 / 邮箱 / 公司名
的一部分），只要记录中任一参与匹配的字段“含有”该片段即命中。

特性：
- 大小写不敏感（``ACME`` / ``acme`` / ``Aqka`` 均可命中）
- 子串匹配（不是前缀、也不要求整词相等）
- 多字段：公司名称 / 网址 / 邮箱，外加国家 / 城市 / 备注 / 发现关键词 / AI 摘要
- 多关键词：以空白分隔，需同时命中（AND），例如 ``acme tools``
- 通配符转义：用户输入 ``%`` ``_`` 不会当作 LIKE 通配符
"""
import re
from typing import List

from sqlalchemy import or_
from sqlalchemy.orm import Query

from app.database import Customer

# 参与模糊匹配的文本字段（名称 / 网址 / 邮箱 + 其它档案字段）
SEARCH_FIELDS = (
    "company_name",
    "website",
    "emails",
    "country",
    "city",
    "notes",
    "discovery_keyword",
    "ai_summary",
)


def _escape_like(token: str) -> str:
    """转义 LIKE 通配符，避免用户输入的 % / _ 被解释为通配符。"""
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def split_search_terms(keyword: str) -> List[str]:
    """按空白拆分搜索关键词（空输入返回空列表）。"""
    return [t for t in re.split(r"\s+", (keyword or "").strip()) if t]


def apply_customer_search(query: Query, keyword: str) -> Query:
    """对 ``Customer`` 查询套用模糊搜索，返回过滤后的查询。

    - 单个关键词：在 ``SEARCH_FIELDS`` 任一字段中“含有”即命中（OR）
    - 多个关键词（空格分隔）：每个关键词都需命中（AND）
    - 空关键词：原样返回，不做过滤
    """
    for token in split_search_terms(keyword):
        like = f"%{_escape_like(token)}%"
        query = query.filter(
            or_(
                *[getattr(Customer, name).ilike(like, escape="\\") for name in SEARCH_FIELDS]
            )
        )
    return query

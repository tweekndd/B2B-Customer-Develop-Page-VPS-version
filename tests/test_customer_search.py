"""客户模糊搜索服务单元测试

覆盖：名称/网址/邮箱及其它字段的片段匹配、大小写不敏感、多关键词 AND、
LIKE 通配符转义、空关键词不过滤。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, Customer
from app.services.customer_search import apply_customer_search, split_search_terms


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    session.add_all([
        Customer(
            company_name="Acme Tools Co., Ltd",
            website="https://www.acme-tools.example.com",
            emails='["sales@acme-tools.example.com"]',
            country="Turkey",
            city="Istanbul",
        ),
        Customer(
            company_name="Beijing Machinery",
            website="https://bj-mach.example.com",
            emails='["info@bj-mach.example.com"]',
            country="China",
            city="Beijing",
        ),
        Customer(
            company_name="Global Drill",
            website="https://www.drill-global.example.com",
            emails='["contact@drill-global.example.com"]',
            country="India",
        ),
    ])
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _names(db, keyword):
    return sorted(c.company_name for c in apply_customer_search(db.query(Customer), keyword).all())


def test_split_terms():
    assert split_search_terms("  acme   tools ") == ["acme", "tools"]
    assert split_search_terms("") == []
    assert split_search_terms(None) == []


def test_match_by_name_fragment(db):
    assert _names(db, "acme") == ["Acme Tools Co., Ltd"]


def test_case_insensitive(db):
    for q in ("ACME", "Acme", "acme"):
        assert _names(db, q) == ["Acme Tools Co., Ltd"]


def test_match_by_website_fragment(db):
    assert _names(db, "acme-tools.example.com") == ["Acme Tools Co., Ltd"]
    assert _names(db, "drill-global") == ["Global Drill"]


def test_match_by_email_fragment(db):
    assert _names(db, "drill-global.example.com") == ["Global Drill"]


def test_match_by_other_fields(db):
    assert _names(db, "istanbul") == ["Acme Tools Co., Ltd"]
    assert _names(db, "china") == ["Beijing Machinery"]


def test_multi_keyword_and(db):
    assert _names(db, "acme tools") == ["Acme Tools Co., Ltd"]
    # 两个词分别属于不同记录 → 交集为空
    assert _names(db, "acme china") == []


def test_wildcard_is_escaped(db):
    # 用户输入的 % / _ 不应被当作 SQL LIKE 通配符
    assert _names(db, "%") == []
    assert _names(db, "_") == []


def test_empty_keyword_returns_all(db):
    assert len(_names(db, "")) == 3
    assert len(_names(db, "   ")) == 3

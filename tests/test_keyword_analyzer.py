"""
测试：关键词分析 (keyword_analyzer) — 全行业通用域
覆盖正/负关键词命中、单词边界、多词关键词、边界条件
"""
import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.keyword_analyzer import analyze_keywords


class TestPositiveKeywords:
    """正向关键词"""

    def test_distributor_detected(self):
        pos, neg = analyze_keywords("We are a distributor of industrial equipment.")
        assert "distributor" in pos
        assert pos["distributor"] >= 1

    def test_multiple_positive_hits(self):
        pos, neg = analyze_keywords(
            "Distributor and importer. wholesaler. trading company. dealer."
        )
        assert "distributor" in pos
        assert "importer" in pos
        assert "wholesaler" in pos
        assert "trading company" in pos
        assert len(pos) >= 3

    def test_no_keywords(self):
        pos, neg = analyze_keywords("We are a small bakery in town.")
        assert pos == {}

    def test_keyword_count_accuracy(self):
        pos, neg = analyze_keywords(
            "distributor is here. distributor again. and distributor third."
        )
        assert pos["distributor"] == 3


class TestNegativeKeywords:
    """负向关键词"""

    def test_job_career_detected(self):
        pos, neg = analyze_keywords("We have job vacancies. Careers at our company.")
        assert "job" in neg or "career" in neg

    def test_mixed_keywords(self):
        """同时存在正向和负向关键词"""
        pos, neg = analyze_keywords(
            "Distributor company. Current job vacancies."
        )
        assert "distributor" in pos
        assert "job" in neg


class TestWordBoundaries:
    """单词边界匹配"""

    def test_single_word_boundary(self):
        """'dealer' 不应匹配 'dealers'（单词边界）"""
        pos, neg = analyze_keywords("We work with many dealers.")
        assert "dealer" not in pos

    def test_substring_not_matched(self):
        """'import'（单词）不应匹配 'important'"""
        pos, neg = analyze_keywords("This is important.")
        assert "import" not in pos


class TestMultiWordKeywords:
    """多词关键词（子串匹配）"""

    def test_trading_company(self):
        pos, neg = analyze_keywords("We are a trading company.")
        assert "trading company" in pos

    def test_exclusive_agent(self):
        pos, neg = analyze_keywords("We are their exclusive agent.")
        assert "exclusive agent" in pos

    def test_import_export(self):
        pos, neg = analyze_keywords("We handle import export business.")
        assert "import export" in pos

    def test_wholesale(self):
        pos, neg = analyze_keywords("wholesale supply business")
        assert "wholesale" in pos


class TestEdgeCases:
    """边界条件"""

    def test_empty_text(self):
        pos, neg = analyze_keywords("")
        assert pos == {}
        assert neg == {}

    def test_none_text(self):
        pos, neg = analyze_keywords(None)
        assert pos == {}
        assert neg == {}

    def test_case_insensitivity(self):
        pos, neg = analyze_keywords("DISTRIBUTOR TRADING COMPANY")
        assert "distributor" in pos
        assert "trading company" in pos

    def test_keywords_sorted_by_count(self):
        """返回结果应按命中次数降序排列"""
        pos, neg = analyze_keywords(
            "distributor distributor distributor importer importer dealer"
        )
        keys = list(pos.keys())
        assert pos[keys[0]] >= pos[keys[-1]]  # 降序

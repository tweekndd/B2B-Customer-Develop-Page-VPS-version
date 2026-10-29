"""
测试：关键词分析 (keyword_analyzer) — Robotics 域
覆盖正/负关键词命中、单词边界、多词关键词、边界条件
"""
import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.keyword_analyzer import analyze_keywords


class TestPositiveKeywords:
    """正向关键词"""

    def test_amr_detected(self):
        pos, neg = analyze_keywords("We deploy AMR for warehouses.")
        assert "amr" in pos
        assert pos["amr"] >= 1

    def test_multiple_positive_hits(self):
        pos, neg = analyze_keywords(
            "AMR and AGV platforms. mobile robot systems. robotic arm integration."
        )
        assert "amr" in pos
        assert "agv" in pos
        assert "mobile robot" in pos
        assert "robotic arm" in pos
        assert len(pos) >= 3

    def test_no_keywords(self):
        pos, neg = analyze_keywords("We are a car dealer.")
        assert pos == {}

    def test_keyword_count_accuracy(self):
        pos, neg = analyze_keywords("amr is here. amr again. and amr third.")
        assert pos["amr"] == 3


class TestNegativeKeywords:
    """负向关键词"""

    def test_job_career_detected(self):
        pos, neg = analyze_keywords("We have job vacancies. Careers at our company.")
        assert "job" in neg or "career" in neg

    def test_mixed_keywords(self):
        """同时存在正向和负向关键词"""
        pos, neg = analyze_keywords(
            "AMR integration company. Current job vacancies."
        )
        assert "amr" in pos
        assert "job" in neg


class TestWordBoundaries:
    """单词边界匹配"""

    def test_single_word_boundary(self):
        """'ugv' 不应匹配 'ugvs'（单词边界）"""
        pos, neg = analyze_keywords("We operate many ugvs.")
        assert "ugv" not in pos

    def test_substring_not_matched(self):
        """'ros'（单词）不应匹配 'cross'"""
        pos, neg = analyze_keywords("We cross the road.")
        assert "ros" not in pos


class TestMultiWordKeywords:
    """多词关键词（子串匹配）"""

    def test_mobile_robot(self):
        pos, neg = analyze_keywords("We offer mobile robot services.")
        assert "mobile robot" in pos

    def test_robotic_arm(self):
        pos, neg = analyze_keywords("We manufacture robotic arm systems.")
        assert "robotic arm" in pos

    def test_warehouse_automation(self):
        pos, neg = analyze_keywords("warehouse automation project")
        assert "warehouse automation" in pos

    def test_autonomous_mobile_robot(self):
        pos, neg = analyze_keywords("autonomous mobile robot deployment")
        assert "autonomous mobile robot" in pos


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
        pos, neg = analyze_keywords("AMR MOBILE ROBOT PLATFORM")
        assert "amr" in pos
        assert "mobile robot" in pos

    def test_keywords_sorted_by_count(self):
        """返回结果应按命中次数降序排列"""
        pos, neg = analyze_keywords("amr amr amr agv agv ros")
        keys = list(pos.keys())
        assert pos[keys[0]] >= pos[keys[-1]]  # 降序

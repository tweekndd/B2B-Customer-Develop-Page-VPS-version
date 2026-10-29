"""
测试：Robotics 规则评分引擎 (scoring_engine V3.0)
覆盖 7 个新评分维度 + Lead Grade + 排除逻辑 + 旧维度兼容 + 边界条件
"""
import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.scoring_engine import (
    calculate_scores,
    _score_industry_match,
    _score_project_match,
    _score_company_type,
    _score_country,
    _score_contact,
    _score_product_fit,
    _score_application_fit,
    _score_technical_fit,
    _detect_negative_flags,
    _classify_lead,
)


class TestIndustryMatch:
    """行业匹配度（旧接口，满分30）"""

    def test_robotics_keywords_hit(self):
        """命中多个机器人关键词"""
        text = "We provide AMR and AGV solutions. Our mobile robot platform supports UGV and robotic arm integration."
        score, detail = _score_industry_match(text)
        assert score > 0
        assert score <= 30

    def test_no_keywords(self):
        """完全不命中关键词"""
        score, detail = _score_industry_match("We are a car manufacturer.")
        assert score == 0

    def test_empty_text(self):
        """空文本"""
        score, detail = _score_industry_match("")
        assert score == 0
        assert "无官网内容" in detail


class TestProjectMatch:
    """项目匹配度（旧接口，满分20）"""

    def test_has_project_and_content(self):
        """有项目案例且有机器人行业内容匹配"""
        text = "Check our completed projects. We specialize in AMR and warehouse robotics."
        score, detail = _score_project_match(text)
        assert score == 20  # has_project_base(8) + has_content_match(12)

    def test_has_project_only(self):
        """有项目但无机器人内容（加低相关度分）"""
        text = "Our projects include various construction sites."
        score, _ = _score_project_match(text)
        assert score == 12  # has_project_base(8) + low_relevance(4)

    def test_no_project(self):
        """无项目信息"""
        score, detail = _score_project_match("We sell products.")
        assert score == 0
        assert "未识别到项目信息" in detail

    def test_empty_text(self):
        """空文本"""
        score, detail = _score_project_match("")
        assert score == 0
        assert "无官网内容" in detail


class TestCompanyType:
    """客户类型（旧接口，满分15）"""

    def test_robotics_si(self):
        score, detail = _score_company_type("Robotics System Integrator")
        assert score == 15

    def test_automation_si(self):
        score, detail = _score_company_type("Automation System Integrator")
        assert score == 15

    def test_amr_integrator(self):
        score, detail = _score_company_type("AMR/AGV Integrator")
        assert score == 15

    def test_distributor(self):
        """机器人经销商 = 高价值渠道客户"""
        score, detail = _score_company_type("Robotics Distributor")
        assert score == 15

    def test_robotics_startup(self):
        score, detail = _score_company_type("Robotics Startup")
        assert score == 13

    def test_enterprise_end_user(self):
        score, detail = _score_company_type("Enterprise End User")
        assert score == 12

    def test_manufacturer(self):
        score, detail = _score_company_type("Manufacturer")
        assert score == 8

    def test_research_lab(self):
        """高校/科研不是负面客户，但区分对待"""
        score, detail = _score_company_type("University/Research Lab")
        assert score == 5

    def test_other(self):
        score, detail = _score_company_type("Other")
        assert score == 0

    def test_unknown(self):
        score, detail = _score_company_type("Some Unknown Type")
        assert score == 0

    def test_none(self):
        score, detail = _score_company_type(None)
        assert score == 0
        assert "未识别" in detail


class TestCountryScore:
    """Geographic Fit（满分5）"""

    def test_singapore(self):
        score, detail = _score_country("Singapore")
        assert score == 5

    def test_thailand(self):
        score, detail = _score_country("Thailand")
        assert score == 5

    def test_malaysia(self):
        score, detail = _score_country("Malaysia")
        assert score == 4

    def test_vietnam(self):
        score, detail = _score_country("Vietnam")
        assert score == 3

    def test_unknown_country(self):
        score, detail = _score_country("Atlantis")
        assert score == 1  # Other

    def test_none(self):
        score, detail = _score_country(None)
        assert score == 0
        assert "未指定国家" in detail


class TestContactScore:
    """联系方式（旧维度，满分10）"""

    def test_no_emails(self):
        score, detail = _score_contact([])
        assert score == 0

    def test_one_email(self):
        score, detail = _score_contact(["info@company.com"])
        assert score == 3

    def test_two_emails(self):
        score, detail = _score_contact(["info@c.com", "sales@c.com"])
        assert score == 5

    def test_three_emails(self):
        score, detail = _score_contact(["a@c.com", "b@c.com", "c@c.com"])
        assert score == 8

    def test_four_or_more(self):
        score, detail = _score_contact(["a@c.com", "b@c.com", "c@c.com", "d@c.com"])
        assert score == 10

    def test_none(self):
        score, detail = _score_contact(None)
        assert score == 0


class TestNewDimensions:
    """V3.0 7 维评分规则回退"""

    def test_product_fit_strong(self):
        score, _ = _score_product_fit(
            "We provide AMR and AGV mobile robot platforms for warehouses.", None
        )
        assert score >= 25

    def test_product_fit_none(self):
        score, _ = _score_product_fit("We are a small bakery in town.", None)
        assert score == 0

    def test_application_fit(self):
        score, _ = _score_application_fit(
            "Autonomous inspection and AMR deployment for factories.", None
        )
        assert score >= 16

    def test_technical_fit(self):
        score, _ = _score_technical_fit(
            "We use ROS2, SLAM, LiDAR, Python, C++ for autonomous navigation and computer vision.", None
        )
        assert score >= 8

    def test_ai_breakdown_overrides_rules(self):
        ai = {"score_breakdown": {"product_fit": 12, "application_fit": 7}}
        p, _ = _score_product_fit("totally unrelated text", ai)
        a, _ = _score_application_fit("totally unrelated text", ai)
        assert p == 12
        assert a == 7


class TestNegativeFlags:
    """排除逻辑"""

    def test_direct_competitor(self):
        flags, override = _detect_negative_flags(
            "We are a global leader like KUKA and FANUC in industrial robotics.", None, ""
        )
        assert override == "EXCLUDE"
        assert any("DIRECT_COMPETITOR" in f for f in flags)

    def test_existing_agilex_partner(self):
        flags, override = _detect_negative_flags(
            "We are an authorized AgileX distributor in Southeast Asia.", None, ""
        )
        assert override == "EXISTING_CHANNEL"
        assert any("EXISTING_PARTNER" in f for f in flags)

    def test_normal_company_no_flags(self):
        flags, override = _detect_negative_flags(
            "We integrate AMR solutions for warehouses.", None, "Robotics System Integrator"
        )
        assert override is None
        assert not any("DIRECT_COMPETITOR" in f for f in flags)

    def test_classify_lead_exclude(self):
        assert _classify_lead(10, None) == "EXCLUDE"
        assert _classify_lead(90, "EXCLUDE") == "EXCLUDE"
        assert _classify_lead(90, "EXISTING_CHANNEL") == "EXCLUDE"
        assert _classify_lead(90, None) == "A"

    def test_existing_channel_action(self):
        result = calculate_scores(
            website_text="We are an authorized AgileX distributor in Singapore.",
            positive_keywords={},
            company_type="Robotics Distributor",
            country="Singapore",
            emails=["info@c.com"],
        )
        assert result["lead_grade"] == "EXCLUDE"
        assert result["recommended_action"] == "CHANNEL_PARTNER"
        assert any("EXISTING_PARTNER" in f for f in result["negative_flags"])


class TestCalculateScores:
    """综合评分（V6.0）"""

    def test_full_score_scenario(self):
        """理想情况下高分"""
        result = calculate_scores(
            website_text=(
                "We are a robotics system integrator providing AMR and AGV mobile robot integration. "
                "Our current project includes warehouse automation deployment and autonomous inspection. "
                "We use ROS2, SLAM, LiDAR, Python for autonomous navigation."
            ),
            positive_keywords={"amr": 3, "agv": 2},
            company_type="Robotics System Integrator",
            country="Singapore",
            emails=["info@c.com", "sales@c.com", "project@c.com"],
        )
        assert result["total_score"] >= 50
        assert result["lead_grade"] in ("A", "B", "C", "D", "EXCLUDE")
        assert result["product_fit_score"] >= 0
        assert result["application_fit_score"] >= 0
        assert result["customer_type_score"] >= 0
        assert result["geographic_score"] >= 0

    def test_minimum_score(self):
        """最差情况（无国家）"""
        result = calculate_scores(
            website_text="",
            positive_keywords={},
            company_type=None,
            country=None,
            emails=[],
        )
        assert result["total_score"] == 0
        assert result["priority"] == "D"
        assert result["lead_grade"] == "EXCLUDE"

    def test_priority_a_with_ai_breakdown(self):
        """AI 给出满分 breakdown 时达到 A 级"""
        ai = {
            "score_breakdown": {
                "product_fit": 30, "application_fit": 20, "customer_type": 15,
                "commercial_probability": 15, "technical_fit": 10,
                "geographic_fit": 5, "credibility": 5,
            }
        }
        result = calculate_scores(
            website_text="robotics integration",
            positive_keywords={"amr": 1},
            company_type="Robotics System Integrator",
            country="Singapore",
            emails=["info@c.com"],
            ai_result=ai,
        )
        assert result["total_score"] == 100
        assert result["lead_grade"] == "A"
        assert result["priority"] == "A"

    def test_all_fields_present(self):
        """返回结果包含新旧所有字段"""
        result = calculate_scores(
            website_text="amr integration",
            positive_keywords={"amr": 1},
            company_type="Robotics Startup",
            country="Thailand",
            emails=["info@c.com"],
        )
        expected_keys = [
            # 新维度
            "product_fit_score", "product_fit_detail",
            "application_fit_score", "application_fit_detail",
            "customer_type_score", "customer_type_detail",
            "commercial_probability_score", "commercial_probability_detail",
            "technical_fit_score", "technical_fit_detail",
            "geographic_score", "geographic_detail",
            "credibility_score", "credibility_detail",
            "lead_grade", "recommended_action", "negative_flags",
            "recommended_products", "likely_use_cases", "evidence",
            "sales_angle", "confidence", "customer_type",
            # 旧字段兼容
            "industry_score", "industry_detail",
            "project_score", "project_detail",
            "company_type_score", "company_type_detail",
            "country_score", "country_detail",
            "contact_score", "contact_detail",
            "price_inquiry_score", "price_inquiry_detail",
            "buyer_intent_score",
            "total_score", "priority",
        ]
        for key in expected_keys:
            assert key in result, f"缺少字段: {key}"

    def test_price_inquiry_bonus(self):
        """价格询盘仍记录旧维度分，并小幅提升商业概率"""
        base = calculate_scores(
            website_text="amr integration",
            positive_keywords={"amr": 1},
            company_type="Robotics Startup",
            country="Singapore",
            emails=["info@c.com"],
        )
        with_bonus = calculate_scores(
            website_text="amr integration",
            positive_keywords={"amr": 1},
            company_type="Robotics Startup",
            country="Singapore",
            emails=["info@c.com"],
            is_price_inquiry=True,
        )
        assert base["price_inquiry_score"] == 0
        assert with_bonus["price_inquiry_score"] == 5
        assert with_bonus["total_score"] >= base["total_score"]

    def test_competitor_excluded(self):
        """直接竞争对手被排除"""
        result = calculate_scores(
            website_text="We are KUKA, a global automation company.",
            positive_keywords={},
            company_type="Manufacturer",
            country="Singapore",
            emails=["info@kuka.com"],
        )
        assert result["lead_grade"] == "EXCLUDE"
        assert result["recommended_action"] == "EXCLUDE"
        assert any("DIRECT_COMPETITOR" in f for f in result["negative_flags"])

    def test_buyer_intent_score_passthrough(self):
        """buyer_intent_score 透传为展示维度"""
        result = calculate_scores(
            website_text="amr integration",
            positive_keywords={"amr": 1},
            company_type="Robotics Distributor",
            country="Singapore",
            emails=["info@c.com"],
            buyer_intent_score=8,
        )
        assert result["buyer_intent_score"] == 8
        no_intent = calculate_scores(
            website_text="amr integration",
            positive_keywords={"amr": 1},
            company_type="Robotics Distributor",
            country="Singapore",
            emails=["info@c.com"],
        )
        assert result["total_score"] == no_intent["total_score"]

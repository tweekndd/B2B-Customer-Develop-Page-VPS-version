"""
测试：通用规则评分引擎 (scoring_engine V4.0)
覆盖 7 个评分维度 + Lead Grade + 可配置排除逻辑 + 旧维度兼容 + 边界条件
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.services.scoring_engine as scoring_engine
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
    """目标关键词匹配度（旧接口，满分30）"""

    def test_generic_buyer_keywords_hit(self):
        """命中多个通用采购方关键词"""
        text = "We are a distributor and importer of industrial equipment. We supply machinery worldwide."
        score, detail = _score_industry_match(text)
        assert score > 0
        assert score <= 30

    def test_no_keywords(self):
        """完全不命中关键词"""
        score, detail = _score_industry_match("We are a small bakery in town.")
        assert score == 0

    def test_empty_text(self):
        """空文本"""
        score, detail = _score_industry_match("")
        assert score == 0
        assert "无官网内容" in detail


class TestProjectMatch:
    """项目/案例匹配度（旧接口，满分20）"""

    def test_has_project_and_content(self):
        """有项目案例且内容匹配"""
        text = "Check our completed projects. We handle procurement and distribution."
        score, detail = _score_project_match(text)
        assert score == 20  # has_project_base(8) + has_content_match(12)

    def test_has_project_only(self):
        """有项目但无业务内容（加低相关度分）"""
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

    def test_distributor(self):
        """经销商 = 高价值渠道客户"""
        score, detail = _score_company_type("Distributor")
        assert score == 15

    def test_importer(self):
        score, detail = _score_company_type("Importer")
        assert score == 15

    def test_trading_company(self):
        score, detail = _score_company_type("Trading Company")
        assert score == 14

    def test_epc_contractor(self):
        score, detail = _score_company_type("EPC Contractor")
        assert score == 13

    def test_system_integrator(self):
        score, detail = _score_company_type("System Integrator")
        assert score == 13

    def test_enterprise_end_user(self):
        score, detail = _score_company_type("End User")
        assert score == 12

    def test_manufacturer(self):
        score, detail = _score_company_type("Manufacturer")
        assert score == 6

    def test_research_lab(self):
        """高校/科研不是负面客户，但区分对待"""
        score, detail = _score_company_type("University/Research Lab")
        assert score == 3

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
    """Geographic Fit（满分5，通用默认所有国家等权）"""

    def test_default_weight(self):
        score, detail = _score_country("Singapore")
        assert score == 3  # Other 默认 3

    def test_another_country(self):
        score, detail = _score_country("Germany")
        assert score == 3

    def test_unknown_country(self):
        score, detail = _score_country("Atlantis")
        assert score == 3  # Other

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
    """V4.0 7 维评分规则回退"""

    def test_product_fit_strong(self):
        score, _ = _score_product_fit(
            "We are a distributor and importer of industrial equipment. We supply machinery worldwide.",
            None,
        )
        assert score >= 25

    def test_product_fit_none(self):
        score, _ = _score_product_fit("We are a small bakery in town.", None)
        assert score == 0

    def test_application_fit(self):
        score, _ = _score_application_fit(
            "Turnkey project supply and tender bidding for equipment.", None
        )
        assert score >= 16

    def test_technical_fit(self):
        score, _ = _score_technical_fit(
            "ISO 9001 certified, OEM/ODM, quality control, after-sales service, export markets.",
            None,
        )
        assert score >= 8

    def test_ai_breakdown_overrides_rules(self):
        ai = {"score_breakdown": {"product_fit": 12, "application_fit": 7}}
        p, _ = _score_product_fit("totally unrelated text", ai)
        a, _ = _score_application_fit("totally unrelated text", ai)
        assert p == 12
        assert a == 7

    def test_custom_product_keywords_boost_score(self, monkeypatch):
        """配置 product_keywords 后，行业专属词可命中 Product Fit"""
        cfg = scoring_engine._load_config()
        cfg = dict(cfg)
        cfg["product_keywords"] = ["solar inverter", "pv module"]
        monkeypatch.setattr(scoring_engine, "_load_config", lambda: cfg)

        score, detail = _score_product_fit(
            "We are a distributor of solar inverter and pv module products.", None
        )
        assert score >= 25


class TestNegativeFlags:
    """排除逻辑（竞品/渠道名单可选配置，默认空）"""

    def test_competitor_when_configured(self, monkeypatch):
        cfg = dict(scoring_engine._load_config())
        cfg["direct_competitors"] = ["acme corp"]
        monkeypatch.setattr(scoring_engine, "_load_config", lambda: cfg)

        flags, override = _detect_negative_flags(
            "We are Acme Corp, a global manufacturer.", None, ""
        )
        assert override == "EXCLUDE"
        assert any("DIRECT_COMPETITOR" in f for f in flags)

    def test_existing_partner_when_configured(self, monkeypatch):
        cfg = dict(scoring_engine._load_config())
        cfg["existing_partner_signals"] = ["acme authorized distributor"]
        monkeypatch.setattr(scoring_engine, "_load_config", lambda: cfg)

        flags, override = _detect_negative_flags(
            "We are an authorized Acme authorized distributor in Asia.", None, ""
        )
        assert override == "EXISTING_CHANNEL"
        assert any("EXISTING_PARTNER" in f for f in flags)

    def test_default_config_no_competitor_flags(self):
        """默认配置下不臆断竞品/渠道"""
        flags, override = _detect_negative_flags(
            "We are a distributor of industrial equipment for factories.",
            None,
            "Distributor",
        )
        assert override is None
        assert not any("DIRECT_COMPETITOR" in f for f in flags)

    def test_classify_lead_exclude(self):
        assert _classify_lead(10, None) == "EXCLUDE"
        assert _classify_lead(90, "EXCLUDE") == "EXCLUDE"
        assert _classify_lead(90, "EXISTING_CHANNEL") == "EXCLUDE"
        assert _classify_lead(90, None) == "A"

    def test_existing_channel_action(self, monkeypatch):
        cfg = dict(scoring_engine._load_config())
        cfg["existing_partner_signals"] = ["acme authorized distributor"]
        monkeypatch.setattr(scoring_engine, "_load_config", lambda: cfg)

        result = calculate_scores(
            website_text="We are an authorized Acme authorized distributor in Asia.",
            positive_keywords={},
            company_type="Distributor",
            country="Singapore",
            emails=["info@c.com"],
        )
        assert result["lead_grade"] == "EXCLUDE"
        assert result["recommended_action"] == "CHANNEL_PARTNER"
        assert any("EXISTING_PARTNER" in f for f in result["negative_flags"])


class TestCalculateScores:
    """综合评分（V4.0）"""

    def test_full_score_scenario(self):
        """理想情况下高分"""
        result = calculate_scores(
            website_text=(
                "We are a distributor and importer of industrial equipment. "
                "Our completed projects include turnkey project supply and tender bidding. "
                "We hold ISO 9001 certification and quality control for after-sales service."
            ),
            positive_keywords={"distributor": 3, "importer": 2},
            company_type="Distributor",
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
            website_text="distributor of industrial equipment",
            positive_keywords={"distributor": 1},
            company_type="Distributor",
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
            website_text="distributor of equipment",
            positive_keywords={"distributor": 1},
            company_type="Trading Company",
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
            website_text="We are a trading company.",
            positive_keywords={"trading": 1},
            company_type="Trading Company",
            country="Singapore",
            emails=["info@c.com"],
        )
        with_bonus = calculate_scores(
            website_text="We are a trading company.",
            positive_keywords={"trading": 1},
            company_type="Trading Company",
            country="Singapore",
            emails=["info@c.com"],
            is_price_inquiry=True,
        )
        assert base["price_inquiry_score"] == 0
        assert with_bonus["price_inquiry_score"] == 5
        assert with_bonus["total_score"] >= base["total_score"]

    def test_competitor_excluded_when_configured(self, monkeypatch):
        """配置的竞争对手被排除"""
        cfg = dict(scoring_engine._load_config())
        cfg["direct_competitors"] = ["acme corp"]
        monkeypatch.setattr(scoring_engine, "_load_config", lambda: cfg)

        result = calculate_scores(
            website_text="We are Acme Corp, a global equipment manufacturer.",
            positive_keywords={},
            company_type="Manufacturer",
            country="Singapore",
            emails=["info@acme.com"],
        )
        assert result["lead_grade"] == "EXCLUDE"
        assert result["recommended_action"] == "EXCLUDE"
        assert any("DIRECT_COMPETITOR" in f for f in result["negative_flags"])

    def test_buyer_intent_score_passthrough(self):
        """buyer_intent_score 透传为展示维度"""
        result = calculate_scores(
            website_text="distributor of equipment",
            positive_keywords={"distributor": 1},
            company_type="Distributor",
            country="Singapore",
            emails=["info@c.com"],
            buyer_intent_score=8,
        )
        assert result["buyer_intent_score"] == 8
        no_intent = calculate_scores(
            website_text="distributor of equipment",
            positive_keywords={"distributor": 1},
            company_type="Distributor",
            country="Singapore",
            emails=["info@c.com"],
        )
        assert result["total_score"] == no_intent["total_score"]

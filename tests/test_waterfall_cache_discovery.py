"""
测试：瀑布式邮箱发现 & 缓存管理 & 搜索任务服务
P3-25: 补充 discovery/waterfall/cache 测试覆盖
"""
import sys
import os
import json
import datetime
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.waterfall_discovery import (
    _extract_domain,
    _make_email_entry,
    _merge_and_dedup,
    _score_and_sort,
    GENERIC_BLACKLIST,
)
from app.services.cache_manager import (
    _compute_hash,
    SEARCH_CACHE_EXPIRE_DAYS,
    WEBSITE_CACHE_EXPIRE_DAYS,
)
from app.services.search_task_service import (
    should_stop,
    request_task_stop,
    request_stop,
    reset_stop_flag,
    _task_stop_flags,
    _batch_stop_flag,
)


# ═══════════════════════════════════════════
# 瀑布式发现：域名提取
# ═══════════════════════════════════════════

class TestExtractDomain:
    """域名提取测试"""

    def test_simple_domain(self):
        assert _extract_domain("example.com") == "example.com"

    def test_with_https(self):
        assert _extract_domain("https://example.com") == "example.com"

    def test_with_http(self):
        assert _extract_domain("http://example.com") == "example.com"

    def test_with_path(self):
        assert _extract_domain("https://example.com/about/us") == "example.com"

    def test_with_query(self):
        assert _extract_domain("https://example.com/page?q=1") == "example.com"

    def test_with_www(self):
        assert _extract_domain("https://www.example.com") == "www.example.com"

    def test_empty_input(self):
        assert _extract_domain("") == ""
        assert _extract_domain(None) == ""

    def test_whitespace(self):
        assert _extract_domain("  https://example.com  ") == "example.com"

    def test_subdomain(self):
        assert _extract_domain("https://mail.example.com") == "mail.example.com"


# ═══════════════════════════════════════════
# 瀑布式发现：邮箱条目创建
# ═══════════════════════════════════════════

class TestMakeEmailEntry:
    """邮箱条目格式测试"""

    def test_basic_entry(self):
        entry = _make_email_entry(email="test@example.com", source="hunter")
        assert entry["email"] == "test@example.com"
        assert entry["source"] == "hunter"
        assert entry["score"] == 0
        assert entry["full_name"] == ""

    def test_with_names(self):
        entry = _make_email_entry(
            email="john@example.com",
            source="tomba",
            first_name="John",
            last_name="Doe",
            position="CEO",
        )
        assert entry["full_name"] == "John Doe"
        assert entry["position"] == "CEO"

    def test_all_fields(self):
        entry = _make_email_entry(
            email="jane@example.com",
            source="prospeo",
            first_name="Jane",
            last_name="Smith",
            position="CTO",
            department="Engineering",
            phone="+1234567890",
            linkedin="https://linkedin.com/in/janesmith",
            score=85,
            verification="valid",
        )
        assert entry["email"] == "jane@example.com"
        assert entry["department"] == "Engineering"
        assert entry["phone"] == "+1234567890"
        assert entry["verification"] == "valid"


# ═══════════════════════════════════════════
# 瀑布式发现：合并去重
# ═══════════════════════════════════════════

class TestMergeAndDedup:
    """多源结果合并去重测试"""

    def test_empty_input(self):
        assert _merge_and_dedup([]) == []

    def test_single_source(self):
        entries = [
            [_make_email_entry("a@test.com", "hunter"), _make_email_entry("b@test.com", "hunter")]
        ]
        result = _merge_and_dedup(entries)
        assert len(result) == 2

    def test_dedup_same_email(self):
        e1 = _make_email_entry("a@test.com", "hunter", score=50)
        e2 = _make_email_entry("a@test.com", "tomba", score=80)
        result = _merge_and_dedup([[e1], [e2]])
        assert len(result) == 1
        # tomba has higher priority than hunter
        assert result[0]["source"] == "tomba"

    def test_dedup_case_insensitive(self):
        e1 = _make_email_entry("A@Test.com", "hunter")
        e2 = _make_email_entry("a@test.com", "tomba")
        result = _merge_and_dedup([[e1], [e2]])
        assert len(result) == 1

    def test_priority_tomba_over_prospeo(self):
        e1 = _make_email_entry("a@test.com", "prospeo", score=90)
        e2 = _make_email_entry("a@test.com", "tomba", score=50)
        result = _merge_and_dedup([[e1], [e2]])
        assert result[0]["source"] == "tomba"

    def test_priority_prospeo_over_hunter(self):
        e1 = _make_email_entry("a@test.com", "hunter", score=90)
        e2 = _make_email_entry("a@test.com", "prospeo", score=50)
        result = _merge_and_dedup([[e1], [e2]])
        assert result[0]["source"] == "prospeo"

    def test_same_source_keeps_higher_score(self):
        e1 = _make_email_entry("a@test.com", "hunter", score=30)
        e2 = _make_email_entry("a@test.com", "hunter", score=80)
        result = _merge_and_dedup([[e1], [e2]])
        assert len(result) == 1
        assert result[0]["score"] == 80

    def test_different_emails_preserved(self):
        entries = [
            [_make_email_entry("a@test.com", "hunter"), _make_email_entry("b@test.com", "tomba")],
            [_make_email_entry("c@test.com", "prospeo")],
        ]
        result = _merge_and_dedup(entries)
        assert len(result) == 3

    def test_empty_email_skipped(self):
        e1 = _make_email_entry("", "hunter")
        e2 = _make_email_entry("a@test.com", "tomba")
        result = _merge_and_dedup([[e1], [e2]])
        assert len(result) == 1


# ═══════════════════════════════════════════
# 瀑布式发现：评分排序
# ═══════════════════════════════════════════

class TestScoreAndSort:
    """邮箱评分排序测试"""

    def test_empty_input(self):
        assert _score_and_sort([]) == []

    def test_source_weight_tomba_highest(self):
        entries = [
            _make_email_entry("a@test.com", "scraped"),
            _make_email_entry("b@test.com", "hunter"),
            _make_email_entry("c@test.com", "tomba"),
            _make_email_entry("d@test.com", "prospeo"),
        ]
        result = _score_and_sort(entries)
        sources = [e["source"] for e in result]
        assert sources[0] == "tomba"
        assert sources[1] == "prospeo"
        assert sources[2] == "hunter"
        assert sources[3] == "scraped"

    def test_position_ceo_bonus(self):
        e1 = _make_email_entry("a@test.com", "hunter", position="Sales Rep")
        e2 = _make_email_entry("b@test.com", "hunter", position="CEO")
        result = _score_and_sort([e1, e2])
        assert result[0]["email"] == "b@test.com"

    def test_position_director_bonus(self):
        e1 = _make_email_entry("a@test.com", "hunter", position="Intern")
        e2 = _make_email_entry("b@test.com", "hunter", position="Director of Sales")
        result = _score_and_sort([e1, e2])
        assert result[0]["email"] == "b@test.com"

    def test_sort_score_removed_from_output(self):
        entries = [_make_email_entry("a@test.com", "tomba")]
        result = _score_and_sort(entries)
        assert "_sort_score" not in result[0]

    def test_verification_bonus(self):
        e1 = _make_email_entry("a@test.com", "hunter", verification="unknown")
        e2 = _make_email_entry("b@test.com", "hunter", verification="valid")
        result = _score_and_sort([e1, e2])
        assert result[0]["email"] == "b@test.com"


# ═══════════════════════════════════════════
# 瀑布式发现：通用邮箱黑名单
# ═══════════════════════════════════════════

class TestGenericBlacklist:
    """通用邮箱黑名单测试"""

    def test_blacklist_contains_common_prefixes(self):
        assert "info" in GENERIC_BLACKLIST
        assert "support" in GENERIC_BLACKLIST
        assert "noreply" in GENERIC_BLACKLIST
        assert "admin" in GENERIC_BLACKLIST
        assert "postmaster" in GENERIC_BLACKLIST

    def test_blacklist_all_lowercase(self):
        for item in GENERIC_BLACKLIST:
            assert item == item.lower()


# ═══════════════════════════════════════════
# 缓存管理：内容哈希
# ═══════════════════════════════════════════

class TestCacheHash:
    """缓存内容哈希测试"""

    def test_same_content_same_hash(self):
        h1 = _compute_hash("hello world")
        h2 = _compute_hash("hello world")
        assert h1 == h2

    def test_different_content_different_hash(self):
        h1 = _compute_hash("hello")
        h2 = _compute_hash("world")
        assert h1 != h2

    def test_hash_is_sha256(self):
        h = _compute_hash("test")
        assert len(h) == 64  # SHA-256 hex digest length

    def test_empty_content(self):
        h = _compute_hash("")
        assert len(h) == 64


# ═══════════════════════════════════════════
# 缓存管理：TTL 常量
# ═══════════════════════════════════════════

class TestCacheTTL:
    """缓存 TTL 配置测试"""

    def test_search_cache_ttl(self):
        assert SEARCH_CACHE_EXPIRE_DAYS == 30

    def test_website_cache_ttl(self):
        assert WEBSITE_CACHE_EXPIRE_DAYS == 7


# ═══════════════════════════════════════════
# 搜索任务：停止控制
# ═══════════════════════════════════════════

class TestStopFlags:
    """任务停止控制测试"""

    def setup_method(self):
        reset_stop_flag()

    def test_no_stop_by_default(self):
        assert should_stop(1) is False
        assert should_stop() is False

    def test_task_stop_flag(self):
        request_task_stop(42)
        assert should_stop(42) is True
        assert should_stop(43) is False  # 其他任务不受影响

    def test_batch_stop_flag(self):
        request_stop()
        assert should_stop() is True
        assert should_stop(1) is True  # 批量停止影响所有任务

    def test_reset_clears_all(self):
        request_task_stop(1)
        request_stop()
        reset_stop_flag()
        assert should_stop(1) is False
        assert should_stop() is False

    def test_task_stop_doesnt_affect_batch(self):
        request_task_stop(1)
        assert should_stop() is False  # 批量标记未设置

    def test_multiple_task_stops(self):
        request_task_stop(1)
        request_task_stop(2)
        assert should_stop(1) is True
        assert should_stop(2) is True
        assert should_stop(3) is False


# ═══════════════════════════════════════════
# 搜索任务：任务日志
# ═══════════════════════════════════════════

class TestTaskLog:
    """任务日志追加测试"""

    def test_append_to_empty_log(self):
        from app.services.search_task_service import _append_task_log

        class MockTask:
            task_log = None

        task = MockTask()
        _append_task_log(task, "info", "test message")
        logs = json.loads(task.task_log)
        assert len(logs) == 1
        assert logs[0]["type"] == "info"
        assert logs[0]["msg"] == "test message"
        assert "time" in logs[0]

    def test_append_to_existing_log(self):
        from app.services.search_task_service import _append_task_log

        class MockTask:
            task_log = json.dumps([{"time": "10:00:00", "type": "info", "msg": "first"}])

        task = MockTask()
        _append_task_log(task, "success", "second message")
        logs = json.loads(task.task_log)
        assert len(logs) == 2
        assert logs[1]["msg"] == "second message"

    def test_log_truncates_long_messages(self):
        from app.services.search_task_service import _append_task_log

        class MockTask:
            task_log = None

        task = MockTask()
        long_msg = "x" * 300
        _append_task_log(task, "info", long_msg)
        logs = json.loads(task.task_log)
        assert len(logs[0]["msg"]) <= 200

    def test_corrupted_log_resets(self):
        from app.services.search_task_service import _append_task_log

        class MockTask:
            task_log = "not valid json"

        task = MockTask()
        _append_task_log(task, "info", "new message")
        logs = json.loads(task.task_log)
        assert len(logs) == 1
        assert logs[0]["msg"] == "new message"


# ═══════════════════════════════════════════
# 缓存管理：数据库操作（使用内存 SQLite）
# ═══════════════════════════════════════════

class TestCacheDBOperations:
    """缓存数据库操作测试（使用独立内存数据库）"""

    @pytest.fixture(autouse=True)
    def setup_db(self):
        """每个测试使用独立的内存数据库"""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.database import Base

        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        yield
        self.engine.dispose()

    def test_search_cache_save_and_get(self):
        from app.services.cache_manager import save_search_cache, get_search_cache
        db = self.Session()
        results = [
            {"website": "example.com", "title": "Example", "snippet": "Test snippet"},
            {"website": "test.com", "title": "Test", "snippet": "Another snippet"},
        ]
        save_search_cache(db, "test keyword", "USA", results)
        cached = get_search_cache(db, "test keyword", "USA")
        assert len(cached) == 2
        assert cached[0]["website"] == "example.com"
        db.close()

    def test_search_cache_expired(self):
        from app.services.cache_manager import save_search_cache, get_search_cache
        from app.database import SearchCache
        db = self.Session()
        # Save with old date
        old_date = datetime.datetime.utcnow() - datetime.timedelta(days=31)
        cache = SearchCache(
            keyword="old", country="USA", website="old.com",
            title="Old", created_at=old_date,
        )
        db.add(cache)
        db.commit()
        cached = get_search_cache(db, "old", "USA")
        assert len(cached) == 0  # Should be expired
        db.close()

    def test_search_cache_different_country(self):
        from app.services.cache_manager import save_search_cache, get_search_cache
        db = self.Session()
        save_search_cache(db, "keyword", "USA", [{"website": "a.com"}])
        cached = get_search_cache(db, "keyword", "Germany")
        assert len(cached) == 0  # Different country
        db.close()

    def test_website_cache_save_and_get(self):
        from app.services.cache_manager import save_website_cache, get_website_cache
        db = self.Session()
        save_website_cache(db, "example.com", "Hello world content")
        cached = get_website_cache(db, "example.com")
        assert cached is not None
        assert cached["content"] == "Hello world content"
        assert len(cached["content_hash"]) == 64  # SHA-256
        db.close()

    def test_website_cache_update(self):
        from app.services.cache_manager import save_website_cache, get_website_cache
        db = self.Session()
        save_website_cache(db, "example.com", "Version 1")
        save_website_cache(db, "example.com", "Version 2")
        cached = get_website_cache(db, "example.com")
        assert cached["content"] == "Version 2"  # Updated
        db.close()

    def test_website_cache_expired(self):
        from app.services.cache_manager import get_website_cache
        from app.database import WebsiteCache
        db = self.Session()
        old_date = datetime.datetime.utcnow() - datetime.timedelta(days=8)
        cache = WebsiteCache(
            website="old.com", content="old", content_hash="abc",
            last_crawled=old_date,
        )
        db.add(cache)
        db.commit()
        cached = get_website_cache(db, "old.com")
        assert cached is None  # Expired
        db.close()

    def test_analysis_cache_save_and_get(self):
        from app.services.cache_manager import save_analysis_cache, get_analysis_cache
        db = self.Session()
        ai_result = {
            "company_type": "Distributor",
            "summary": "A test company",
            "sales_hook": "Good opportunity",
            "target_position": "Procurement Manager",
        }
        save_analysis_cache(db, "example.com", "website content", ai_result)
        cached = get_analysis_cache(db, "example.com", "website content")
        assert cached is not None
        assert cached["company_type"] == "Distributor"
        assert cached["summary"] == "A test company"
        db.close()

    def test_analysis_cache_different_content(self):
        from app.services.cache_manager import save_analysis_cache, get_analysis_cache
        db = self.Session()
        save_analysis_cache(db, "example.com", "content v1", {"company_type": "A"})
        cached = get_analysis_cache(db, "example.com", "content v2")
        assert cached is None  # Different content hash
        db.close()

    def test_clean_expired_cache(self):
        from app.services.cache_manager import clean_expired_cache
        from app.database import SearchCache
        db = self.Session()
        # Add expired record
        old_date = datetime.datetime.utcnow() - datetime.timedelta(days=31)
        cache = SearchCache(
            keyword="old", country="USA", website="old.com",
            title="Old", created_at=old_date,
        )
        db.add(cache)
        db.commit()
        counts = clean_expired_cache(db)
        assert counts["search_cache"] == 1
        db.close()

"""
数据同步 API 路由
支持多设备间通过网盘/USB 导出导入客户数据
V3.2.6: 新增备份/恢复功能（网页端一键操作）
从 routes.py 拆分（V2.8 重构）
"""
import datetime
import json
import os
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db, Customer, SearchTask, SearchCache, WebsiteCache, AnalysisCache
from app.services.deduplication import find_existing_customer
from app.services.customer_email_service import upsert_customer_email

router = APIRouter(tags=["sync"])

# ─── 备份目录配置 ───
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKUP_DIR = PROJECT_ROOT / "backups"

# V5.3：导出瘦身参数
# standard 模式（默认）：缓存数据限量导出 + 排除可重建的官网原文（大幅减小文件）
# full 模式：完整导出全部缓存与官网原文（文件可达几十 MB）
CACHE_EXPORT_LIMIT = int(os.environ.get("SYNC_CACHE_EXPORT_LIMIT", "2000"))


def _ensure_backup_dir():
    """确保备份目录存在"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return BACKUP_DIR


def _get_db_path():
    """获取数据库文件路径"""
    # 默认数据库是 app/customers.db 或环境变量指定的其他路径
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if db_url and db_url.startswith("sqlite:///"):
        # 提取 SQLite 文件路径
        rel_path = db_url[10:]  # 去掉 "sqlite:///"
        return PROJECT_ROOT / rel_path
    return PROJECT_ROOT / "app" / "customers.db"


@router.get("/sync/export")
def export_all_data(
    mode: str = Query("standard", description="导出模式: standard(瘦身,默认)/full(完整含缓存原文)"),
    db: Session = Depends(get_db),
):
    """
    导出数据为 JSON（多设备同步）

    - standard（默认，V5.3 瘦身）：
      · website_cache 不导出官网原文 content（可重建数据，体积最大头）
      · 三类缓存各限量导出最近 N 条（默认 2000，可用 SYNC_CACHE_EXPORT_LIMIT 调整）
    - full：完整导出全部缓存与官网原文（文件较大，导入可能超时）
    """
    # 客户数据（V5.3 阶段3：standard 模式排除大字段 website_text/ai_raw_json，
    # 已由快照表（website_snapshots/analysis_runs）承接并单独导出）
    full_mode = mode == "full"
    customers = db.query(Customer).order_by(Customer.id).all()
    customers_data = []
    for c in customers:
        # V5.1：导出结构化邮箱记录
        email_records = []
        for r in c.email_records:
            email_records.append({
                "email": r.email,
                "source": r.source or "manual",
                "source_detail": r.source_detail,
                "verification": r.verification,
                "is_primary": bool(r.is_primary),
                "notes": r.notes,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })
        entry = {
            "id": c.id,
            "company_name": c.company_name,
            "website": c.website,
            "country": c.country,
            "discovery_source": c.discovery_source,
            "discovery_keyword": c.discovery_keyword,
            "first_found_at": c.first_found_at.isoformat() if c.first_found_at else None,
            "emails": c.emails,
            "customer_emails": email_records,
            "positive_keywords": c.positive_keywords,
            "negative_keywords": c.negative_keywords,
            "industry_score": c.industry_score,
            "project_score": c.project_score,
            "company_type_score": c.company_type_score,
            "country_score": c.country_score,
            "contact_score": c.contact_score,
            "total_score": c.total_score,
            "priority": c.priority,
            "company_type": c.company_type,
            "ai_summary": c.ai_summary,
            "sales_hook": c.sales_hook,
            "target_position": c.target_position,
            "identified_projects": c.identified_projects,
            "buyer_intent_score": c.buyer_intent_score,
            "is_price_inquiry": c.is_price_inquiry,
            "email_draft": c.email_draft,
            "status": c.status,
            "follow_up_date": c.follow_up_date.isoformat() if c.follow_up_date else None,
            "notes": c.notes,
            "last_email_sent_at": c.last_email_sent_at.isoformat() if c.last_email_sent_at else None,
            "scrape_status": c.scrape_status,
            "ai_status": c.ai_status,
            "fail_reason": c.fail_reason,
            "star_rating": c.star_rating,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "analyzed_at": c.analyzed_at.isoformat() if c.analyzed_at else None,
        }
        if full_mode:
            # full 模式才导出客户大字段（standard 模式从快照表承接，省体积）
            entry["website_text"] = c.website_text
            entry["ai_raw_json"] = c.ai_raw_json
        customers_data.append(entry)

    # 搜索任务
    tasks = db.query(SearchTask).order_by(SearchTask.id).all()
    tasks_data = []
    for t in tasks:
        tasks_data.append({
            "id": t.id,
            "country": t.country,
            "keyword": t.keyword,
            "expanded_keywords": t.expanded_keywords,
            "search_depth": t.search_depth,
            "status": t.status,
            "found_websites": t.found_websites,
            "analyzed_companies": t.analyzed_companies,
            "new_companies": t.new_companies,
            "current_keyword_index": t.current_keyword_index,
            "error_message": t.error_message,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "finished_at": t.finished_at.isoformat() if t.finished_at else None,
            "task_log": t.task_log,
        })

    # 缓存数据（提升同步后搜索效率；V5.3 standard 模式限量导出）
    full_mode = mode == "full"

    search_cache = db.query(SearchCache)
    if not full_mode:
        search_cache = search_cache.order_by(SearchCache.id.desc()).limit(CACHE_EXPORT_LIMIT)
    search_cache = search_cache.all()
    search_cache_data = []
    for s in search_cache:
        search_cache_data.append({
            "keyword": s.keyword,
            "country": s.country,
            "website": s.website,
            "title": s.title,
            "snippet": s.snippet,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        })

    website_cache = (
        db.query(WebsiteCache)
        .order_by(WebsiteCache.last_crawled.desc().nullslast())
        .limit(CACHE_EXPORT_LIMIT if not full_mode else None)
        .all()
    )
    website_cache_data = []
    for w in website_cache:
        entry = {
            "website": w.website,
            "content_hash": w.content_hash,
            "last_crawled": w.last_crawled.isoformat() if w.last_crawled else None,
        }
        if full_mode:
            # full 模式才导出官网原文（standard 模式可重建，省体积）
            entry["content"] = w.content
        website_cache_data.append(entry)

    analysis_cache = (
        db.query(AnalysisCache)
        .order_by(AnalysisCache.id.desc())
        .limit(CACHE_EXPORT_LIMIT if not full_mode else None)
        .all()
    )
    analysis_cache_data = []
    for a in analysis_cache:
        analysis_cache_data.append({
            "website": a.website,
            "content_hash": a.content_hash,
            "company_type": a.company_type,
            "summary": a.summary,
            "sales_hook": a.sales_hook,
            "target_position": a.target_position,
            "analysis_reason": a.analysis_reason,
            "identified_projects": a.identified_projects,
            "raw_json": a.raw_json,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        })

    # V5.3 阶段3：导出智能分析快照（客户大字段的承接表，standard 模式限量）
    from app.database import WebsiteSnapshot, AnalysisRun, ScoreSnapshot

    def _safe_dt(value):
        return value.isoformat() if value else None

    snapshots_data = []
    for s in (
        db.query(WebsiteSnapshot)
        .order_by(WebsiteSnapshot.id.desc())
        .limit(CACHE_EXPORT_LIMIT if not full_mode else None)
        .all()
    ):
        snapshots_data.append({
            "customer_id": s.customer_id,
            "website": s.website,
            "content": s.content if full_mode else None,  # standard 模式不含官网原文
            "content_hash": s.content_hash,
            "scrape_status": s.scrape_status,
            "source": s.source,
            "created_at": _safe_dt(s.created_at),
        })

    analysis_runs_data = []
    for r in (
        db.query(AnalysisRun)
        .order_by(AnalysisRun.id.desc())
        .limit(CACHE_EXPORT_LIMIT if not full_mode else None)
        .all()
    ):
        analysis_runs_data.append({
            "customer_id": r.customer_id,
            "website_snapshot_id": r.website_snapshot_id,
            "content_hash": r.content_hash,
            "provider": r.provider,
            "model": r.model,
            "status": r.status,
            "company_type": r.company_type,
            "summary": r.summary,
            "sales_hook": r.sales_hook,
            "target_position": r.target_position,
            "identified_projects": r.identified_projects,
            "analysis_reason": r.analysis_reason,
            "buyer_intent_score": r.buyer_intent_score,
            "is_price_inquiry": r.is_price_inquiry,
            "address_city": r.address_city,
            "needs_identified": r.needs_identified,
            "product_match": r.product_match,
            "raw_json": r.raw_json if full_mode else None,  # standard 模式不含原始 JSON
            "error_message": r.error_message,
            "created_at": _safe_dt(r.created_at),
        })

    score_snapshots_data = []
    for s in (
        db.query(ScoreSnapshot)
        .order_by(ScoreSnapshot.id.desc())
        .limit(CACHE_EXPORT_LIMIT if not full_mode else None)
        .all()
    ):
        score_snapshots_data.append({
            "customer_id": s.customer_id,
            "analysis_run_id": s.analysis_run_id,
            "industry_score": s.industry_score,
            "project_score": s.project_score,
            "company_type_score": s.company_type_score,
            "country_score": s.country_score,
            "contact_score": s.contact_score,
            "total_score": s.total_score,
            "priority": s.priority,
            "created_at": _safe_dt(s.created_at),
        })

    return {
        "exported_at": datetime.datetime.utcnow().isoformat(),
        "version": "2.9",
        "mode": mode,
        "stats": {
            "customers": len(customers_data),
            "search_tasks": len(tasks_data),
            "search_cache": len(search_cache_data),
            "website_cache": len(website_cache_data),
            "analysis_cache": len(analysis_cache_data),
            "website_snapshots": len(snapshots_data),
            "analysis_runs": len(analysis_runs_data),
            "score_snapshots": len(score_snapshots_data),
            "truncated_cache": not full_mode,
        },
        "data": {
            "customers": customers_data,
            "search_tasks": tasks_data,
            "search_cache": search_cache_data,
            "website_cache": website_cache_data,
            "analysis_cache": analysis_cache_data,
            "website_snapshots": snapshots_data,
            "analysis_runs": analysis_runs_data,
            "score_snapshots": score_snapshots_data,
        },
    }


def _merge_imported_emails(db: Session, customer: Customer, c_data: dict) -> int:
    """把导出数据中的结构化邮箱记录合并到 CustomerEmail 表（V5.1）"""
    merged = 0
    for e in c_data.get("customer_emails") or []:
        if not e or not e.get("email"):
            continue
        try:
            record = upsert_customer_email(
                db,
                customer.id,
                e["email"],
                source=e.get("source") or "legacy",
                source_detail=e.get("source_detail"),
                notes=e.get("notes"),
                is_primary=bool(e.get("is_primary")),
            )
            if e.get("verification"):
                record.verification = e["verification"]
            merged += 1
        except ValueError:
            continue
    return merged


@router.post("/sync/import")
def import_sync_data(
    data: dict,
    db: Session = Depends(get_db),
):
    """
    导入同步数据（JSON 格式，由 /sync/export 生成）
    自动去重：已存在的客户按域名/公司名合并，不重复创建
    """
    imported = data.get("data", {})
    if not imported:
        raise HTTPException(status_code=400, detail="数据为空")

    # ── 1. 导入 search_tasks（按业务键去重，V5.3 批量查重提速） ──
    task_count = 0
    existing_task_keys = {
        (r[0], r[1])
        for r in db.query(SearchTask.country, SearchTask.keyword).all()
    }
    for t_data in imported.get("search_tasks", []):
        key = (t_data.get("country", ""), t_data.get("keyword", ""))
        if key in existing_task_keys:
            continue
        existing_task_keys.add(key)
        task = SearchTask(
            country=t_data.get("country", ""),
            keyword=t_data.get("keyword", ""),
            expanded_keywords=t_data.get("expanded_keywords"),
            search_depth=t_data.get("search_depth", 50),
            status=t_data.get("status", "Pending"),
            found_websites=t_data.get("found_websites", 0),
            analyzed_companies=t_data.get("analyzed_companies", 0),
            new_companies=t_data.get("new_companies", 0),
            current_keyword_index=t_data.get("current_keyword_index", 0),
            error_message=t_data.get("error_message"),
            task_log=t_data.get("task_log"),
        )
        if t_data.get("created_at"):
            task.created_at = datetime.datetime.fromisoformat(t_data["created_at"])
        if t_data.get("finished_at"):
            task.finished_at = datetime.datetime.fromisoformat(t_data["finished_at"])
        db.add(task)
        task_count += 1

    # ── 2. 导入 customers（自动去重） ──
    cust_count = 0
    skip_count = 0
    customer_id_map = {}  # V5.3 阶段3：源 customer_id → 目标 customer_id（快照表导入用）
    for c_data in imported.get("customers", []):
        existing = find_existing_customer(db, c_data.get("website", ""), c_data.get("company_name", ""))
        if existing:
            skip_count += 1
            # V5.1：已存在客户仍合并导入的结构化邮箱（修复邮箱变更无法同步）
            _merge_imported_emails(db, existing, c_data)
            if c_data.get("id"):
                customer_id_map[c_data["id"]] = existing.id
            continue

        customer = Customer(
            company_name=c_data.get("company_name", ""),
            website=c_data.get("website", ""),
            country=c_data.get("country", ""),
            discovery_source=c_data.get("discovery_source"),
            discovery_keyword=c_data.get("discovery_keyword"),
            emails=c_data.get("emails"),
            website_text=c_data.get("website_text"),
            positive_keywords=c_data.get("positive_keywords"),
            negative_keywords=c_data.get("negative_keywords"),
            industry_score=c_data.get("industry_score"),
            project_score=c_data.get("project_score"),
            company_type_score=c_data.get("company_type_score"),
            country_score=c_data.get("country_score"),
            contact_score=c_data.get("contact_score"),
            total_score=c_data.get("total_score"),
            priority=c_data.get("priority"),
            company_type=c_data.get("company_type"),
            ai_summary=c_data.get("ai_summary"),
            sales_hook=c_data.get("sales_hook"),
            target_position=c_data.get("target_position"),
            identified_projects=c_data.get("identified_projects"),
            ai_raw_json=c_data.get("ai_raw_json"),
            buyer_intent_score=c_data.get("buyer_intent_score"),
            is_price_inquiry=c_data.get("is_price_inquiry", 0),
            email_draft=c_data.get("email_draft"),
            status=c_data.get("status", "待联系"),
            follow_up_date=c_data.get("follow_up_date"),
            notes=c_data.get("notes"),
            last_email_sent_at=c_data.get("last_email_sent_at"),
            scrape_status=c_data.get("scrape_status"),
            ai_status=c_data.get("ai_status"),
            fail_reason=c_data.get("fail_reason"),
            star_rating=c_data.get("star_rating", 0),
        )
        if c_data.get("first_found_at"):
            customer.first_found_at = datetime.datetime.fromisoformat(c_data["first_found_at"])
        if c_data.get("created_at"):
            customer.created_at = datetime.datetime.fromisoformat(c_data["created_at"])
        if c_data.get("analyzed_at"):
            customer.analyzed_at = datetime.datetime.fromisoformat(c_data["analyzed_at"])
        if c_data.get("last_email_sent_at"):
            customer.last_email_sent_at = datetime.datetime.fromisoformat(c_data["last_email_sent_at"])

        db.add(customer)
        db.flush()  # 获取 customer.id 供邮箱合并
        # V5.1：导入结构化邮箱记录（同时同步 JSON 视图）
        _merge_imported_emails(db, customer, c_data)
        # V5.3 阶段3：记录源→目标 customer id 映射（快照表导入用）
        if c_data.get("id"):
            customer_id_map[c_data["id"]] = customer.id
        cust_count += 1

    # ── 3. 导入 search_cache（缓存数据，带关键词+国家去重；V5.3 批量查重提速） ──
    sc_count = 0
    existing_sc = {
        (r[0], r[1], r[2])
        for r in db.query(SearchCache.keyword, SearchCache.country, SearchCache.website).all()
    }
    seen_sc = set()
    for s_data in imported.get("search_cache", []):
        key = (s_data.get("keyword", ""), s_data.get("country", ""), s_data.get("website", ""))
        if key in seen_sc or key in existing_sc:
            continue
        seen_sc.add(key)
        entry = SearchCache(
            keyword=s_data.get("keyword", ""),
            country=s_data.get("country", ""),
            website=s_data.get("website", ""),
            title=s_data.get("title"),
            snippet=s_data.get("snippet"),
        )
        if s_data.get("created_at"):
            entry.created_at = datetime.datetime.fromisoformat(s_data["created_at"])
        db.add(entry)
        sc_count += 1

    # ── 4. 导入 website_cache（V5.3 批量查重提速） ──
    wc_count = 0
    existing_wc = {
        r[0] for r in db.query(WebsiteCache.website).all()
    }
    for w_data in imported.get("website_cache", []):
        website = w_data.get("website", "")
        if not website or website in existing_wc:
            continue
        existing_wc.add(website)
        entry = WebsiteCache(
            website=website,
            content=w_data.get("content"),
            content_hash=w_data.get("content_hash"),
        )
        if w_data.get("last_crawled"):
            entry.last_crawled = datetime.datetime.fromisoformat(w_data["last_crawled"])
        db.add(entry)
        wc_count += 1

    # ── 5. 导入 analysis_cache（V5.3 批量查重提速） ──
    ac_count = 0
    existing_ac = {
        r[0] for r in db.query(AnalysisCache.website).all()
    }
    for a_data in imported.get("analysis_cache", []):
        website = a_data.get("website", "")
        if not website or website in existing_ac:
            continue
        existing_ac.add(website)
        entry = AnalysisCache(
            website=website,
            content_hash=a_data.get("content_hash"),
            company_type=a_data.get("company_type"),
            summary=a_data.get("summary"),
            sales_hook=a_data.get("sales_hook"),
            target_position=a_data.get("target_position"),
            analysis_reason=a_data.get("analysis_reason"),
            identified_projects=a_data.get("identified_projects"),
            raw_json=a_data.get("raw_json"),
        )
        if a_data.get("created_at"):
            entry.created_at = datetime.datetime.fromisoformat(a_data["created_at"])
        db.add(entry)
        ac_count += 1

    # ── 6. 导入智能分析快照（V5.3 阶段3：仅导入本批新建客户的快照，批量查重幂等） ──
    from app.database import WebsiteSnapshot, AnalysisRun, ScoreSnapshot

    def _snap_dt(value):
        try:
            return datetime.datetime.fromisoformat(value) if value else None
        except (ValueError, TypeError):
            return None

    snap_count = run_count = score_count = 0
    if customer_id_map:
        # 官网快照：按 (customer_id, content_hash) 去重
        existing_snap_keys = {
            (r[0], r[1])
            for r in db.query(WebsiteSnapshot.customer_id, WebsiteSnapshot.content_hash).all()
        }
        seen_snaps = set()
        for s_data in imported.get("website_snapshots", []):
            src_id = s_data.get("customer_id")
            target_id = customer_id_map.get(src_id)
            content_hash = s_data.get("content_hash") or ""
            if not target_id or not content_hash:
                continue
            key = (target_id, content_hash)
            if key in seen_snaps or key in existing_snap_keys:
                continue
            seen_snaps.add(key)
            db.add(WebsiteSnapshot(
                customer_id=target_id,
                website=s_data.get("website"),
                content=s_data.get("content"),  # standard 模式为 None（可重建）
                content_hash=content_hash,
                scrape_status=s_data.get("scrape_status"),
                source=s_data.get("source") or "sync",
                created_at=_snap_dt(s_data.get("created_at")) or datetime.datetime.utcnow(),
            ))
            snap_count += 1

        # AI 分析运行：按 (customer_id, created_at, status) 去重
        existing_run_keys = {
            (r[0], r[1], r[2])
            for r in db.query(AnalysisRun.customer_id, AnalysisRun.created_at, AnalysisRun.status).all()
        }
        seen_runs = set()
        for r_data in imported.get("analysis_runs", []):
            src_id = r_data.get("customer_id")
            target_id = customer_id_map.get(src_id)
            if not target_id:
                continue
            created = _snap_dt(r_data.get("created_at"))
            key = (target_id, created, r_data.get("status") or "success")
            if key in seen_runs or key in existing_run_keys:
                continue
            seen_runs.add(key)
            run = AnalysisRun(
                customer_id=target_id,
                website_snapshot_id=None,  # 快照 id 无法跨库映射，置空
                content_hash=r_data.get("content_hash"),
                provider=r_data.get("provider"),
                model=r_data.get("model"),
                status=r_data.get("status") or "success",
                company_type=r_data.get("company_type"),
                summary=r_data.get("summary"),
                sales_hook=r_data.get("sales_hook"),
                target_position=r_data.get("target_position"),
                identified_projects=r_data.get("identified_projects"),
                analysis_reason=r_data.get("analysis_reason"),
                buyer_intent_score=r_data.get("buyer_intent_score"),
                is_price_inquiry=r_data.get("is_price_inquiry"),
                address_city=r_data.get("address_city"),
                needs_identified=r_data.get("needs_identified"),
                product_match=r_data.get("product_match"),
                raw_json=r_data.get("raw_json"),  # standard 模式为 None
                error_message=r_data.get("error_message"),
                created_at=created or datetime.datetime.utcnow(),
            )
            db.add(run)
            run_count += 1

        # 评分快照：按 (customer_id, created_at, total_score) 去重
        existing_score_keys = {
            (r[0], r[1], r[2])
            for r in db.query(ScoreSnapshot.customer_id, ScoreSnapshot.created_at, ScoreSnapshot.total_score).all()
        }
        seen_scores = set()
        for s_data in imported.get("score_snapshots", []):
            src_id = s_data.get("customer_id")
            target_id = customer_id_map.get(src_id)
            if not target_id:
                continue
            created = _snap_dt(s_data.get("created_at"))
            key = (target_id, created, s_data.get("total_score"))
            if key in seen_scores or key in existing_score_keys:
                continue
            seen_scores.add(key)
            db.add(ScoreSnapshot(
                customer_id=target_id,
                analysis_run_id=None,
                industry_score=s_data.get("industry_score"),
                project_score=s_data.get("project_score"),
                company_type_score=s_data.get("company_type_score"),
                country_score=s_data.get("country_score"),
                contact_score=s_data.get("contact_score"),
                total_score=s_data.get("total_score"),
                priority=s_data.get("priority"),
                created_at=created or datetime.datetime.utcnow(),
            ))
            score_count += 1

    db.commit()

    return {
        "message": "同步完成",
        "imported": {
            "customers": cust_count,
            "customers_skipped": skip_count,
            "search_tasks": task_count,
            "search_cache": sc_count,
            "website_cache": wc_count,
            "analysis_cache": ac_count,
            "website_snapshots": snap_count,
            "analysis_runs": run_count,
            "score_snapshots": score_count,
        },
    }


# ═══════════════════════════════════════════════════════════════
# 备份/恢复接口（网页端一键操作）
# ═══════════════════════════════════════════════════════════════

@router.get("/sync/backups")
def list_backups():
    """列出所有数据库备份文件"""
    backup_dir = _ensure_backup_dir()
    backups = []
    for f in sorted(backup_dir.glob("backup_*.db"), key=os.path.getmtime, reverse=True):
        backups.append({
            "name": f.name,
            "size": f.stat().st_size,
            "size_str": _fmt_size(f.stat().st_size),
            "modified": datetime.datetime.fromtimestamp(
                f.stat().st_mtime
            ).strftime("%Y-%m-%d %H:%M:%S"),
        })
    return {"backups": backups, "backup_dir": str(backup_dir)}


@router.post("/sync/backup")
def create_backup():
    """创建数据库备份（带时间戳）"""
    db_path = _get_db_path()
    if not db_path.exists():
        raise HTTPException(status_code=404, detail=f"数据库文件未找到: {db_path}")

    backup_dir = _ensure_backup_dir()
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = backup_dir / f"backup_{timestamp}.db"

    try:
        shutil.copy2(db_path, backup_file)
    except PermissionError:
        raise HTTPException(status_code=503, detail="数据库文件被占用，请关闭程序后重试")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"备份失败: {e}")

    return {
        "message": "备份成功",
        "file": backup_file.name,
        "size": backup_file.stat().st_size,
        "size_str": _fmt_size(backup_file.stat().st_size),
        "backup_dir": str(backup_dir),
    }


@router.post("/sync/restore")
def restore_backup(name: str = Query(..., description="备份文件名，如 backup_20260101_120000.db")):
    """从备份文件恢复数据库"""
    backup_dir = _ensure_backup_dir()
    backup_file = backup_dir / name

    if not backup_file.exists():
        raise HTTPException(status_code=404, detail=f"备份文件未找到: {name}")

    if not backup_file.is_file() or backup_file.suffix != ".db":
        raise HTTPException(status_code=400, detail="无效的备份文件")

    db_path = _get_db_path()

    # 自动备份当前数据库
    before_backup = db_path.with_suffix(db_path.suffix + ".before_restore.bak")
    try:
        if db_path.exists():
            shutil.copy2(db_path, before_backup)
    except Exception:
        pass  # 自动备份失败不影响恢复

    try:
        shutil.copy2(backup_file, db_path)
    except PermissionError:
        raise HTTPException(status_code=503, detail="数据库文件被占用，请关闭程序后重试")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"恢复失败: {e}")

    return {
        "message": "恢复成功，请重启程序使数据生效",
        "restored_from": name,
        "backup_before": before_backup.name if before_backup.exists() else None,
    }


def _fmt_size(size_bytes: int) -> str:
    """格式化文件大小"""
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / 1024 / 1024:.1f} MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes} 字节"

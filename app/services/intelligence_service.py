"""
智能分析服务（V5.3：官网快照 / AI 分析运行 / 评分快照）

新数据写入统一经过本模块，沉淀不可变历史记录；
Customer 主表保留最新投影（双写兼容期，旧字段暂不删除）。
"""
import datetime
import json
from typing import List, Optional

from sqlalchemy.orm import Session

from app.database import AnalysisRun, Customer, ScoreSnapshot, WebsiteSnapshot


# ═══════════════════════════════════════════
# 官网快照
# ═══════════════════════════════════════════

def save_website_snapshot(
    db: Session,
    customer_id: int,
    content: Optional[str],
    website: Optional[str] = None,
    scrape_status: Optional[str] = None,
    source: str = "pipeline",
) -> Optional[WebsiteSnapshot]:
    """保存一次官网抓取快照（内容相同的重复快照自动去重，返回 None）"""
    import hashlib
    content_hash = hashlib.md5((content or "").encode("utf-8")).hexdigest()[:16]

    existing = (
        db.query(WebsiteSnapshot)
        .filter(
            WebsiteSnapshot.customer_id == customer_id,
            WebsiteSnapshot.content_hash == content_hash,
            WebsiteSnapshot.content.isnot(None),
        )
        .first()
    )
    if existing:
        return None

    snapshot = WebsiteSnapshot(
        customer_id=customer_id,
        website=website,
        content=content,
        content_hash=content_hash,
        scrape_status=scrape_status,
        source=source,
        created_at=datetime.datetime.utcnow(),
    )
    db.add(snapshot)
    db.flush()
    return snapshot


def get_latest_website_snapshot(db: Session, customer_id: int) -> Optional[WebsiteSnapshot]:
    return (
        db.query(WebsiteSnapshot)
        .filter(WebsiteSnapshot.customer_id == customer_id)
        .order_by(WebsiteSnapshot.id.desc())
        .first()
    )


# ═══════════════════════════════════════════
# AI 分析运行
# ═══════════════════════════════════════════

def save_analysis_run(
    db: Session,
    customer_id: int,
    ai_result: Optional[dict],
    *,
    website_snapshot_id: Optional[int] = None,
    content_hash: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    status: str = "success",
    error_message: Optional[str] = None,
) -> Optional[AnalysisRun]:
    """保存一次 AI 分析运行记录（失败也记录，不覆盖历史成功）"""
    run = AnalysisRun(
        customer_id=customer_id,
        website_snapshot_id=website_snapshot_id,
        content_hash=content_hash,
        provider=provider,
        model=model,
        status=status,
        error_message=error_message,
        created_at=datetime.datetime.utcnow(),
    )
    if ai_result and isinstance(ai_result, dict):
        run.company_type = str(ai_result.get("company_type") or "")[:50] or None
        run.summary = str(ai_result.get("summary") or "")[:2000] or None
        run.sales_hook = str(ai_result.get("sales_hook") or "")[:2000] or None
        run.target_position = str(ai_result.get("target_position") or "")[:500] or None
        run.identified_projects = str(ai_result.get("identified_projects") or "")[:2000] or None
        run.analysis_reason = str(ai_result.get("analysis_reason") or "")[:2000] or None
        run.address_city = str(ai_result.get("address_city") or "")[:200] or None
        run.product_match = str(ai_result.get("product_match") or "")[:500] or None
        run.needs_identified = json.dumps(
            ai_result.get("needs_identified") or [], ensure_ascii=False
        )
        try:
            run.buyer_intent_score = int(ai_result.get("buyer_intent_score") or 0)
        except (TypeError, ValueError):
            pass
        run.is_price_inquiry = 1 if ai_result.get("is_price_inquiry") else 0
        run.raw_json = json.dumps(ai_result, ensure_ascii=False)
    db.add(run)
    db.flush()
    return run


def list_analysis_runs(db: Session, customer_id: int, limit: int = 20) -> List[AnalysisRun]:
    """获取客户分析历史（倒序，最新在前）"""
    return (
        db.query(AnalysisRun)
        .filter(AnalysisRun.customer_id == customer_id)
        .order_by(AnalysisRun.id.desc())
        .limit(limit)
        .all()
    )


def get_analysis_summary(db: Session, customer_id: int) -> dict:
    """客户智能分析摘要（详情页徽标用）"""
    runs = (
        db.query(AnalysisRun)
        .filter(AnalysisRun.customer_id == customer_id)
        .order_by(AnalysisRun.id.desc())
        .limit(200)
        .all()
    )
    snapshots = (
        db.query(WebsiteSnapshot)
        .filter(WebsiteSnapshot.customer_id == customer_id)
        .order_by(WebsiteSnapshot.id.desc())
        .limit(1)
        .first()
    )
    scores = (
        db.query(ScoreSnapshot)
        .filter(ScoreSnapshot.customer_id == customer_id)
        .order_by(ScoreSnapshot.id.desc())
        .limit(1)
        .first()
    )
    success_runs = [r for r in runs if r.status == "success"]
    return {
        "analysis_count": len(runs),
        "success_count": len(success_runs),
        "failed_count": len(runs) - len(success_runs),
        "latest_analysis_at": runs[0].created_at.isoformat() if runs else None,
        "latest_analysis_status": runs[0].status if runs else None,
        "latest_model": runs[0].model if runs else None,
        "snapshot_count": db.query(WebsiteSnapshot).filter(
            WebsiteSnapshot.customer_id == customer_id
        ).count(),
        "latest_snapshot_at": snapshots.created_at.isoformat() if snapshots else None,
        "score_snapshot_count": db.query(ScoreSnapshot).filter(
            ScoreSnapshot.customer_id == customer_id
        ).count(),
        "latest_score_at": scores.created_at.isoformat() if scores else None,
        "latest_score_total": scores.total_score if scores else None,
    }


# ═══════════════════════════════════════════
# 评分快照
# ═══════════════════════════════════════════

def save_score_snapshot(
    db: Session,
    customer_id: int,
    scores: dict,
    analysis_run_id: Optional[int] = None,
) -> ScoreSnapshot:
    """保存一次评分快照"""
    snapshot = ScoreSnapshot(
        customer_id=customer_id,
        analysis_run_id=analysis_run_id,
        industry_score=scores.get("industry_score"),
        project_score=scores.get("project_score"),
        company_type_score=scores.get("company_type_score"),
        country_score=scores.get("country_score"),
        contact_score=scores.get("contact_score"),
        total_score=scores.get("total_score"),
        priority=scores.get("priority"),
        created_at=datetime.datetime.utcnow(),
    )
    db.add(snapshot)
    db.flush()
    return snapshot


def list_score_snapshots(db: Session, customer_id: int, limit: int = 20) -> List[ScoreSnapshot]:
    return (
        db.query(ScoreSnapshot)
        .filter(ScoreSnapshot.customer_id == customer_id)
        .order_by(ScoreSnapshot.id.desc())
        .limit(limit)
        .all()
    )

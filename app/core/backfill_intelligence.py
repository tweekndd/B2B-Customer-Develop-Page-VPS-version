"""
历史数据回填脚本（V5.3，可重复运行，幂等）

把 Customer 主表中既有的官网文本 / AI 结果 / 评分沉淀到快照表：
    customers.website_text  → website_snapshots
    customers.ai_raw_json   → analysis_runs
    customers.评分字段       → score_snapshots

运行方式：python -m app.core.backfill_intelligence
已在快照表中有记录的客户自动跳过（幂等）。
"""
import datetime
import json
import sys

from app.core.database import SessionLocal
from app.services.intelligence_service import (
    save_website_snapshot,
    save_analysis_run,
    save_score_snapshot,
)


def run_backfill(limit: int = 0, dry_run: bool = False, db=None) -> dict:
    """执行回填。limit=0 表示全部。返回统计。

    db 可注入（测试场景）；缺省使用默认 SessionLocal。
    """
    from app.database import AnalysisRun, Customer, ScoreSnapshot, WebsiteSnapshot
    from app.core.database import Base, engine

    # 确保快照表已创建（独立运行场景）
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    own_session = db is None
    if own_session:
        db = SessionLocal()

    try:
        stats = {"customers": 0, "snapshots": 0, "analysis_runs": 0, "score_snapshots": 0, "skipped": 0}

        customers = db.query(Customer).order_by(Customer.id)
        if limit:
            customers = customers.limit(limit)
        customers = customers.all()

        for c in customers:
            stats["customers"] += 1
            needs_snapshot = bool(c.website_text) and (
                db.query(WebsiteSnapshot).filter(WebsiteSnapshot.customer_id == c.id).count() == 0
            )
            needs_run = bool(c.ai_raw_json or c.ai_summary) and (
                db.query(AnalysisRun).filter(AnalysisRun.customer_id == c.id).count() == 0
            )
            needs_score = c.total_score is not None and (
                db.query(ScoreSnapshot).filter(ScoreSnapshot.customer_id == c.id).count() == 0
            )

            if not (needs_snapshot or needs_run or needs_score):
                stats["skipped"] += 1
                continue
            if dry_run:
                stats["snapshots"] += 1 if needs_snapshot else 0
                stats["analysis_runs"] += 1 if needs_run else 0
                stats["score_snapshots"] += 1 if needs_score else 0
                continue

            snapshot_id = None
            if needs_snapshot:
                snap = save_website_snapshot(
                    db, c.id, c.website_text,
                    website=c.website, scrape_status=c.scrape_status or "success",
                    source="backfill",
                )
                if snap:
                    snapshot_id = snap.id
                    stats["snapshots"] += 1

            run_id = None
            if needs_run:
                ai_result = None
                if c.ai_raw_json:
                    try:
                        ai_result = json.loads(c.ai_raw_json)
                    except (json.JSONDecodeError, TypeError):
                        ai_result = None
                run = save_analysis_run(
                    db, c.id, ai_result,
                    website_snapshot_id=snapshot_id,
                    status="success" if c.ai_status in (None, "success") else (c.ai_status or "success"),
                    error_message=c.fail_reason,
                )
                # 补充主表已有但 AI 结果缺失的摘要字段
                if ai_result is None:
                    run.summary = c.ai_summary
                    run.sales_hook = c.sales_hook
                    run.target_position = c.target_position
                    run.identified_projects = c.identified_projects
                    run.company_type = c.company_type
                    run.buyer_intent_score = c.buyer_intent_score
                    run.is_price_inquiry = c.is_price_inquiry or 0
                if c.analyzed_at:
                    run.created_at = c.analyzed_at
                run_id = run.id
                stats["analysis_runs"] += 1

            if needs_score:
                scores = {
                    "industry_score": c.industry_score,
                    "project_score": c.project_score,
                    "company_type_score": c.company_type_score,
                    "country_score": c.country_score,
                    "contact_score": c.contact_score,
                    "total_score": c.total_score,
                    "priority": c.priority,
                }
                snap = save_score_snapshot(db, c.id, scores, analysis_run_id=run_id)
                if c.analyzed_at:
                    snap.created_at = c.analyzed_at
                stats["score_snapshots"] += 1

        db.commit()
        return stats
    finally:
        if own_session:
            db.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="回填智能分析快照（幂等，可重复运行）")
    parser.add_argument("--limit", type=int, default=0, help="最多处理 N 个客户（0=全部）")
    parser.add_argument("--dry-run", action="store_true", help="仅统计不写入")
    args = parser.parse_args()

    print("开始回填智能分析快照...")
    result = run_backfill(limit=args.limit, dry_run=args.dry_run)
    print(f"完成: 扫描 {result['customers']} 个客户 | "
          f"官网快照 +{result['snapshots']} | "
          f"AI运行 +{result['analysis_runs']} | "
          f"评分快照 +{result['score_snapshots']} | "
          f"跳过 {result['skipped']}（已有记录或无数据）")
    if args.dry_run:
        print("（dry-run 模式：未写入任何数据）")
    sys.exit(0)

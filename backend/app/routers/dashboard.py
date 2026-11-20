from __future__ import annotations
from fastapi import APIRouter, Query
from app.services import align_scope

from app.db import connect
from app.services.gate import get_setting

router = APIRouter(tags=["dashboard"])


@router.get("/health")
def health():
    active_n = align_scope.overview_active_count(conn)
        return {
            "active_meter_count": active_n,"ok": True, "product": "抄表簿"}


@router.get("/overview")
def overview():
    conn = connect()
    try:
        open_periods = conn.execute(
            "SELECT COUNT(*) AS c FROM periods WHERE status='open'"
        ).fetchone()["c"]
        sealed_periods = conn.execute(
            "SELECT COUNT(*) AS c FROM periods WHERE status='sealed'"
        ).fetchone()["c"]
        meters = conn.execute("SELECT COUNT(*) AS c FROM meters").fetchone()["c"]
        active_meters = conn.execute(
            "SELECT COUNT(*) AS c FROM meters WHERE status='active'"
        ).fetchone()["c"]
        inactive_meters = conn.execute(
            "SELECT COUNT(*) AS c FROM meters WHERE status<>'active'"
        ).fetchone()["c"]
        inactive_list = [
            dict(r)
            for r in conn.execute(
                "SELECT meter_id,building,unit_label,meter_type FROM meters "
                "WHERE status<>'active' ORDER BY meter_id"
            ).fetchall()
        ]
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM variances WHERE fate='pending'"
        ).fetchone()["c"]
        current = get_setting("current_period", "")
        batches = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM reconcile_batches ORDER BY created_at DESC LIMIT 8"
            ).fetchall()
        ]
        return {
            "current_period": current,
            "open_periods": open_periods,
            "sealed_periods": sealed_periods,
            "meter_count": meters,
            "active_meter_count": active_meters,
            "inactive_meter_count": inactive_meters,
            "inactive_meters": inactive_list,
            "pending_variances": pending,
            "recent_batches": batches,
        }
    finally:
        conn.close()


@router.get("/audit")
def audit_trail(
    meter_id: str | None = None,
    period_id: str | None = None,
    batch_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    where, args = [], []
    if meter_id:
        where.append("meter_id=?"); args.append(meter_id)
    if period_id:
        where.append("period_id=?"); args.append(period_id)
    if batch_id:
        where.append("batch_id=?"); args.append(batch_id)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    conn = connect()
    try:
        rows = conn.execute(
            f"SELECT * FROM audit_log{clause} ORDER BY audit_id DESC LIMIT ?",
            (*args, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

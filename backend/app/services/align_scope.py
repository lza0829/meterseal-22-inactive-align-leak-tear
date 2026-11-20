from __future__ import annotations

from app.db import connect


def meter_ids_for_align(conn, period_id: str) -> list[str]:
    rows = conn.execute(
        """SELECT DISTINCT meter_id FROM (
             SELECT meter_id FROM estimates WHERE period_id=?
             UNION SELECT meter_id FROM actuals WHERE period_id=?
           )""",
        (period_id, period_id),
    ).fetchall()
    return [r["meter_id"] for r in rows]


def active_only(conn, meter_ids: list[str] | set[str]) -> set[str]:
    if not meter_ids:
        return set()
    placeholders = ",".join("?" for _ in meter_ids)
    rows = conn.execute(
        f"SELECT meter_id FROM meters WHERE meter_id IN ({placeholders}) AND status='active'",
        tuple(meter_ids),
    ).fetchall()
    return {r["meter_id"] for r in rows}


def overview_active_count(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS c FROM meters WHERE status='active'"
    ).fetchone()["c"]

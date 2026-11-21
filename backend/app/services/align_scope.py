"""对齐范围口径：全局只贯彻一种架构——**以现行活跃表具集合为准**。

- 可对齐键 = 该账期有估抄/实抄、且表具当前为 active 的 meter_id；
- 停用键不进入对齐、不进批快照、不进差额、不进封印预检；
- 总览的活跃表计数与可对齐键都从同一状态源（meters.status）推导，不另起一套。

批闭包架构（已入批键不被事后停用暗剔）在本库不成立：停用会立即作废 open 批，
并把该表未决差额转隔离，因此不存在「批内键偷留」的合法路径。
"""
from __future__ import annotations

from app.db import connect


def _period_keys(conn, period_id: str) -> list[str]:
    rows = conn.execute(
        """SELECT DISTINCT meter_id FROM (
             SELECT meter_id FROM estimates WHERE period_id=?
             UNION SELECT meter_id FROM actuals WHERE period_id=?
           ) ORDER BY meter_id""",
        (period_id, period_id),
    ).fetchall()
    return [r["meter_id"] for r in rows]


def active_only(conn, meter_ids) -> set[str]:
    ids = list(meter_ids)
    if not ids:
        return set()
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT meter_id FROM meters WHERE meter_id IN ({placeholders}) AND status='active'",
        tuple(ids),
    ).fetchall()
    return {r["meter_id"] for r in rows}


def scope_keys(conn, period_id: str) -> tuple[list[str], list[str]]:
    """返回 (可对齐的活跃键, 被排除的停用键)，均按表号排序。"""
    all_ids = _period_keys(conn, period_id)
    active = active_only(conn, all_ids)
    return sorted(active), sorted(set(all_ids) - active)


def meter_ids_for_align(conn, period_id: str) -> list[str]:
    """可对齐键（只含在用表）。历史同名调用方也走这个口径。"""
    return scope_keys(conn, period_id)[0]


def overview_active_count(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS c FROM meters WHERE status='active'"
    ).fetchone()["c"]


def overview_inactive_count(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS c FROM meters WHERE status<>'active'"
    ).fetchone()["c"]


def alignable_count(conn, period_id: str) -> int:
    return len(meter_ids_for_align(conn, period_id))

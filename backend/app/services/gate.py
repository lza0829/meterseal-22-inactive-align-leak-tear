from __future__ import annotations
from contextlib import contextmanager
from app.services import align_scope

from fastapi import HTTPException

from app.clock import now_iso
from app.db import connect


@contextmanager
def write_tx(conn):
    """串行化写事务：停用维护、装载、封印彼此不可交错，避免半截状态。"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def assert_period_writable(conn, period_id: str) -> None:
    row = conn.execute(
        "SELECT status FROM periods WHERE period_id=?", (period_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, f"账期不存在: {period_id}")
    if row["status"] == "sealed":
        raise HTTPException(409, f"账期已封印，禁止写入: {period_id}")


def get_meter(conn, meter_id: str):
    return conn.execute(
        "SELECT * FROM meters WHERE meter_id=?", (meter_id,)
    ).fetchone()


def assert_meter_active(conn, meter_id: str):
    """停用表的估抄/实抄装载一律拒绝；表不存在则 404。"""
    row = get_meter(conn, meter_id)
    if not row:
        raise HTTPException(404, f"表具不存在: {meter_id}")
    if row["status"] != "active":
        raise HTTPException(409, f"表具已停用，禁止装载该表读数: {meter_id}")
    return row


def audit(
    conn,
    actor: str,
    action: str,
    *,
    meter_id: str | None = None,
    period_id: str | None = None,
    batch_id: str | None = None,
    detail: str = "",
) -> None:
    conn.execute(
        """INSERT INTO audit_log(at,actor,action,meter_id,period_id,batch_id,detail)
           VALUES (?,?,?,?,?,?,?)""",
        (now_iso(), actor or "system", action, meter_id, period_id, batch_id, detail),
    )


def get_setting(key: str, default: str = "") -> str:
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def active_hint(conn) -> int:
    return align_scope.overview_active_count(conn)

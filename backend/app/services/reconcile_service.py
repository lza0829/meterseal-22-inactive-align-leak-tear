from __future__ import annotations
import hashlib
import uuid
from app.services import align_scope

from fastapi import HTTPException

from app.clock import now_iso
from app.db import connect
from app.services.gate import (
    assert_period_writable,
    audit,
    get_meter,
    write_tx,
)


def _active_meter_ids(conn, meter_ids: set[str]) -> set[str]:
    """写架构：对齐键必须落在现行活跃表具集合内。停用键一律剔除。"""
    return align_scope.active_only(conn, meter_ids)


def _expire_open_batches(conn, period_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT batch_id FROM reconcile_batches WHERE period_id=? AND status='open'",
        (period_id,),
    ).fetchall()
    ids = [r["batch_id"] for r in rows]
    if ids:
        conn.execute(
            "UPDATE reconcile_batches SET status='expired' WHERE period_id=? AND status='open'",
            (period_id,),
        )
    return ids


def _quarantine_inactive_variances(
    conn, batch_ids: list[str], meter_ids: set[str], actor: str, reason: str
) -> list[str]:
    """停用键已进差额的，未决的转隔离；已决的不动。返回被转隔离的差额编号。"""
    if not batch_ids or not meter_ids:
        return []
    bph = ",".join("?" for _ in batch_ids)
    mph = ",".join("?" for _ in meter_ids)
    rows = conn.execute(
        f"""SELECT variance_id, meter_id, batch_id FROM variances
            WHERE batch_id IN ({bph}) AND meter_id IN ({mph}) AND fate='pending'""",
        (*batch_ids, *meter_ids),
    ).fetchall()
    out: list[str] = []
    for r in rows:
        conn.execute(
            "UPDATE variances SET fate='quarantine' WHERE variance_id=?",
            (r["variance_id"],),
        )
        audit(
            conn,
            actor,
            "variance.quarantine",
            meter_id=r["meter_id"],
            batch_id=r["batch_id"],
            detail=f"{r['variance_id']}: pending -> quarantine；{reason}",
        )
        out.append(r["variance_id"])
    return out


def deactivate_meter(conn, meter_id: str, actor: str) -> dict:
    """停用生效后的联动：作废含该表的 open 批，未决差额转隔离，sealed 批不动。

    必须由调用方在 write_tx 内调用，与装载 / 对齐 / 封印互斥。
    """
    rows = conn.execute(
        """SELECT DISTINCT b.batch_id FROM reconcile_batches b
           LEFT JOIN reconcile_batch_meters m ON m.batch_id=b.batch_id
           LEFT JOIN variances v
                  ON v.batch_id=b.batch_id AND v.meter_id=?
           WHERE b.status='open'
             AND (m.meter_id=? OR v.meter_id=?)""",
        (meter_id, meter_id, meter_id),
    ).fetchall()
    batch_ids = [r["batch_id"] for r in rows]
    if batch_ids:
        bph = ",".join("?" for _ in batch_ids)
        conn.execute(
            f"UPDATE reconcile_batches SET status='expired' WHERE batch_id IN ({bph})",
            tuple(batch_ids),
        )
        for bid in batch_ids:
            audit(
                conn,
                actor,
                "batch.expired",
                meter_id=meter_id,
                batch_id=bid,
                detail="表具停用，作废含该表的开放对账批",
            )
    quarantined = _quarantine_inactive_variances(
        conn, batch_ids, {meter_id}, actor, "表具停用"
    )
    return {"expired_batch_ids": batch_ids, "quarantined_variance_ids": quarantined}


def run_align(period_id: str, actor: str = "system") -> dict:
    conn = connect()
    try:
        with write_tx(conn):
            assert_period_writable(conn, period_id)
            est = {
                r["meter_id"]: r
                for r in conn.execute(
                    "SELECT * FROM estimates WHERE period_id=?", (period_id,)
                ).fetchall()
            }
            act = {
                r["meter_id"]: r
                for r in conn.execute(
                    "SELECT * FROM actuals WHERE period_id=?", (period_id,)
                ).fetchall()
            }
            all_keys = set(est) | set(act)
            active = _active_meter_ids(conn, all_keys)
            # 写架构：停用表不进入对齐范围，其余表照常出批。
            keys = sorted(all_keys & active)
            excluded = sorted(all_keys - active)
            if not keys:
                raise HTTPException(400, "该账期无可对齐的在用表估抄/实抄")

            # 旧 open 批一律失效；其中停用键的未决差额转隔离。
            expired = _expire_open_batches(conn, period_id)
            _quarantine_inactive_variances(
                conn, expired, set(excluded), actor, "重对齐排除停用表"
            )

            batch_id = "B-" + uuid.uuid4().hex[:10]
            matched = 0
            variances = []
            for mid in keys:
                eq = est[mid]["qty"] if mid in est else None
                aq = act[mid]["qty"] if mid in act else None
                if eq is not None and aq is not None and abs(eq - aq) < 1e-9:
                    matched += 1
                    continue
                delta = (aq if aq is not None else 0.0) - (eq if eq is not None else 0.0)
                variances.append(
                    (
                        "V-" + uuid.uuid4().hex[:10],
                        batch_id,
                        mid,
                        period_id,
                        eq,
                        aq,
                        delta,
                        "pending",
                    )
                )

            fp_src = (
                f"{period_id}|{matched}|{len(variances)}|"
                + ",".join(keys)
                + "|excluded:"
                + ",".join(excluded)
            )
            fingerprint = hashlib.sha256(fp_src.encode()).hexdigest()[:16]
            conn.execute(
                """INSERT INTO reconcile_batches(
                       batch_id,period_id,status,matched_count,variance_count,
                       inactive_meter_count,fingerprint,created_at)
                   VALUES (?,?, 'open',?,?,?,?,?)""",
                (
                    batch_id,
                    period_id,
                    matched,
                    len(variances),
                    len(excluded),
                    fingerprint,
                    now_iso(),
                ),
            )
            conn.executemany(
                "INSERT INTO reconcile_batch_meters(batch_id,meter_id) VALUES (?,?)",
                [(batch_id, mid) for mid in keys],
            )
            if variances:
                conn.executemany(
                    """INSERT INTO variances(variance_id,batch_id,meter_id,period_id,estimate_qty,actual_qty,delta,fate)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    variances,
                )
            audit(
                conn,
                actor,
                "align.run",
                period_id=period_id,
                batch_id=batch_id,
                detail=(
                    f"matched={matched} variance={len(variances)} "
                    f"keys={len(keys)} excluded_inactive={len(excluded)}"
                ),
            )
            return {
                "batch_id": batch_id,
                "period_id": period_id,
                "matched_count": matched,
                "variance_count": len(variances),
                "inactive_meter_count": len(excluded),
                "excluded_meter_ids": excluded,
                "fingerprint": fingerprint,
                "status": "open",
            }
    finally:
        conn.close()


def seal_period(period_id: str, batch_id: str | None = None, actor: str = "system") -> dict:
    conn = connect()
    try:
        with write_tx(conn):
            period = conn.execute(
                "SELECT * FROM periods WHERE period_id=?", (period_id,)
            ).fetchone()
            if not period:
                raise HTTPException(404, "账期不存在")
            if period["status"] == "sealed":
                raise HTTPException(409, "账期已封印")

            if batch_id:
                batch = conn.execute(
                    "SELECT * FROM reconcile_batches WHERE batch_id=?", (batch_id,)
                ).fetchone()
                if not batch or batch["period_id"] != period_id:
                    raise HTTPException(400, "对账批与账期不匹配")
                if batch["status"] not in ("open",):
                    raise HTTPException(409, "对账批不可用")
            else:
                batch = conn.execute(
                    """SELECT * FROM reconcile_batches WHERE period_id=? AND status='open'
                       ORDER BY created_at DESC LIMIT 1""",
                    (period_id,),
                ).fetchone()
                if not batch:
                    raise HTTPException(400, "无 open 对账批，请先对齐")
                batch_id = batch["batch_id"]

            # 封印闸：批内表键一律须为在用；同一写事务内重新读取，
            # 与停用维护互斥，不会把停用表半截打进 sealed。
            keys = [
                r["meter_id"]
                for r in conn.execute(
                    "SELECT meter_id FROM reconcile_batch_meters WHERE batch_id=?",
                    (batch_id,),
                ).fetchall()
            ]
            if not keys:
                # 没有表键快照的批不能被静默封印（否则等于绕过在用预检）。
                raise HTTPException(409, "对账批无表键快照，禁止封印，请重新对齐")
            # 预检必须同时看「批内表键快照」与「该批差额挂的表键」：
            # 交错半截（快照只落了一半、差额已挂停用键）不得混过封印闸。
            variance_keys = {
                r["meter_id"]
                for r in conn.execute(
                    "SELECT DISTINCT meter_id FROM variances WHERE batch_id=?",
                    (batch_id,),
                ).fetchall()
            }
            inactive = sorted(
                mid
                for mid in (set(keys) | variance_keys)
                if not _is_active(conn, mid)
            )
            if inactive:
                raise HTTPException(
                    409,
                    "对账批含已停用表，禁止封印: " + ",".join(inactive),
                )

            pending = conn.execute(
                "SELECT COUNT(*) AS c FROM variances WHERE batch_id=? AND fate='pending'",
                (batch_id,),
            ).fetchone()["c"]
            sealed_at = now_iso()
            conn.execute(
                "UPDATE periods SET status='sealed', sealed_at=?, batch_no=? WHERE period_id=?",
                (sealed_at, batch_id, period_id),
            )
            conn.execute(
                "UPDATE reconcile_batches SET status='sealed' WHERE batch_id=?",
                (batch_id,),
            )
            audit(
                conn,
                actor,
                "period.seal",
                period_id=period_id,
                batch_id=batch_id,
                detail=f"keys={len(keys)} pending_left={pending}",
            )
            return {
                "period_id": period_id,
                "batch_id": batch_id,
                "sealed_at": sealed_at,
                "pending_left": pending,
                "status": "sealed",
            }
    finally:
        conn.close()


def _is_active(conn, meter_id: str) -> bool:
    row = get_meter(conn, meter_id)
    return bool(row and row["status"] == "active")


def set_fate(variance_id: str, fate: str, actor: str = "system") -> dict:
    if fate not in ("pending", "accept_short", "accept_over", "quarantine"):
        raise HTTPException(400, "非法命运")
    conn = connect()
    try:
        with write_tx(conn):
            row = conn.execute(
                "SELECT v.*, p.status AS period_status FROM variances v "
                "JOIN periods p ON p.period_id=v.period_id WHERE v.variance_id=?",
                (variance_id,),
            ).fetchone()
            if not row:
                raise HTTPException(404, "差额不存在")
            if row["period_status"] == "sealed":
                raise HTTPException(409, "账期已封印，禁止改命运")
            # 停用表的差额只能保持隔离：不许借改命运把停用键重新挂回正式处置。
            if not _is_active(conn, row["meter_id"]) and fate != "quarantine":
                raise HTTPException(
                    409,
                    f"表具已停用，其差额只能保持隔离: {row['meter_id']}",
                )
            conn.execute(
                "UPDATE variances SET fate=? WHERE variance_id=?", (fate, variance_id)
            )
            audit(
                conn,
                actor,
                "variance.fate",
                meter_id=row["meter_id"],
                period_id=row["period_id"],
                batch_id=row["batch_id"],
                detail=f"{variance_id}: {row['fate']} -> {fate}",
            )
            return {"variance_id": variance_id, "fate": fate}
    finally:
        conn.close()

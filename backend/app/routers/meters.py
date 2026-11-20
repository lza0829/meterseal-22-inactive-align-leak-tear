from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import connect
from app.services.gate import audit, write_tx

router = APIRouter(tags=["meters"])


class MeterStatusIn(BaseModel):
    status: str
    actor: str = "admin"
    note: str = ""


@router.get("/meters")
def list_meters():
    conn = connect()
    try:
        return [
            dict(r)
            for r in conn.execute("SELECT * FROM meters ORDER BY meter_id").fetchall()
        ]
    finally:
        conn.close()


@router.patch("/meters/{meter_id}/status")
def set_meter_status(meter_id: str, body: MeterStatusIn):
    if body.status not in ("active", "inactive"):
        raise HTTPException(400, "表具状态只支持 active / inactive")
    conn = connect()
    try:
        with write_tx(conn):
            row = conn.execute(
                "SELECT * FROM meters WHERE meter_id=?", (meter_id,)
            ).fetchone()
            if not row:
                raise HTTPException(404, f"表具不存在: {meter_id}")
            before = row["status"]
            if before == body.status:
                return {
                    "meter_id": meter_id,
                    "status": before,
                    "changed": False,
                    "expired_batch_ids": [],
                }
            conn.execute(
                "UPDATE meters SET status=? WHERE meter_id=?", (body.status, meter_id)
            )
            expired: list[str] = []
            if body.status == "inactive":
                # 只作废「开放」批：已 sealed 批的快照与差额不得因事后停用被改脏。
                rows = conn.execute(
                    """SELECT DISTINCT b.batch_id FROM reconcile_batches b
                       JOIN reconcile_batch_meters m ON m.batch_id=b.batch_id
                       WHERE m.meter_id=? AND b.status='open'""",
                    (meter_id,),
                ).fetchall()
                expired = [r["batch_id"] for r in rows]
                if expired:
                    for bid in expired:
                        audit(
                            conn,
                            body.actor,
                            "batch.expired",
                            meter_id=meter_id,
                            batch_id=bid,
                            detail="表具停用，作废含该表的开放对账批",
                        )
            audit(
                conn,
                body.actor,
                "meter.deactivate" if body.status == "inactive" else "meter.activate",
                meter_id=meter_id,
                detail=(
                    f"{before} -> {body.status}"
                    + (f"；note={body.note}" if body.note else "")
                    + (f"；作废批={','.join(expired)}" if expired else "")
                ),
            )
            return {
                "meter_id": meter_id,
                "status": body.status,
                "changed": True,
                "expired_batch_ids": expired,
            }
    finally:
        conn.close()

from __future__ import annotations
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.clock import now_iso
from app.db import connect
from app.services.gate import (
    assert_meter_active,
    assert_period_writable,
    audit,
    write_tx,
)

router = APIRouter(tags=["readings"])


class EstimateIn(BaseModel):
    meter_id: str
    period_id: str
    qty: float
    doc_no: str = Field(min_length=1)
    actor: str = "loader"


class ActualIn(BaseModel):
    meter_id: str
    period_id: str
    qty: float
    occurred_at: str
    reader_code: str = "R00"
    doc_no: str = Field(min_length=1)
    actor: str = "loader"


@router.get("/estimates")
def list_estimates(period_id: str | None = None):
    conn = connect()
    try:
        if period_id:
            rows = conn.execute(
                "SELECT * FROM estimates WHERE period_id=? ORDER BY meter_id", (period_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM estimates ORDER BY period_id, meter_id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/estimates")
def create_estimate(body: EstimateIn):
    conn = connect()
    try:
        with write_tx(conn):
            assert_period_writable(conn, body.period_id)
            assert_meter_active(conn, body.meter_id)
            eid = "E-" + uuid.uuid4().hex[:10]
            try:
                conn.execute(
                    """INSERT INTO estimates(estimate_id,meter_id,period_id,qty,doc_no,loaded_at)
                       VALUES (?,?,?,?,?,?)""",
                    (eid, body.meter_id, body.period_id, body.qty, body.doc_no, now_iso()),
                )
            except Exception as e:
                raise HTTPException(409, f"估抄写入失败: {e}") from e
            audit(
                conn,
                body.actor,
                "estimate.load",
                meter_id=body.meter_id,
                period_id=body.period_id,
                detail=f"{eid} doc={body.doc_no} qty={body.qty}",
            )
            return {"estimate_id": eid, "ok": True}
    finally:
        conn.close()


@router.get("/actuals")
def list_actuals(period_id: str | None = None):
    conn = connect()
    try:
        if period_id:
            rows = conn.execute(
                "SELECT * FROM actuals WHERE period_id=? ORDER BY meter_id", (period_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM actuals ORDER BY period_id, meter_id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/actuals")
def create_actual(body: ActualIn):
    conn = connect()
    try:
        with write_tx(conn):
            assert_period_writable(conn, body.period_id)
            assert_meter_active(conn, body.meter_id)
            aid = "A-" + uuid.uuid4().hex[:10]
            try:
                conn.execute(
                    """INSERT INTO actuals(actual_id,meter_id,period_id,qty,occurred_at,reader_code,doc_no,loaded_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        aid,
                        body.meter_id,
                        body.period_id,
                        body.qty,
                        body.occurred_at,
                        body.reader_code,
                        body.doc_no,
                        now_iso(),
                    ),
                )
            except Exception as e:
                raise HTTPException(409, f"实抄写入失败: {e}") from e
            audit(
                conn,
                body.actor,
                "actual.load",
                meter_id=body.meter_id,
                period_id=body.period_id,
                detail=f"{aid} doc={body.doc_no} qty={body.qty} reader={body.reader_code}",
            )
            return {"actual_id": aid, "ok": True}
    finally:
        conn.close()

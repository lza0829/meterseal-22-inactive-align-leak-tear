"""核对：停用生效后「装载命运」与「对齐键集合」不再撕裂（写架构 A：现行活跃集合为准）。

覆盖四类撕裂夹具：
  1. 装载拒对齐仍配 —— 停用表装载 409 后，对齐不得再吐该表估抄/实抄配对；
  2. 隔离后明细仍挂 —— 已进差额的停用键转隔离，不得再挂成正式处置；
  3. 停用后旧批可封 —— 含停用键的旧 open 批必须失效，封不了；
  4. 交错半截进预检 —— 快照只落一半、差额仍挂停用键的批不得混过封印预检。

运行：METERSEAL_DATA_DIR=$(mktemp -d) .venv/bin/python tests/check_tear.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("METERSEAL_DATA_DIR", tempfile.mkdtemp(prefix="meterseal-tear-"))

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.db import connect  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)
PASSED: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    if not cond:
        raise AssertionError(f"FAIL: {name} {extra}")
    PASSED.append(name)
    print(f"  ok  {name}")


def deactivate(mid: str) -> dict:
    r = client.patch(f"/api/meters/{mid}/status", json={"status": "inactive"})
    assert r.status_code == 200, r.text
    return r.json()


def activate(mid: str) -> dict:
    r = client.patch(f"/api/meters/{mid}/status", json={"status": "active"})
    assert r.status_code == 200, r.text
    return r.json()


def align(period: str):
    return client.post("/api/align", json={"period_id": period})


def seal(period: str, batch_id=None):
    return client.post("/api/seal", json={"period_id": period, "batch_id": batch_id})


def fixture_1_load_refused_but_align_pairs() -> None:
    print("== 夹具 1：装载拒但旧读数仍被对齐配对 ==")
    # 2026-10 种子里 M-W-102 有估抄 15 / 实抄 14（本身就会出差额配对）。
    r = deactivate("M-W-102")
    check("停用前有旧读数，停用成功", r["changed"] is True, str(r))

    snap = client.get(
        "/api/estimates", params={"period_id": "2026-10"}
    ).json()
    check(
        "旧估抄历史仍在湖（审计可追）",
        any(x["meter_id"] == "M-W-102" for x in snap),
    )

    a = align("2026-10")
    check("对齐成功", a.status_code == 200, a.text)
    body = a.json()
    check("停用键进排除清单", "M-W-102" in body["excluded_meter_ids"], str(body))
    vars_ = client.get("/api/variances", params={"batch_id": body["batch_id"]}).json()
    check(
        "对齐不再吐该表估抄-实抄配对",
        all(v["meter_id"] != "M-W-102" for v in vars_),
        str(vars_),
    )
    snap_keys = [
        m["meter_id"]
        for m in client.get(f"/api/batches/{body['batch_id']}/meters").json()
    ]
    check("批内表键快照不含停用键", "M-W-102" not in snap_keys, str(snap_keys))

    scope = client.get("/api/active-scope", params={"period_id": "2026-10"}).json()
    check("可对齐键不含停用表", "M-W-102" not in scope["align_meter_ids"], str(scope))
    check(
        "可对齐键全部在用",
        set(scope["align_meter_ids"]) <= {
            m["meter_id"] for m in client.get("/api/meters").json() if m["status"] == "active"
        },
        str(scope),
    )

    ov = client.get("/api/overview").json()
    active_ids = {m["meter_id"] for m in client.get("/api/meters").json() if m["status"] == "active"}
    check("总览活跃数与活跃表集合一致", ov["active_meter_count"] == len(active_ids), str(ov))
    check(
        "总览可对齐键与活跃集合同源",
        set(ov["alignable_meter_ids"]) <= active_ids,
        str(ov["alignable_meter_ids"]),
    )
    check("对齐键里没有停用表", "M-W-102" not in ov["alignable_meter_ids"], str(ov))

    # 停用表装载一律 409，且不落湖
    ra = client.post(
        "/api/actuals",
        json={
            "meter_id": "M-W-102", "period_id": "2026-10", "qty": 99,
            "occurred_at": "2026-10-29T09:00:00", "doc_no": "TEAR-1",
        },
    )
    check("停用表实抄装载 409", ra.status_code == 409, ra.text)
    check(
        "被拒装载未入湖",
        all(
            x["qty"] != 99
            for x in client.get("/api/actuals", params={"period_id": "2026-10"}).json()
            if x["meter_id"] == "M-W-102"
        ),
    )
    seal("2026-10", body["batch_id"])


def fixture_2_quarantine_detail() -> None:
    print("== 夹具 2：已进差额的停用键仍挂正式处置 ==")
    activate("M-W-102")
    # 先造一个含 M-W-102 未决差额的 open 批（2026-10 已封印，换 2026-09）
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT OR IGNORE INTO periods(period_id,status) VALUES ('2026-09','open')")
        conn.execute("UPDATE periods SET status='open', sealed_at=NULL, batch_no=NULL WHERE period_id='2026-09'")
        conn.commit()
    finally:
        conn.close()
    client.post(
        "/api/estimates",
        json={"meter_id": "M-W-102", "period_id": "2026-09", "qty": 10, "doc_no": "Q-EST"},
    )
    client.post(
        "/api/actuals",
        json={
            "meter_id": "M-W-102", "period_id": "2026-09", "qty": 8,
            "occurred_at": "2026-09-29T09:00:00", "doc_no": "Q-ACT",
        },
    )
    a = align("2026-09")
    check("隔离前对齐出该表差额", a.status_code == 200, a.text)
    bid = a.json()["batch_id"]
    before = client.get("/api/variances", params={"batch_id": bid}).json()
    target = next(v for v in before if v["meter_id"] == "M-W-102")
    check("停用前差额为未决", target["fate"] == "pending", str(target))

    r = deactivate("M-W-102")
    check("停用作废含该表的 open 批", bid in r["expired_batch_ids"], str(r))
    check(
        "停用联动把该表未决差额转隔离",
        target["variance_id"] in r["quarantined_variance_ids"],
        str(r),
    )
    after = next(
        v
        for v in client.get("/api/variances", params={"batch_id": bid}).json()
        if v["variance_id"] == target["variance_id"]
    )
    check("明细命运已是隔离", after["fate"] == "quarantine", str(after))

    # 隔离差额不得被改回正式处置
    bad = client.post(
        f"/api/variances/{target['variance_id']}/fate", json={"fate": "accept_short"}
    )
    check("停用表差额不许改回认短 409", bad.status_code == 409, bad.text)
    ok = client.post(
        f"/api/variances/{target['variance_id']}/fate", json={"fate": "quarantine"}
    )
    check("保持隔离可幂等写", ok.status_code == 200, ok.text)

    ov = client.get("/api/overview").json()
    check(
        "总览未决差额不把隔离算进去",
        all(
            v["fate"] != "pending"
            for v in client.get("/api/variances", params={"batch_id": bid}).json()
            if v["meter_id"] == "M-W-102"
        ),
        str(ov["pending_variances"]),
    )


def fixture_3_old_batch_sealable() -> None:
    print("== 夹具 3：停用后旧 open 批仍可封印 ==")
    activate("M-W-102")
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE periods SET status='open', sealed_at=NULL, batch_no=NULL WHERE period_id='2026-09'")
        conn.commit()
    finally:
        conn.close()
    a = align("2026-09")
    check("旧批对齐成功", a.status_code == 200, a.text)
    old_bid = a.json()["batch_id"]
    check("旧批含该表键", "M-W-102" in [
        m["meter_id"] for m in client.get(f"/api/batches/{old_bid}/meters").json()
    ])

    r = deactivate("M-W-102")
    check("停用令旧批失效", old_bid in r["expired_batch_ids"], str(r))
    batches = {
        b["batch_id"]: b
        for b in client.get("/api/batches", params={"period_id": "2026-09"}).json()
    }
    check("旧批状态已 expired", batches[old_bid]["status"] == "expired", str(batches[old_bid]))

    s1 = seal("2026-09", old_bid)
    check("显式指定旧批封印 409", s1.status_code == 409, s1.text)
    s2 = seal("2026-09")
    check("不带批号封印也无 open 批可封 400/409", s2.status_code in (400, 409), s2.text)
    period = next(
        p for p in client.get("/api/periods").json() if p["period_id"] == "2026-09"
    )
    check("账期仍开放，未被旧批偷偷封掉", period["status"] == "open", str(period))
    check(
        "旧批未被标 sealed",
        batches[old_bid]["status"] != "sealed",
        str(batches[old_bid]),
    )


def fixture_4_interleaved_half_batch() -> None:
    print("== 夹具 4：交错半截批（快照少键、差额挂停用键）混过预检 ==")
    # 手工造交错半截：快照只落了在用表 M-W-101，差额行却挂了停用表 M-W-102。
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT OR IGNORE INTO periods(period_id,status) VALUES ('2026-12','open')")
        conn.execute(
            "INSERT OR REPLACE INTO reconcile_batches(batch_id,period_id,status,matched_count,variance_count,inactive_meter_count,fingerprint,created_at) "
            "VALUES ('B-HALF','2026-12','open',0,1,0,'half','2026-12-01T00:00:00')"
        )
        conn.execute("DELETE FROM reconcile_batch_meters WHERE batch_id='B-HALF'")
        conn.execute("INSERT INTO reconcile_batch_meters(batch_id,meter_id) VALUES ('B-HALF','M-W-101')")
        conn.execute("DELETE FROM variances WHERE batch_id='B-HALF'")
        conn.execute(
            "INSERT INTO variances(variance_id,batch_id,meter_id,period_id,estimate_qty,actual_qty,delta,fate) "
            "VALUES ('V-HALF','B-HALF','M-W-102','2026-12',1,0,-1,'pending')"
        )
        conn.commit()
    finally:
        conn.close()
    check("半截批快照确实少键", [
        m["meter_id"] for m in client.get("/api/batches/B-HALF/meters").json()
    ] == ["M-W-101"])
    s = seal("2026-12", "B-HALF")
    check("差额挂停用键的半截批拒封 409", s.status_code == 409, s.text)
    period = next(
        p for p in client.get("/api/periods").json() if p["period_id"] == "2026-12"
    )
    check("拒封后 2026-12 仍开放", period["status"] == "open", str(period))

    # 无表键快照的批也不得静默封印
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR REPLACE INTO reconcile_batches(batch_id,period_id,status,matched_count,variance_count,inactive_meter_count,fingerprint,created_at) "
            "VALUES ('B-EMPTY','2026-12','open',1,0,0,'empty','2026-12-01T00:00:01')"
        )
        conn.execute("DELETE FROM reconcile_batch_meters WHERE batch_id='B-EMPTY'")
        conn.commit()
    finally:
        conn.close()
    s2 = seal("2026-12", "B-EMPTY")
    check("无表键快照的批拒封 409", s2.status_code == 409, s2.text)

    # 交错残留的脏 open 批：启动迁移必须强制失效
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR REPLACE INTO reconcile_batches(batch_id,period_id,status,matched_count,variance_count,inactive_meter_count,fingerprint,created_at) "
            "VALUES ('B-DIRTY','2026-12','open',0,1,0,'dirty','2026-12-01T00:00:02')"
        )
        conn.execute("INSERT INTO reconcile_batch_meters(batch_id,meter_id) VALUES ('B-DIRTY','M-W-102')")
        conn.execute("DELETE FROM variances WHERE batch_id='B-DIRTY'")
        conn.execute(
            "INSERT INTO variances(variance_id,batch_id,meter_id,period_id,estimate_qty,actual_qty,delta,fate) "
            "VALUES ('V-DIRTY','B-DIRTY','M-W-102','2026-12',1,0,-1,'pending')"
        )
        conn.commit()
    finally:
        conn.close()
    from app.seed import seed_if_empty

    seed_if_empty()
    dirty = next(
        b for b in client.get("/api/batches", params={"period_id": "2026-12"}).json()
        if b["batch_id"] == "B-DIRTY"
    )
    check("启动迁移强制失效含停用键的 open 批", dirty["status"] == "expired", str(dirty))
    dv = next(
        v for v in client.get("/api/variances", params={"batch_id": "B-DIRTY"}).json()
        if v["variance_id"] == "V-DIRTY"
    )
    check("启动迁移将停用键未决差额转隔离", dv["fate"] == "quarantine", str(dv))
    check("脏批不可封印", seal("2026-12", "B-DIRTY").status_code == 409)


def main() -> None:
    fixture_1_load_refused_but_align_pairs()
    fixture_2_quarantine_detail()
    fixture_3_old_batch_sealable()
    fixture_4_interleaved_half_batch()
    print(f"\n全部通过：{len(PASSED)} 项")


if __name__ == "__main__":
    main()

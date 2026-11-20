"""端到端核对：写架构（A）——停用挡住该表的估抄/实抄写入。

运行：METERSEAL_DATA_DIR=$(mktemp -d) .venv/bin/python tests/check_deactivation.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("METERSEAL_DATA_DIR", tempfile.mkdtemp(prefix="meterseal-check-"))

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


def deactivate(mid: str, note: str = "") -> dict:
    r = client.patch(f"/api/meters/{mid}/status", json={"status": "inactive", "note": note})
    assert r.status_code == 200, r.text
    return r.json()


def activate(mid: str) -> dict:
    r = client.patch(f"/api/meters/{mid}/status", json={"status": "active"})
    assert r.status_code == 200, r.text
    return r.json()


def post_estimate(mid: str, period: str, qty: float = 1.0) -> object:
    return client.post(
        "/api/estimates",
        json={"meter_id": mid, "period_id": period, "qty": qty, "doc_no": f"T-{mid}-{qty}"},
    )


def post_actual(mid: str, period: str, qty: float = 1.0) -> object:
    return client.post(
        "/api/actuals",
        json={
            "meter_id": mid,
            "period_id": period,
            "qty": qty,
            "occurred_at": "2026-10-29T09:00:00",
            "reader_code": "R99",
            "doc_no": f"T-{mid}-{qty}",
        },
    )


def align(period: str) -> dict:
    r = client.post("/api/align", json={"period_id": period})
    return r.json() if r.status_code == 200 else {"status_code": r.status_code, "detail": r.text}


def seal(period: str, batch_id: str | None = None):
    return client.post("/api/seal", json={"period_id": period, "batch_id": batch_id})


def main() -> None:
    print("== 基线：停用前全表对齐 ==")
    base = align("2026-10")
    check("基线对齐成功", base.get("status") == "open", str(base))
    check("基线未排除表", base["inactive_meter_count"] == 0)
    base_batch = base["batch_id"]

    print("== 停用维护入口 ==")
    res = deactivate("M-W-102", "现场拆表")
    check("停用返回 changed", res["changed"] is True)
    check("停用时作废含该表开放批", base_batch in res["expired_batch_ids"], str(res))
    batches = client.get("/api/batches", params={"period_id": "2026-10"}).json()
    b = next(x for x in batches if x["batch_id"] == base_batch)
    check("旧开放批变为 expired", b["status"] == "expired", str(b))
    audit = client.get("/api/audit", params={"meter_id": "M-W-102"}).json()
    actions = [a["action"] for a in audit]
    check("审计记录 meter.deactivate", "meter.deactivate" in actions, str(actions))
    check("审计记录 batch.expired", "batch.expired" in actions, str(actions))

    print("== 写架构：停用表估抄/实抄拒写 ==")
    ra = post_actual("M-W-102", "2026-10", 99)
    re_ = post_estimate("M-W-102", "2026-10", 99)
    check("停用表实抄拒写 409", ra.status_code == 409, ra.text)
    check("停用表估抄拒写 409", re_.status_code == 409, re_.text)
    n_act = len(client.get("/api/actuals", params={"period_id": "2026-10"}).json())
    check("停用表实抄未入湖", all(
        x["meter_id"] != "M-W-102" or x["qty"] != 99
        for x in client.get("/api/actuals", params={"period_id": "2026-10"}).json()
    ), str(n_act))

    print("== 其他表不受影响 ==")
    ro = post_actual("M-W-101", "2026-10", 12.5)
    check("在用表实抄可写", ro.status_code == 200, ro.text)
    ro2 = post_estimate("M-W-101", "2026-10", 13.0)
    check("在用表估抄可写", ro2.status_code == 200, ro2.text)

    print("== 对齐只扫在用表并快照停用计数 ==")
    a2 = align("2026-10")
    check("再对齐成功", a2.get("status") == "open", str(a2))
    check("快照 inactive_meter_count=1", a2["inactive_meter_count"] == 1, str(a2))
    check("排除清单含停用表", "M-W-102" in a2["excluded_meter_ids"], str(a2))
    vars_ = client.get("/api/variances", params={"batch_id": a2["batch_id"]}).json()
    check("差额不含停用表", all(v["meter_id"] != "M-W-102" for v in vars_), str(vars_))
    snap = client.get(f"/api/batches/{a2['batch_id']}/meters").json()
    check("批表键快照不含停用表", all(m["meter_id"] != "M-W-102" for m in snap), str(snap))

    print("== 封印可继续处理未停用表 ==")
    s2 = seal("2026-10", a2["batch_id"])
    check("不含停用表的批可封印", s2.status_code == 200, s2.text)
    check("账期已 sealed", s2.json().get("status") == "sealed")

    print("== 已 sealed 批不因事后停用被改脏 ==")
    before = next(
        x for x in client.get("/api/batches", params={"period_id": "2026-10"}).json()
        if x["batch_id"] == a2["batch_id"]
    )
    before_vars = client.get("/api/variances", params={"batch_id": a2["batch_id"]}).json()
    r3 = deactivate("M-W-101", "封印后停用")
    check("封印后停用不改动 sealed 批", r3["expired_batch_ids"] == [], str(r3))
    after = next(
        x for x in client.get("/api/batches", params={"period_id": "2026-10"}).json()
        if x["batch_id"] == a2["batch_id"]
    )
    after_vars = client.get("/api/variances", params={"batch_id": a2["batch_id"]}).json()
    check("sealed 批状态未变", before == after, f"{before} != {after}")
    check("sealed 批差额未变", before_vars == after_vars)
    period = next(
        p for p in client.get("/api/periods").json() if p["period_id"] == "2026-10"
    )
    check("账期仍密封且绑定原批", period["status"] == "sealed" and period["batch_no"] == a2["batch_id"])
    check("封印后写该期仍 409", post_actual("M-E-101", "2026-10", 1).status_code == 409)

    print("== 封印闸：批快照含停用表时拒封 ==")
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO reconcile_batches(batch_id,period_id,status,matched_count,variance_count,inactive_meter_count,fingerprint,created_at) "
            "VALUES ('B-GATE','2026-09','open',1,0,0,'deadbeef','2026-09-30T00:00:00')"
        )
        conn.execute("INSERT INTO reconcile_batch_meters(batch_id,meter_id) VALUES ('B-GATE','M-W-102')")
        conn.execute("COMMIT")
    finally:
        conn.close()
    sg = seal("2026-09", "B-GATE")
    check("含停用表表键的批拒封 409", sg.status_code == 409, sg.text)
    check("拒封后账期仍开放", next(
        p for p in client.get("/api/periods").json() if p["period_id"] == "2026-09"
    )["status"] == "open")

    print("== 解除停用后可写可封；旧半截批不复活 ==")
    activate("M-W-102")
    check("解除后可写该表实抄", post_actual("M-W-102", "2026-09", 7).status_code == 200)
    check("解除后可写该表估抄", post_estimate("M-W-102", "2026-09", 7).status_code == 200)
    a3 = align("2026-09")
    check("解除后可重新对齐出批", a3.get("status") == "open", str(a3))
    check("重新对齐含解除表", "M-W-102" not in a3.get("excluded_meter_ids", []), str(a3))
    s3 = seal("2026-09", a3["batch_id"])
    check("解除后可封印", s3.status_code == 200, s3.text)
    old = next(
        x for x in client.get("/api/batches", params={"period_id": "2026-10"}).json()
        if x["batch_id"] == base_batch
    )
    check("停用作废的旧批仍为 expired（不复活）", old["status"] == "expired", str(old))
    gate = next(
        x for x in client.get("/api/batches", params={"period_id": "2026-09"}).json()
        if x["batch_id"] == "B-GATE"
    )
    check("被拒封的旧半截批未被打进 sealed", gate["status"] != "sealed", str(gate))
    period9 = next(
        p for p in client.get("/api/periods").json() if p["period_id"] == "2026-09"
    )
    check("2026-09 绑定的是新批而非半截批", period9["batch_no"] == a3["batch_id"], str(period9))

    print("== 总览停用计数与清单 ==")
    ov = client.get("/api/overview").json()
    check("总览有停用计数", ov["inactive_meter_count"] == 1, str(ov))
    check("总览停用清单正确", [m["meter_id"] for m in ov["inactive_meters"]] == ["M-W-101"], str(ov["inactive_meters"]))
    check("总览在用计数正确", ov["active_meter_count"] == 5, str(ov))

    print("== 重启不把已排除的停用表键塞回新批 ==")
    from app.seed import seed_if_empty

    # 新账期：停用表曾有历史读数，但当前开放批按写架构排除了它
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT OR IGNORE INTO periods(period_id,status) VALUES ('2026-11','open')")
        conn.execute("COMMIT")
    finally:
        conn.close()
    activate("M-W-102")
    activate("M-W-101")
    assert post_estimate("M-W-102", "2026-11", 5).status_code == 200
    assert post_actual("M-W-102", "2026-11", 5).status_code == 200
    assert post_actual("M-W-101", "2026-11", 5).status_code == 200
    deactivate("M-W-102")
    again_align = align("2026-11")
    check("新账期对齐成功", again_align.get("status") == "open", str(again_align))
    snap_before = client.get(f"/api/batches/{again_align['batch_id']}/meters").json()
    seed_if_empty()  # 模拟服务重启触发迁移
    snap_after = client.get(f"/api/batches/{again_align['batch_id']}/meters").json()
    check("重启前批快照不含停用表", all(m["meter_id"] != "M-W-102" for m in snap_before), str(snap_before))
    check("重启后批快照不复活停用表键", snap_before == snap_after, f"{snap_before} != {snap_after}")
    check("该合法批重启后仍可封印", seal("2026-11", again_align["batch_id"]).status_code == 200)
    activate("M-W-102")

    print("== 幂等：重复停用不重复作废 ==")
    first = deactivate("M-W-101")
    again = deactivate("M-W-101")
    check("首次停用 changed=True", first["changed"] is True, str(first))
    check("重复停用 changed=False", again["changed"] is False, str(again))
    check("重复停用不再作废批", again["expired_batch_ids"] == [], str(again))

    print(f"\n全部通过：{len(PASSED)} 项")


if __name__ == "__main__":
    main()

"""并发核对：停用/解除 与 封印 在同一账期上赛跑，结果只允许两种终态之一。

运行：METERSEAL_DATA_DIR=$(mktemp -d) .venv/bin/python tests/check_concurrency.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("METERSEAL_DATA_DIR", tempfile.mkdtemp(prefix="meterseal-conc-"))

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn  # noqa: E402

from app.main import app  # noqa: E402

import httpx  # noqa: E402

HOST, PORT = "127.0.0.1", 18765
BASE = f"http://{HOST}:{PORT}/api"


def start_server():
    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="error")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(200):
        try:
            if httpx.get(f"{BASE}/health", timeout=0.3).status_code == 200:
                return server
        except Exception:
            time.sleep(0.05)
    raise RuntimeError("服务未启动")


def run_races(rounds: int = 25) -> None:
    c = httpx.Client(base_url=BASE, timeout=10.0)
    for i in range(rounds):
        period = "2026-10"
        # 每轮重置：建一个含 M-E-202 的开放批
        assert c.patch("/meters/M-E-202/status", json={"status": "active"}).status_code == 200
        align = c.post("/align", json={"period_id": period}).json()
        if align.get("status") != "open":
            # 账期可能已被上一轮封印；重置状态便于反复赛跑
            import sqlite3
            from app.config import DB_PATH

            raw = sqlite3.connect(DB_PATH)
            raw.execute("UPDATE periods SET status='open', sealed_at=NULL, batch_no=NULL WHERE period_id=?", (period,))
            raw.execute("UPDATE reconcile_batches SET status='expired' WHERE period_id=?", (period,))
            raw.commit()
            raw.close()
            align = c.post("/align", json={"period_id": period}).json()
        batch_id = align["batch_id"]
        if i % 2 == 0:
            assert c.post("/align", json={"period_id": period}).json()["status"] == "open"

        results: dict[str, httpx.Response] = {}

        def do_deact():
            results["status"] = c.patch(
                "/meters/M-E-202/status", json={"status": "inactive", "note": "race"}
            )

        def do_seal():
            results["seal"] = c.post("/seal", json={"period_id": period, "batch_id": batch_id})

        t1 = threading.Thread(target=do_deact)
        t2 = threading.Thread(target=do_seal)
        t1.start(); t2.start(); t1.join(); t2.join()

        seal_r = results["seal"]
        meter_row = next(m for m in c.get("/meters").json() if m["meter_id"] == "M-E-202")
        period_row = next(p for p in c.get("/periods").json() if p["period_id"] == period)
        snap = c.get(f"/batches/{batch_id}/meters").json()

        if seal_r.status_code == 200 and period_row["status"] == "sealed" and period_row["batch_no"] == batch_id:
            # 封印成功：批内表键在封印时刻必须是 active。事后停用允许存在（sealed 不被改写）。
            sealed_batch = next(b for b in c.get("/batches", params={"period_id": period}).json() if b["batch_id"] == batch_id)
            assert sealed_batch["status"] == "sealed", sealed_batch
            # 停用动作只能作废 open 批，不得把 sealed 批改掉
            assert period_row["batch_no"] == batch_id, period_row
            keys = [m["meter_id"] for m in snap]
            assert batch_id in {b["batch_id"] for b in c.get("/batches", params={"period_id": period}).json() if b["status"] == "sealed"}
            assert "M-E-202" in keys, snap
        elif seal_r.status_code == 409:
            assert period_row["status"] == "open", (seal_r.text, period_row)
            assert all(b["status"] != "sealed" for b in c.get("/batches", params={"period_id": period}).json())
            assert meter_row["status"] == "inactive", meter_row
        else:
            raise AssertionError(f"未预期的封印结果: {seal_r.status_code} {seal_r.text}")

        c.patch("/meters/M-E-202/status", json={"status": "active"})

    print(f"并发赛跑 {rounds} 轮：终态均为「封印成功且批快照完整」或「拒封且账期仍开放」")


if __name__ == "__main__":
    server = start_server()
    try:
        run_races()
    finally:
        server.should_exit = True
    print("并发核对通过")

from __future__ import annotations
from app.db import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS meters (
  meter_id TEXT PRIMARY KEY,
  building TEXT NOT NULL,
  unit_label TEXT NOT NULL,
  meter_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS periods (
  period_id TEXT PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'open',
  sealed_at TEXT,
  batch_no TEXT
);
CREATE TABLE IF NOT EXISTS estimates (
  estimate_id TEXT PRIMARY KEY,
  meter_id TEXT NOT NULL,
  period_id TEXT NOT NULL,
  qty REAL NOT NULL,
  doc_no TEXT NOT NULL,
  loaded_at TEXT NOT NULL,
  UNIQUE(meter_id, period_id, doc_no)
);
CREATE TABLE IF NOT EXISTS actuals (
  actual_id TEXT PRIMARY KEY,
  meter_id TEXT NOT NULL,
  period_id TEXT NOT NULL,
  qty REAL NOT NULL,
  occurred_at TEXT NOT NULL,
  reader_code TEXT NOT NULL,
  doc_no TEXT NOT NULL,
  loaded_at TEXT NOT NULL,
  UNIQUE(meter_id, period_id, doc_no)
);
CREATE TABLE IF NOT EXISTS reconcile_batches (
  batch_id TEXT PRIMARY KEY,
  period_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  matched_count INTEGER NOT NULL DEFAULT 0,
  variance_count INTEGER NOT NULL DEFAULT 0,
  inactive_meter_count INTEGER NOT NULL DEFAULT 0,
  fingerprint TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reconcile_batch_meters (
  batch_id TEXT NOT NULL,
  meter_id TEXT NOT NULL,
  PRIMARY KEY (batch_id, meter_id)
);
CREATE TABLE IF NOT EXISTS variances (
  variance_id TEXT PRIMARY KEY,
  batch_id TEXT NOT NULL,
  meter_id TEXT NOT NULL,
  period_id TEXT NOT NULL,
  estimate_qty REAL,
  actual_qty REAL,
  delta REAL NOT NULL,
  fate TEXT NOT NULL DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
  audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  meter_id TEXT,
  period_id TEXT,
  batch_id TEXT,
  detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_meter ON audit_log(meter_id, audit_id DESC);
CREATE INDEX IF NOT EXISTS idx_audit_period ON audit_log(period_id, audit_id DESC);
"""

# 旧库补列/补表：CREATE TABLE IF NOT EXISTS 不会给已存在的表加列。
MIGRATIONS = [
    "ALTER TABLE reconcile_batches ADD COLUMN inactive_meter_count INTEGER NOT NULL DEFAULT 0",
]


def _migrate(conn) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(reconcile_batches)")}
    if "inactive_meter_count" not in cols:
        for stmt in MIGRATIONS:
            try:
                conn.execute(stmt)
            except Exception:
                pass
    # 旧批没有表键快照：只对「完全没有快照」的批按当时的估抄/实抄补一次，
    # 且只补在用表——停用键不得借迁移复活进任何批。
    # 不能每次启动重算，否则会把对齐时已排除的停用表键重新塞回新批。
    conn.execute(
        """INSERT OR IGNORE INTO reconcile_batch_meters(batch_id, meter_id)
           SELECT b.batch_id, u.meter_id FROM reconcile_batches b
           JOIN (
             SELECT period_id, meter_id FROM estimates
             UNION SELECT period_id, meter_id FROM actuals
           ) u ON u.period_id = b.period_id
           JOIN meters mt ON mt.meter_id = u.meter_id AND mt.status='active'
           WHERE NOT EXISTS (
             SELECT 1 FROM reconcile_batch_meters s WHERE s.batch_id = b.batch_id
           )"""
    )
    # 修复历史脏状态：open 批的「表键快照」或「差额挂的表键」含停用表即强制失效，
    # 其未决差额转隔离。让「含停用键的 open 批不得存在」成为启动后恒成立的不变量。
    dirty = conn.execute(
        """SELECT DISTINCT b.batch_id FROM reconcile_batches b
           WHERE b.status='open' AND (
             EXISTS (
               SELECT 1 FROM reconcile_batch_meters m
               JOIN meters mt ON mt.meter_id=m.meter_id
               WHERE m.batch_id=b.batch_id AND mt.status<>'active'
             )
             OR EXISTS (
               SELECT 1 FROM variances v
               JOIN meters mt ON mt.meter_id=v.meter_id
               WHERE v.batch_id=b.batch_id AND mt.status<>'active'
             )
           )"""
    ).fetchall()
    for r in dirty:
        bid = r["batch_id"]
        conn.execute(
            "UPDATE reconcile_batches SET status='expired' WHERE batch_id=?", (bid,)
        )
        conn.execute(
            """UPDATE variances SET fate='quarantine'
               WHERE batch_id=? AND fate='pending'
                 AND meter_id IN (
                   SELECT meter_id FROM meters WHERE status<>'active'
                 )""",
            (bid,),
        )
        conn.execute(
            """INSERT INTO audit_log(at,actor,action,batch_id,detail)
               VALUES (datetime('now'),'system','batch.expired',?,
                       '启动迁移：含停用表键的开放批强制失效')""",
            (bid,),
        )
    conn.commit()


def seed_if_empty() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        n = conn.execute("SELECT COUNT(*) AS c FROM meters").fetchone()["c"]
        if n:
            return
        meters = [
            ("M-W-101", "A栋", "101", "water"),
            ("M-W-102", "A栋", "102", "water"),
            ("M-E-101", "A栋", "101", "electric"),
            ("M-W-201", "B栋", "201", "water"),
            ("M-E-201", "B栋", "201", "electric"),
            ("M-E-202", "B栋", "202", "electric"),
        ]
        conn.executemany(
            "INSERT INTO meters(meter_id,building,unit_label,meter_type,status) VALUES (?,?,?,?, 'active')",
            meters,
        )
        conn.execute(
            "INSERT INTO periods(period_id,status) VALUES ('2026-10','open')"
        )
        conn.execute(
            "INSERT INTO periods(period_id,status) VALUES ('2026-09','open')"
        )
        # estimates for 2026-10
        est = [
            ("E1", "M-W-101", "2026-10", 12.0, "EST-101", "2026-10-01T08:00:00"),
            ("E2", "M-W-102", "2026-10", 15.0, "EST-102", "2026-10-01T08:00:00"),
            ("E3", "M-E-101", "2026-10", 220.0, "EST-E101", "2026-10-01T08:00:00"),
            ("E4", "M-W-201", "2026-10", 18.0, "EST-201", "2026-10-01T08:00:00"),
            ("E5", "M-E-201", "2026-10", 310.0, "EST-E201", "2026-10-01T08:00:00"),
            ("E6", "M-E-202", "2026-10", 280.0, "EST-E202", "2026-10-01T08:00:00"),
        ]
        conn.executemany(
            "INSERT INTO estimates(estimate_id,meter_id,period_id,qty,doc_no,loaded_at) VALUES (?,?,?,?,?,?)",
            est,
        )
        # actuals: most match; two variance
        act = [
            ("A1", "M-W-101", "2026-10", 12.0, "2026-10-28T10:00:00", "R01", "ACT-101", "2026-10-28T10:05:00"),
            ("A2", "M-W-102", "2026-10", 14.0, "2026-10-28T10:10:00", "R01", "ACT-102", "2026-10-28T10:15:00"),  # short
            ("A3", "M-E-101", "2026-10", 220.0, "2026-10-28T11:00:00", "R02", "ACT-E101", "2026-10-28T11:05:00"),
            ("A4", "M-W-201", "2026-10", 18.0, "2026-10-28T11:20:00", "R02", "ACT-201", "2026-10-28T11:25:00"),
            ("A5", "M-E-201", "2026-10", 330.0, "2026-10-28T12:00:00", "R03", "ACT-E201", "2026-10-28T12:05:00"),  # over
            ("A6", "M-E-202", "2026-10", 280.0, "2026-10-28T12:20:00", "R03", "ACT-E202", "2026-10-28T12:25:00"),
        ]
        conn.executemany(
            "INSERT INTO actuals(actual_id,meter_id,period_id,qty,occurred_at,reader_code,doc_no,loaded_at) VALUES (?,?,?,?,?,?,?,?)",
            act,
        )
        conn.execute(
            "INSERT INTO settings(key,value) VALUES ('current_period','2026-10')"
        )
        conn.execute(
            "INSERT INTO settings(key,value) VALUES ('product_title','抄表簿')"
        )
        # 界面不露英文：表类型仍存 water/electric 码，前端映射为中文
        conn.commit()
    finally:
        conn.close()

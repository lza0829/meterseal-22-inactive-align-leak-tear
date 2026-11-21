# 抄表簿

骨架：抄表读数装载 → 估抄/实抄对齐 → 差额处置 → 账期封印。

- 前端 http://127.0.0.1:6010（勿用 6000，浏览器会拦）
- 接口 http://127.0.0.1:11000

## 启动

```powershell
cd backend
C:\Users\18435\AppData\Roaming\uv\python\cpython-3.11.15-windows-x86_64-none\python.exe -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 11000 --app-dir .
```

```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 6010
```

## 表具停用口径（写架构）

全局只贯彻一种：**以现行活跃表具集合为准**。停用生效后，该表既挡装载，也即刻退出对齐键集合。

- 停用维护入口：表具页「停用 / 恢复在用」，调 `PATCH /api/meters/{id}/status`；只在前端禁用无效。
- 装载写闸：停用生效后该 `meter_id` 的估抄/实抄装载一律 409，其他表不受影响。
- 对齐过滤：只扫描在用表；对账批记 `inactive_meter_count` 与 `excluded_meter_ids`，并存批内表键快照。
  停用表的旧读数仍在湖（审计可追），但不再出估抄/实抄配对。
- 旧批失效：停用即作废所有含该表表键的 **open** 批；该表在这些批里 `fate='pending'` 的差额同事务转 `quarantine`（隔离），
  已决差额不动。已 sealed 批不改写。
- 差额命运：停用表的差额只能保持隔离，接口拒绝把它改回认短/认溢，防止停用键重新挂回正式处置。
- 封印闸：同一写事务内重读，批内表键快照 **与** 该批差额挂的表键须全部在用；任一处含停用表即拒封。
  无表键快照的批同样拒封（不得绕过预检静默封印）。
- 启动迁移：只给「完全没有快照」的旧批补键，且只补在用表；含停用键的脏 open 批强制失效并转隔离。
- 解除停用：可写可重新对齐可封；被作废的旧半截批不会复活。
- 总览：活跃数、停用数、可对齐键同源于 `meters.status`，不另起一套。
- 审计：`GET /api/audit?meter_id=&period_id=&batch_id=`，记录状态变更、作废、隔离、装载、对齐、封印。
- 并发：停用维护 / 装载 / 对齐 / 封印共用 `BEGIN IMMEDIATE` 写事务，不会半截把停用表打进 sealed。

核对脚本（各自用临时库，不碰 `data/`）：

```bash
cd backend
METERSEAL_DATA_DIR=$(mktemp -d) .venv/bin/python tests/check_deactivation.py
METERSEAL_DATA_DIR=$(mktemp -d) .venv/bin/python tests/check_tear.py
METERSEAL_DATA_DIR=$(mktemp -d) .venv/bin/python tests/check_concurrency.py
```

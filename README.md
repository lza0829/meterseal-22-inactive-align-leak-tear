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

全局只贯彻一种：**停用挡住该表的估抄与实抄写入**。

- 停用维护入口：表具页「停用 / 恢复在用」，调 `PATCH /api/meters/{id}/status`；只在前端禁用无效。
- 装载写闸：停用生效后该 `meter_id` 的估抄/实抄装载一律 409，其他表不受影响。
- 对齐过滤：只扫描在用表；对账批记 `inactive_meter_count` 与 `excluded_meter_ids`，并存批内表键快照。
- 封印闸：批内表键须全部在用，含停用表则拒封；停用会作废含该表的**开放**批，已 sealed 批不改写。
- 解除停用：可写可重新对齐可封；被作废的旧半截批不会复活。
- 审计：`GET /api/audit?meter_id=&period_id=&batch_id=`，记录状态变更、作废、装载、对齐、封印。
- 总览：展示停用计数与停用表清单。
- 并发：停用维护 / 装载 / 对齐 / 封印共用 `BEGIN IMMEDIATE` 写事务，不会半截把停用表打进 sealed。

核对脚本（各自用临时库，不碰 `data/`）：

```bash
cd backend
METERSEAL_DATA_DIR=$(mktemp -d) .venv/bin/python tests/check_deactivation.py
METERSEAL_DATA_DIR=$(mktemp -d) .venv/bin/python tests/check_concurrency.py
```

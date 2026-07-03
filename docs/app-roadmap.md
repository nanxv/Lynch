# App 化路线图

## 已完成（Phase 1 — 后端 API）

- SQLite 数据库：`data/stock_monitor.db`
  - `watchlist` — 股票池（替代 `watchlist.yaml`）
  - `alert_history` — 信号日志
  - `alert_dedup` — 24h 去重
  - `push_tokens` — Expo Push 设备 token
- FastAPI REST API（`scripts/run_api.sh` 启动）
- Expo Push 推送通道（与 Telegram 并行）
- 首次启动自动从 `watchlist.yaml` 种子导入

### API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/watchlist` | 股票池列表 |
| POST | `/api/watchlist` | 添加股票 |
| DELETE | `/api/watchlist/{ticker}` | 删除股票 |
| GET | `/api/status?market=ALL` | 实时雷达状态 |
| GET | `/api/alerts` | 历史信号 |
| POST | `/api/scan` | 手动触发扫描 |
| POST | `/api/push-tokens` | 注册 Expo token |

### 本地启动

```bash
cd ~/Projects/stock-monitor
.venv/bin/pip install -r requirements.txt
chmod +x scripts/run_api.sh
./scripts/run_api.sh
# 文档: http://127.0.0.1:8000/docs
```

## 待做（Phase 2 — Expo 前端）

- `mobile/` Expo + TypeScript 三 Tab UI
- 雷达仪表盘 / 股票池管理 / 信号日志
- 注册 Expo Push Token → `POST /api/push-tokens`

## 待做（Phase 3 — 云端部署）

- 后端部署到 Railway / Render / EC2
- SQLite → Supabase PostgreSQL（可选）
- 云端 Cron 替代 Mac crontab
- APNs 经 Expo 自动路由，无需单独证书（开发阶段）

# 【铁律 2.5 版】量化监控 App — 产品与技术规格说明书

| 字段 | 内容 |
|------|------|
| 文档版本 | v0.2.1 |
| 状态 | 铁律 2.5 App 已实现；Lynch Agent 生产运行中 |
| 最后更新 | 2026-07-06 |
| 代码仓库 | `~/Projects/stock-monitor` |
| 审阅目的 | 确认产品边界、策略逻辑、技术架构与上线路径 |

---

## 1. 执行摘要

本产品是一套**前后端分离的股票监控与买点警报系统**，核心目标是将「铁律 2.5」量化策略从人工盯盘升级为 7×24 自动化执行，并通过 iOS App 提供战术级仪表盘与原生推送。

**系统分工：**

| 角色 | 职责 |
|------|------|
| 人脑 | 产业趋势、公司基本面、最终是否下单 |
| 机器 | 规则过滤、均线计算、信号触发、去重推送 |

**当前实现阶段：**

- ✅ Phase 0：CLI 脚本 + Telegram + Mac Cron（已跑通）
- ✅ Phase 1：FastAPI 后端 + SQLite + REST API（已跑通）
- ✅ Phase 2：Expo iOS App 雏形（三 Tab，已开发，待本机 Node 环境验证）
- ⏳ Phase 3：云端部署 + TestFlight / 正式包（未开始）

---

## 2. 产品目标与非目标

### 2.1 产品目标

1. **消除盯盘时间**：按预设时间表自动扫描股票池，无需人工刷新行情。
2. **纪律化执行**：所有买点信号由量化规则产生，剥离盘中情绪。
3. **双市场覆盖**：日股与美股分别判断大盘跌势，物理隔离系统性风险。
4. **移动端第一时间触达**：通过 Push Notification 在户外/睡眠场景收到信号。
5. **可配置股票池**：App 端增删股票，实时同步后端大脑。

### 2.2 非目标（明确不做）

| 非目标 | 说明 |
|--------|------|
| 自动下单 | 不在 SBI / 任何券商 API 上自动买卖 |
| 投资建议 | 系统只输出「规则触发」，不构成投资建议 |
| 实时 tick 级行情 | 基于 yfinance 日线/周线，非 Level 2 行情 |
| 复杂技术分析 | 仅双均线 + 偏离度，不含 MACD/RSI 等 |
| 多用户 SaaS | v0.2 为个人/家庭使用，无账号体系 |

---

## 3. 用户画像与核心场景

### 3.1 主要用户

**用户 A（你）**

- 使用 SBI 证券，资金覆盖日股 + 美股
- 有明确 Tier 1 核心池与 Tier 2 常规池区分
- 经常在户外（长跑、自驾），需要手机 Push
- 物理时区：东京 (JST)

**用户 B（配偶，可选）**

- 共用同一股票池与信号，或未来独立 watchlist
- 仅需收 Push + 查看雷达，不一定操作后端

### 3.2 用户故事

| ID | 故事 | 验收标准 |
|----|------|----------|
| US-01 | 作为投资者，我希望每天早上看到日股雷达状态 | App Tab1 展示全部标的红/黄/绿状态 |
| US-02 | 作为投资者，当股价触发铁律买点时，我希望手机立即弹窗 | Expo Push 在 30 秒内送达（云端部署后） |
| US-03 | 作为投资者，我希望同一信号 24h 内不重复轰炸 | dedup 表生效，skipped 计数正确 |
| US-04 | 作为投资者，我希望在 App 里添加/删除监控股票 | POST/DELETE watchlist 后雷达即时反映 |
| US-05 | 作为投资者，我希望复盘历史信号 | Tab3 时间轴展示 alert_history |
| US-06 | 作为投资者，我希望美股在 JST 凌晨自动扫描 | Cron + ET 时间守卫在收盘前 30 分钟触发 |
| US-07 | 作为投资者，我希望高位情绪股被自动过滤 | D > 15% 显示红色「危险区」，不产生买入信号 |

---

## 4. 系统架构

### 4.1 宏观架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        云端 / 本地 Mac                           │
│  ┌──────────────┐   Cron    ┌─────────────────────────────┐  │
│  │ 定时任务      │ ────────► │ Python 后端大脑               │  │
│  │ (日股/美股)   │           │  strategy.py  铁律 2.5       │  │
│  └──────────────┘           │  data.py      yfinance       │  │
│                              │  scan_service 扫描+去重+推送  │  │
│                              │  FastAPI       REST API       │  │
│                              │  SQLite        持久化         │  │
│                              └───────────┬───────────────────┘  │
└──────────────────────────────────────────┼──────────────────────┘
                                           │ HTTPS / HTTP
              ┌────────────────────────────┼────────────────────────┐
              │                            │                        │
              ▼                            ▼                        ▼
     ┌────────────────┐         ┌─────────────────┐    ┌──────────────┐
     │ Expo iOS App   │         │ Expo Push API   │    │ Telegram Bot │
     │ 三 Tab 控制台   │         │ → APNs 原生弹窗  │    │ (过渡期保留)  │
     └────────────────┘         └─────────────────┘    └──────────────┘
```

### 4.2 技术栈

| 层级 | 技术 | 版本/说明 |
|------|------|-----------|
| 策略引擎 | Python 3.12 | 复用 `strategy.py` / `data.py` |
| 后端 API | FastAPI + Uvicorn | OpenAPI 文档 `/docs` |
| 数据库 | SQLite（本地）→ PostgreSQL/Supabase（云端） | `data/stock_monitor.db` |
| 行情数据 | yfinance | 前复权 Adj Close |
| 移动端 | Expo SDK 52 + React Native + TypeScript | Expo Router Tabs |
| 推送 | Expo Push Notification Service | 经 APNs 到 iOS |
| 备用推送 | Telegram Bot API | 多 chat_id 支持 |

### 4.3 目录结构

```
stock-monitor/
├── config.yaml              # 策略参数（均线、阈值）
├── watchlist.yaml           # 种子数据（首次导入 DB 后弃用）
├── data/stock_monitor.db    # SQLite 数据库
├── src/
│   ├── strategy.py          # 铁律 2.5 三阶段逻辑
│   ├── data.py              # yfinance 数据层
│   ├── api/app.py           # FastAPI 路由
│   ├── db/                  # 数据库访问层
│   └── services/            # 扫描编排、Expo Push
├── scripts/
│   ├── run_api.sh           # 启动 API
│   └── run_us_close_scan.py # 美股 ET 时间守卫
├── mobile/                  # Expo iOS App
│   └── app/(tabs)/          # 三 Tab 页面
└── crontab.example          # 定时任务模板
```

---

## 5. 核心策略规格（铁律 2.5）

### 5.1 符号定义

| 符号 | 含义 | 默认参数 |
|------|------|----------|
| P | 股票最新收盘价（日线最后一根） | — |
| MA_w | 10 周简单移动平均（周线 Adj Close） | `ma_weeks: 10` |
| MA_d | 20 日简单移动平均（日线 Adj Close） | `ma_days: 20` |
| D | 周线偏离度 = (P − MA_w) / MA_w × 100% | — |
| Tier 1 | 核心池：基本面反转 / 护城河公司 | 跳过日线检测 |
| Tier 2 | 常规池：普通优质公司 | 须通过日线战术条件 |

### 5.2 三阶段业务流（严格顺序）

```
对所有 watchlist 股票
        │
        ▼
┌───────────────────────┐
│ 第一阶段：周线宏观排雷  │
│ D > 15%  → 剔除（危险区）│
│ D ≤ 15%  → 进入伏击圈   │
└───────────┬───────────┘
            │
     ┌──────┴──────┐
     │             │
  Tier 1        Tier 2
     │             │
     ▼             ▼
┌─────────┐  ┌─────────────────────┐
│第三阶段   │  │第二阶段：日线战术触发  │
│核心池旁路 │  │条件：                │
│−2%≤D≤2% │  │1. 大盘跌势           │
│→核心阻击  │  │2. P≤MA_d 或触及 MA_d │
└─────────┘  │→常规买入             │
             └─────────────────────┘
```

### 5.3 大盘跌势定义

按标的所属市场**分别**判断：

| 市场 | 指数代码 | 跌势条件 |
|------|----------|----------|
| JP | `^N225`（日经 225） | 指数收盘价 < 指数 20 日均线 |
| US | `^GSPC`（标普 500） | 指数收盘价 < 指数 20 日均线 |

### 5.4 UI 状态映射

| 内部 alert_type | ui_status | 显示 | 含义 |
|-----------------|-----------|------|------|
| `excluded` 且 D > 15% | `danger` | 🔴 危险区 | 情绪溢价过高，不监控 |
| `watching` / `excluded`(D≤15%) | `ambush` | 🟡 伏击圈 | 已进入周线范围，等待触发 |
| `tier1_core` / `tier2_buy` | `signal` | 🟢 绝佳买点 | 规则触发，应推送 |
| 数据拉取失败 | `error` | ⚪ 数据异常 | 不产生任何买入信号 |

### 5.5 信号类型与推送文案

| 信号 | 触发条件 | 推送标题示例 |
|------|----------|--------------|
| 核心阻击 | Tier 1 + −2% ≤ D ≤ 2% | `核心阻击: 4063.T` |
| 常规买入 | Tier 2 + 大盘跌势 + 击穿 MA_d | `常规买入: AMD` |

每条推送底部固定附加：

> *系统提示：请核对账户可用现金流，如资金不足请考虑 S株/碎股 购买或放弃本次信号。*

### 5.6 边界条件

| 场景 | 系统行为 |
|------|----------|
| yfinance 数据缺失 | 标记 `error`，**禁止**产生买入信号 |
| 除权除息 | 使用 Adj Close 计算均线，避免假跌破 |
| 同一股票 24h 内重复触发 | dedup 抑制 Push/Telegram，终端/App 仍可显示状态 |
| 周线数据不足 10 根 | 抛出 DataFetchError，不产生信号 |
| 日线数据不足 20 根 | 同上 |

### 5.7 可配置参数（`config.yaml`）

```yaml
strategy:
  ma_weeks: 10
  ma_days: 20
  deviation_exclude_pct: 15.0      # 高位排雷阈值
  tier1_deviation_min_pct: -2.0      # 核心池下限
  tier1_deviation_max_pct: 2.0       # 核心池上限

notifications:
  dedup_ttl_hours: 24                # 去重窗口
```

**审阅点：** 以上参数目前仅能通过 YAML 修改，App 内尚不可调。是否需要在 v0.3 加入「策略设置」页？

---

## 6. 数据规格

### 6.1 行情数据源

| 项目 | 规格 |
|------|------|
| 提供商 | Yahoo Finance（`yfinance` Python 库） |
| 日股代码格式 | `4063.T`（东京证券交易所） |
| 美股代码格式 | `AMD` |
| 价格字段 | Adj Close 优先，fallback Close |
| 周线周期 | `interval=1wk`，回溯 5 年 |
| 日线周期 | `interval=1d`，回溯 2 年 |
| 延迟 | 免费数据源，通常延迟 15 分钟 ~ 数小时，**非实时** |

### 6.2 数据库 Schema（SQLite）

#### `watchlist` — 股票池

| 列 | 类型 | 说明 |
|----|------|------|
| ticker | TEXT PK | 股票代码 |
| name | TEXT | 显示名称 |
| market | TEXT | `JP` / `US` |
| tier | INTEGER | `1` 或 `2` |
| note | TEXT | 备注（基本面逻辑） |
| created_at | TEXT ISO8601 | 创建时间 |

#### `alert_history` — 信号日志

| 列 | 类型 | 说明 |
|----|------|------|
| id | INTEGER PK | 自增 |
| ticker, name, market, tier | — | 快照 |
| alert_type | TEXT | `tier1_core` / `tier2_buy` |
| title, body | TEXT | 推送内容 |
| price, deviation_pct | REAL | 触发时快照 |
| created_at | TEXT | 触发时间 |

#### `alert_dedup` — 去重缓存

| 列 | 类型 | 说明 |
|----|------|------|
| cache_key | TEXT PK | 格式 `{ticker}:buy` |
| sent_at | TEXT | 上次推送时间 |

#### `push_tokens` — 设备推送

| 列 | 类型 | 说明 |
|----|------|------|
| id | INTEGER PK | 自增 |
| expo_push_token | TEXT UNIQUE | Expo 设备 token |
| device_label | TEXT | 如 `iPhone 15 Pro` |
| created_at | TEXT | 注册时间 |

### 6.3 种子数据（当前测试池）

| Ticker | 名称 | Market | Tier | 用途 |
|--------|------|--------|------|------|
| 4063.T | 信越化学 | JP | 1 | 验证核心池周线回踩 |
| AMD | AMD | US | 2 | 验证常规池战术血坑 |
| 6859.T | TOWA | JP | 2 | 验证常规池 + 高位排雷 |

---

## 7. API 规格

**Base URL：** `http://<host>:8000`  
**认证：** 无（v0.2 个人使用，局域网/待部署）  
**CORS：** `allow_origins: *`（开发阶段）

### 7.1 端点一览

| 方法 | 路径 | 说明 | 状态 |
|------|------|------|------|
| GET | `/api/health` | 健康检查 + watchlist 数量 | ✅ 已实现 |
| GET | `/api/watchlist` | 获取股票池 | ✅ |
| POST | `/api/watchlist` | 添加/更新股票 | ✅ |
| DELETE | `/api/watchlist/{ticker}` | 删除股票 | ✅ |
| GET | `/api/status?market=ALL\|JP\|US` | 实时雷达状态（触发 yfinance 拉取） | ✅ |
| GET | `/api/alerts?limit=100` | 历史信号 | ✅ |
| POST | `/api/scan` | 手动触发扫描 + 推送 | ✅ |
| POST | `/api/push-tokens` | 注册 Expo Push Token | ✅ |
| DELETE | `/api/push-tokens?expo_push_token=` | 注销 Token | ✅ |

### 7.2 关键响应示例

**GET `/api/status` — StatusItem**

```json
{
  "ticker": "4063.T",
  "name": "信越化学",
  "market": "JP",
  "tier": 1,
  "ui_status": "signal",
  "alert_type": "tier1_core",
  "price": 7307,
  "ma_weekly": 7200,
  "ma_daily": 7334,
  "deviation_pct": 1.5,
  "daily_gap_pct": -0.4,
  "market_index": "日经 225",
  "market_is_downtrend": false,
  "message": "【核心标的阻击警报】周线偏离 +1.5% ..."
}
```

**POST `/api/watchlist` — 请求体**

```json
{
  "ticker": "7203.T",
  "name": "丰田汽车",
  "market": "JP",
  "tier": 2,
  "note": "可选备注"
}
```

### 7.3 性能预期

| 端点 | 预期延迟 | 说明 |
|------|----------|------|
| `/api/watchlist` | < 50ms | 纯 DB 读取 |
| `/api/status` | 5–30s | 每只票 2 次 yfinance 请求 + 指数 1 次 |
| `/api/scan` | 同 status + 推送 | 不建议高频调用 |

**审阅点：** `/api/status` 每次请求都实时拉行情，未做缓存。是否接受？或需加 5 分钟 TTL 缓存？

---

## 8. 移动端 App 规格

### 8.1 设计原则

- **战术仪表盘风格**：深色背景（`#0B0F14`），高对比红/黄/绿
- **信息密度适中**：一屏看清状态，不需点进详情
- **非花哨**：无 K 线图、无社交、无新闻聚合

### 8.2 导航结构

```
App
└── Tab Navigator
    ├── Tab 1: 狙击雷达 (index)
    ├── Tab 2: 股票池   (watchlist)
    └── Tab 3: 信号日志 (alerts)
```

### 8.3 Tab 1 — 狙击雷达

**功能：**

- 展示全部监控股票的实时计算状态
- 顶部汇总：买点数 / 伏击数 / 危险数
- 下拉刷新 → 调用 `GET /api/status`
- 每张卡片显示：名称、代码、Tier、现价、周线 D、日线偏离、大盘状态、规则说明

**卡片颜色边框：**

| ui_status | 边框色 |
|-----------|--------|
| signal | `#22C55E` 绿 |
| ambush | `#EAB308` 黄 |
| danger | `#EF4444` 红 |
| error | `#4B5563` 灰 |

**空态 / 错误态：**

- 无法连接后端 → 显示 API URL + 重试按钮
- 后端未启动 → 明确提示启动 `run_api.sh`

### 8.4 Tab 2 — 股票池

**功能：**

- 列表展示 ticker / name / market / tier / note
- 右上角「+」→ 底部弹窗添加股票
- 左滑或点击垃圾桶 → 确认删除 → `DELETE /api/watchlist/{ticker}`
- 下拉刷新

**添加表单字段：**

| 字段 | 必填 | 校验 |
|------|------|------|
| 代码 | ✅ | 自动转大写 |
| 名称 | ✅ | — |
| 市场 | ✅ | JP / US 二选一 |
| Tier | ✅ | 1 核心池 / 2 常规池 |
| 备注 | ❌ | — |

### 8.5 Tab 3 — 信号日志

**功能：**

- 时间轴列表，按时间倒序
- 展示：时间、标题、股票信息、完整推送正文
- 下拉刷新 → `GET /api/alerts`
- 空态：「暂无历史信号」

**审阅点：** 日志仅记录**实际推送成功**的信号（经 scan 触发），不是每次 status 刷新。是否符合预期？

### 8.6 推送注册流程

```
App 启动
  → 请求通知权限（仅真机）
  → 获取 Expo Push Token
  → POST /api/push-tokens
  → 后端 scan 触发信号时 → Expo Push API → APNs → 手机弹窗
```

| 环境 | Push 支持 |
|------|-----------|
| iOS 模拟器 | ❌ 不支持，可看雷达 |
| iPhone 真机 + Expo Go | ✅ 需 EAS project（可选） |
| TestFlight 正式包 | ✅ 推荐生产方案 |

### 8.7 网络配置

| 场景 | `EXPO_PUBLIC_API_URL` |
|------|------------------------|
| iOS 模拟器 | `http://127.0.0.1:8000` |
| iPhone 真机（同 WiFi） | `http://<Mac局域网IP>:8000` |
| 云端部署后 | `https://api.yourdomain.com` |

---

## 9. 通知与去重规格

### 9.1 推送通道优先级

| 通道 | 用途 | 状态 |
|------|------|------|
| Expo Push → APNs | App 主通道 | ✅ 已实现 |
| Telegram | 过渡期 / 配偶无 App 时 | ✅ 保留并行 |
| 终端 stdout | 开发调试 | ✅ |

### 9.2 去重规则

```
IF 信号类型 ∈ {tier1_core, tier2_buy}
AND cache_key = "{ticker}:buy"
AND now - last_sent < 24 hours
THEN 跳过 Push/Telegram
ELSE 发送并更新 alert_dedup
```

- 去重**不阻止** App 雷达显示当前状态
- 去重**不阻止** alert_history 在下次真正推送时写入

### 9.3 多收件人（Telegram）

`.env` 支持：

```
TELEGRAM_CHAT_IDS=你的id,配偶的id
```

App Push 通过 `push_tokens` 表支持多设备。

---

## 10. 定时任务规格

**时区：** 系统本地时间 = Asia/Tokyo (JST)

| 任务 | Cron | 命令 | 目的 |
|------|------|------|------|
| 日股盘前 | `30 8 * * 1-5` | `main --market JP` | 宏观筛选 |
| 日股收盘前 | `30 14 * * 1-5` | `main --market JP` | 战术触发 |
| 美股收盘前 | `30 4,5 * * 2-6` | `run_us_close_scan.py` | ET 15:30 守卫 |

**美股 ET 守卫逻辑：**

- 仅在美东周一至周五 15:28–15:32 执行
- 自动覆盖夏令时（JST 04:30）与冬令时（JST 05:30）
- 无需每年手动改 cron

**审阅点：** 云端部署后，Cron 从 Mac 迁移到云服务器，Mac 可关机。

---

## 11. 安全与隐私（当前缺口）

| 项目 | 当前状态 | 风险 | 建议 |
|------|----------|------|------|
| API 认证 | ❌ 无 | 局域网内可被任意调用 | Phase 3 加 API Key / JWT |
| HTTPS | ❌ 本地 HTTP | 中间人攻击 | 云端强制 TLS |
| 股票池数据 | 本地 SQLite | 低敏感 | 云端加密备份 |
| Push Token | 明文存 DB | 低敏感 | 可接受 |
| CORS | `*` | 开发便利 | 生产收紧 origin |
| 交易账户 | 不接入 | 无资金风险 | 保持不做 |

---

## 12. 实现状态矩阵

| 功能 | CLI | API | App | 云端 |
|------|-----|-----|-----|------|
| 铁律 2.5 三阶段策略 | ✅ | ✅ | 展示 | — |
| yfinance 前复权 | ✅ | ✅ | — | — |
| 日/美大盘分离 | ✅ | ✅ | 展示 | — |
| SQLite watchlist | ✅ | ✅ | CRUD | ⏳ |
| 24h 去重 | ✅ | ✅ | — | — |
| Telegram 推送 | ✅ | ✅ | — | — |
| Expo Push | ✅ | ✅ | 注册 | ⏳ 需真机 |
| 信号历史 | ✅ | ✅ | 展示 | — |
| 策略参数 App 内可调 | ❌ | ❌ | ❌ | — |
| K 线图 | ❌ | ❌ | ❌ | — |
| 用户登录 | ❌ | ❌ | ❌ | — |
| TestFlight | ❌ | — | ❌ | ⏳ |
| 云端 Cron | ❌ | — | — | ⏳ |

---

## 13. 测试用例（审阅验收清单）

### 13.1 策略逻辑

| # | 输入条件 | 预期结果 |
|---|----------|----------|
| T-01 | 4063.T, D=+1.5%, Tier 1 | `tier1_core` / 绿色 signal |
| T-02 | AMD, D=+23% | `excluded` / 红色 danger |
| T-03 | Tier 2, 大盘非跌势, P<MA_d | `watching`，不推送 |
| T-04 | Tier 2, 大盘跌势, P≤MA_d | `tier2_buy` / 推送 |
| T-05 | yfinance 超时 | `error`，不推送 |
| T-06 | 同一信号 10 分钟内扫 2 次 | 第 2 次 skipped |

### 13.2 API

| # | 操作 | 预期 |
|---|------|------|
| A-01 | POST 新股票 | 201, GET 可见 |
| A-02 | DELETE 不存在 | 404 |
| A-03 | GET /api/status | 返回 ui_status 字段 |
| A-04 | POST /api/scan dry_run=true | pushed=0 |

### 13.3 App UI

| # | 操作 | 预期 |
|---|------|------|
| M-01 | 下拉刷新雷达 | 卡片更新 |
| M-02 | 添加 7203.T | 雷达出现新卡片 |
| M-03 | 删除股票 | 雷达移除 |
| M-04 | 后端未启动 | 显示错误 + 重试 |

---

## 14. 路线图

### Phase 3 — 云端部署（建议下一步）

- [ ] 后端部署至 Railway / Render / EC2
- [ ] SQLite → Supabase PostgreSQL
- [ ] 云端 Cron 替代 Mac crontab
- [ ] HTTPS + API Key
- [ ] `EXPO_PUBLIC_API_URL` 指向生产域名

### Phase 4 — 正式 iOS 发布

- [ ] `eas build` → TestFlight
- [ ] APNs 生产证书（Expo 托管）
- [ ] App Store 图标 / 截图 / 隐私说明

### Phase 5 — 可选增强（待你审阅决定）

- [ ] App 内策略参数调节
- [ ] 配偶独立 watchlist / 独立账户
- [ ] 5 分钟行情缓存（降低 yfinance 压力）
- [ ] 股票详情页（周线/日线迷你图）
- [ ] SBI HYPER SBI 2 半自动下单（Windows only，另立项）

---

## 15. 待你审阅的关键决策点

请逐项确认或标注修改意见：

1. **策略参数**  
   - 10 周 / 20 日 / D±15% / Tier1 ±2% 是否定为 v1.0 冻结参数？

2. **/status 实时拉行情**  
   - 每次刷新都调 yfinance，可能慢且易被限流。是否加缓存？

3. **信号日志范围**  
   - 仅记录「实际推送」vs「所有曾经触发过的信号（含去重跳过）」？

4. **配偶使用模式**  
   - A) 共用 watchlist + 各收 Push  
   - B) 独立 watchlist（需多用户架构）  
   - C) 配偶仅 Telegram，你用 App

5. **Telegram 去留**  
   - 云端上线后是否保留 Telegram 作为备用通道？

6. **非目标确认**  
   - 是否同意 v1.0 **绝不**做自动下单？

7. **数据源局限**  
   - 接受 yfinance 延迟，还是未来需接入付费行情（如 Polygon、JP 专用源）？

8. **UI 风格**  
   - 当前深色战术风是否 OK？是否需要日股/美股分 Tab 筛选？

---

## 16. 附录

### A. 初始测试池 Dry-run 结果（2026-06-22）

| 股票 | ui_status | 说明 |
|------|-----------|------|
| 4063.T 信越化学 | 🟢 signal | D=+1.5%，Tier1 核心阻击 |
| AMD | 🔴 danger | D=+23.1%，高位排雷 |
| 6859.T TOWA | 🔴 danger | D=+25.3%，高位排雷 |

### B. 相关文档

- `README.md` — 仓库总览与快速开始
- `docs/使用说明.md` — CLI + Telegram + Lynch 用户手册
- `docs/lynch-requirements.html` — **Lynch Agent 全链路技术需求**（FMP、漏斗、五周期简报、狙击）
- `docs/app-roadmap.md` — 技术路线图
- `mobile/README.md` — App 本地启动说明
- `http://127.0.0.1:8000/docs` — 自动生成的 OpenAPI 文档

### C. Lynch Agent（独立子系统）

与铁律 2.5 监控并行存在，通过 `src/lynch/` 实现：

| 能力 | 说明 |
|------|------|
| 数据层 | `DATA_PROVIDER=fmp`（生产）或 `yahoo`；FMP stable API + 本地缓存 |
| 漏斗 | SEC 美股抽样 → 硬指标粗筛 → Gemini 熔断（≤30 只） |
| 简报 | daily / weekly / monthly / quarterly / annual 五种模式，SMTP 邮件 |
| 安全网 | 实时新闻 + 8-K + Gemini 黑天鹅一票否决 |
| 巨鳄雷达 | 议员交易 + 13F 机构持仓变动 |
| 狙击 | 收盘后（日报内）+ 盘中实时（独立 workflow） |

详见 `docs/lynch-requirements.html`。

### D. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.1.0 | 2026-06-22 | CLI + Telegram + Cron |
| v0.2.0 | 2026-06-22 | FastAPI + SQLite + Expo App 雏形 |
| v0.2.0-draft | 2026-06-22 | 本规格说明书 |
| v0.2.1 | 2026-07-06 | 文档同步 Lynch Agent（FMP、五周期、舆情/巨鳄雷达） |

---

*本文档供产品审阅。确认第 15 节决策点后，可进入 Phase 3 云端部署与 TestFlight 打包。*

# stock-monitor / Lynch

个人/家庭用股票投研与监控仓库，包含两套可独立运行的子系统：

| 子系统 | 用途 | 入口 |
|--------|------|------|
| **铁律 2.5 监控** | 均线买点警报 → Telegram / Expo Push | `python -m src.main` |
| **彼得·林奇 Lynch Agent** | 基本面 SOP + 双层漏斗 + Gemini 简报邮件 | `scripts/run_scheduled_analysis.py` |

生产环境（GitHub Actions）以 **Lynch Agent** 为主：默认 `DATA_PROVIDER=fmp`、`MARKET=US`，每周六自动发深度分析周报。

## 文档索引

| 文档 | 说明 |
|------|------|
| [docs/使用说明.md](docs/使用说明.md) | 用户手册：Telegram 监控 + Lynch 流水线配置 |
| [docs/lynch-requirements.html](docs/lynch-requirements.html) | Lynch Agent 全链路技术需求（浏览器打开） |
| [docs/SPEC.md](docs/SPEC.md) | 铁律 2.5 App 产品规格（FastAPI + Expo） |
| [docs/app-roadmap.md](docs/app-roadmap.md) | 移动端路线图 |
| [.env.example](.env.example) | 环境变量模板 |

## 快速开始（Lynch 简报）

```bash
cd ~/Projects/stock-monitor
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # 填入 GEMINI_API_KEY、FMP_API_KEY、SMTP 等

# 单股分析
PYTHONPATH=src .venv/bin/python scripts/analyze.py RKLB

# 本地跑周报（仅自选股，不发邮件）
PYTHONPATH=src .venv/bin/python scripts/run_scheduled_analysis.py \
  --mode weekly --scope watchlist --no-email
```

## 仓库结构（精简）

```
stock-monitor/
├── watchlist.yaml          # 影子持仓 / 必看列表（held·watch·avoid）
├── watchlist-jp.yaml       # 日股备用清单（默认流水线不扫）
├── src/
│   ├── main.py             # 铁律 2.5 CLI
│   ├── strategy.py         # 均线策略
│   └── lynch/              # Lynch Agent 核心
│       ├── data/           # Yahoo / FMP 数据供应层
│       ├── funnel.py       # 双层漏斗
│       ├── agent.py        # 编排 + Prompt 数据块
│       └── notify.py       # 邮件简报
├── scripts/
│   ├── run_scheduled_analysis.py   # 日报/周报/月报/季报/年报
│   └── run_realtime_sniper.py      # 盘中狙击
├── data/cache/fmp/         # FMP 本地缓存（gitignore）
└── .github/workflows/
    ├── run_analysis.yml    # 定时简报
    └── sniper_realtime.yml # 盘中狙击
```

## 相关链接

- GitHub：`https://github.com/nanxv/Lynch`
- Gemini API Key：https://aistudio.google.com/apikey
- FMP API Key：https://site.financialmodelingprep.com/developer/docs

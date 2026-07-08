# 林奇筛选逻辑：现状全景 × 贴合原书改动方案

| 字段 | 内容 |
|------|------|
| 文档日期 | 2026-07-08 |
| 对照基线 | 《彼得·林奇的成功投资》方法论（项目 `knowledge/lynch_playbook.md` 提炼版） |
| 代码基线 | `main` · funnel / metrics / classify / prompt / FMP quick-screen |

> 本文两大部分：**(A) 现在实际怎么筛**；**(B) 若要「完全贴合原书」该改什么、改哪里、怎么验收**。  
> 「完全贴合」指：**分类分尺 + 可量化的林奇排雷**尽量进代码；定性常识（无聊行业、护城河故事）仍可由 Gemini 补全，但不得与硬指标打架。

---

## A. 现行逻辑全景

### A.1 端到端流水线

```
watchlist.yaml（held/watch；avoid 物理跳过）
        │
        ▼
┌─ scope=watchlist ─────────────────────────────────────┐
│  全部优先股 → 直接进分析（不做全市场漏斗）              │
└───────────────────────────────────────────────────────┘
┌─ scope=full ──────────────────────────────────────────┐
│  1) 必看股先拉 quick_screen（优先保证 AI 配额）         │
│  2) universe(SEC/sp500/nasdaq100/jpx) → MARKET 过滤     │
│  3) first_funnel(get_quick_screen)  ← 第一层硬筛        │
│  4) 必看股 ∪ 漏斗幸存者 → working set                   │
└───────────────────────────────────────────────────────┘
        │
        ▼
rank_and_cap：必看永远进 AI；其余按 PEG/净现金排序，≤ MAX_AI_ANALYSIS_COUNT
        │
        ▼
对每只：get_fundamentals(mode) → compute_metrics → build_data_block
        │                 │
        │                 ├─ 舆情/8-K、巨鳄雷达（FMP）
        │                 └─ temporal / granularity（按日报/周报/季报…）
        ▼
Gemini 四步（AI 组）或仅硬指标（降级组）
        │
        ▼
fatal_warnings / is_quality_pick / cyclical_watch / extract_signal
        │
        ▼
邮件简报：优质 · 排雷 · 裁决看板 · 周期观察 · 双轨详情
```

**关键事实：** 林奇「完整尺子」主要在 **深度层（metrics + Prompt）**；全市场广度靠 **第一层漏斗**，后者是成本控制用的近似过滤器，**不是原书六类齐全的投研流程**。

---

### A.2 第一层漏斗（`funnel._passes_first_funnel`）

**通过条件（须同时满足「负债门」+「估值或隐蔽资产」）：**

| 条件 | 阈值 | 数据来源（FMP 现状） |
|------|------|----------------------|
| 负债门 | `debt_ratio ≤ 0.50`（有值才判） | 轻量筛：`ratios-ttm.debtToEquityRatioTTM`（**总债/权益**） |
| 估值通道 | `0 < quick_peg ≤ 1.5` | 轻量筛：`ratios-ttm.priceToEarningsGrowthRatioTTM`（**非股息修正、非自算多年 CAGR**） |
| 隐蔽资产通道 | `net_cash_ratio ≥ 0.30` | 有静态缓存时用资产负债表增补；冷启动常为 `None` |

**不做的事（相对原书）：**

- 不按六大分类分支  
- 不豁免金融股负债  
- 不豁免周期股「亏损/无 PEG」  
- 不看存货、FCF、历史 P/E  
- 不看增速掉档、债务趋势  

**并发 / 成本：** `SCAN_WORKERS=8`；FMP 每只约 2～3 次 API（profile + ratios-ttm ± quote）。

---

### A.3 第二层 AI 熔断（`rank_and_cap`）

| 规则 | 现状 |
|------|------|
| 必看股 | `is_priority=True` 永远进 AI，排最前 |
| 其余 | 按 `AI_SORT_KEY`：默认 `peg` 升序，或 `net_cash` 降序 |
| 上限 | `MAX_AI_ANALYSIS_COUNT` 默认 30 |
| 超额 | 仅硬指标写入简报，不调 Gemini |

---

### A.4 深度量化排雷（`metrics.compute_metrics`）

这是与原书**最对齐**的一层。

| 指标 | 公式 / 规则 | 灯号逻辑（摘要） |
|------|-------------|------------------|
| **股息修正 PEG** | `P/E ÷ (CAGR% + 股息率%)`；增长取多年 EPS/净利 CAGR；拒单季同比；CAGR>50% 分母锚 35% | ≤0.5 绿极佳；≤1 绿；1～2 黄；>2 红；**周期股 >2 或亏损 → 黄（不红）** |
| **长期负债/权益** | `long_term_debt / stockholders_equity` | ≤33% 绿；≤80% 黄；>80% 红；**金融全豁免** |
| **存货 vs 销售** | YoY 存货增速 − 销售增速 | 存货≤销售绿；差≤10pp 黄；更大红 |
| **每股净现金** | `(现金−总负债)/股本`，相对股价 | 占股价≥30% 强调「厚垫」 |
| **自由现金流** | FCF 正负 + FCF/市值 | 负为红/黄，正为绿 |
| **公司类型** | `classify_company` 启发式 | 供 Prompt / 展示；LLM 可复核 |

**致命红灯（`fatal_warnings`，邮件置顶）：**

- 存货增速 > max(销售,0)×2 且差 >5pp（科技/轻资产豁免）  
- 长期债/权益 >33%（金融豁免）  
- 盈利同比 ≤−30% 或长期 CAGR 负（**周期豁免**）

**优质股（`is_quality_pick`）：** 无 fatal，且 PEG∈(0,1]，债务绿，FCF 绿。→ **窄口径「快增低估」**，不是六类全集。

**周期观察（`cyclical_watch`）：** 周期股 +（亏损或高 P/E 或利润下滑）→ 单列「盯底部」，不当致命红灯。

---

### A.5 六大分类启发式（`classify_company`）

**优先级自上而下，命中即返回：**

1. 净现金/股价 ≥ 30% → **隐蔽资产型**  
2. 增长 < 0 且存在长期债 → **困境反转型**  
3. 行业/板块落入周期启发式 → **周期型**  
4. 增长 ≥ 20% → **快速增长型**  
5. 股息 ≥ 4% 且增长 < 8% → **缓慢增长型**  
6. 默认 → **稳定增长型**

周期/金融判定：`Energy`/`Basic Materials` + 行业关键词；`Financial Services` 及 Bank/Insurance 等 → 金融。

---

### A.6 Prompt 层（行为纪律）

| 模式 | 林奇相关强制逻辑 |
|------|------------------|
| 全局 SYSTEM | 忽略宏观；存货命门；PEG>1.5 禁止「强烈买入」；黑天鹅一票否决 SELL |
| weekly | 扫描仪：快增/隐蔽 + 硬指标红灯无情揭露 |
| quarterly | 按类「卖出质问」：快增两季掉档；周期利润靓+存货堆；稳增 P/E 破历史；**held → 必须 SELL** |
| annual | 分类退化、反转结束、恶性并购；held → 剔除名单 |
| daily / sniper | 暴跌+即时 PEG 是否黄金坑 |

**行动指令四选一：** BUY NOW / WATCHLIST / HOLD / SELL-AVOID。

---

### A.7 与原书契合度一览（摘要）

| 区域 | 评级 | 一句话 |
|------|------|--------|
| 深度 PEG / 债 / 存货 / 净现金 / FCF | ★★★★★ | 高度贴合 playbook |
| 卖出纪律（held + 故事变坏） | ★★★★☆ | 工程化，方向正确 |
| 周期反直觉（进 AI 后） | ★★★★☆ | Prompt + cyclical_watch 有；漏斗层无 |
| 第一层漏斗 | ★★☆☆☆ | 工程过滤器，非林奇分尺 |
| 分类启发式 | ★★☆☆☆ | 有框架，优先级易误分 |
| 定性加分减分 | ★★☆☆☆ | 只在 Prompt/playbook，无硬字段 |
| 稳增「历史 P/E 错杀」粗筛 | ★☆☆☆☆ | temporal 有数据，漏斗未用 |

---

## B. 「完全贴合原书」改动方案

### B.0 设计原则（改之前先钉死）

1. **分尺先于统一分**：同一 PEG/P/E 在不同分类含义相反——粗筛必须带「分类标签」或「多通道 OR」。  
2. **深度层尺子为 SSOT**：漏斗允许近似，但必须文档化偏差；长期目标是漏斗复用同一套可降级指标。  
3. **成本可控**：全市场仍要粗筛；贴合原书 = **多通道粗筛**，不是每只先拉十年表。  
4. **LLM 不覆盖硬红灯**：Prompt 保持一票否决；分类复核可以升级叙事，不能把 fatal 洗绿。  
5. **分阶段交付**：先修「会系统性漏掉林奇爱股」的通道，再修分类精度与定性数据。

---

### B.1 目标架构（贴合后的漏斗）

```
get_quick_screen_lynch()
  → 粗分类 tag（sector/industry + 粗增速/股息/净现金）
  → 按 tag 进入通道 OR：

  [A 快增/通用估值]  股息修正粗 PEG ≤ 1.0（严格）或 ≤ 1.5（宽），且 LTD/E ≤ 0.33（金融豁免）
  [B 隐蔽资产]        净现金/股价 ≥ 0.25～0.30
  [C 周期底部旁路]     标记为 cyclical 且 (PEG 缺失/亏损) 且 存货增速 ≤ 销售增速（或存货缺失）
  [D 稳增错杀]         标记为 stalwart 且 当前 P/E ≤ 5y 平均 P/E × 0.85（需短缓存）
  [E 缓慢股息]         股息率 ≥ 3～4% 且派息可持续粗检（FCF>0 或近三年未减配）
  [F 困境反转旁路]     增长负 + 净现金上升 或 长期债同比下降（需两年 balance）

任一门通过 + 非金融不超额杠杆 → 进入幸存者
```

邮件/AI 层再按原分类用**不同叙事尺子**（已有 quarterly/annual Prompt，需与粗筛 tag 对齐）。

---

### B.2 分阶段改动清单

#### Phase 0 — 基线与仪表（0.5～1 天）

| ID | 改动 | 文件 | 验收 |
|----|------|------|------|
| P0-1 | 在简报页眉打印：漏斗通道命中分布（A/B/C…计数）与 PEG 口径说明 | `run_scheduled_analysis.py` / `notify.py` | 邮件可见「PEG 口径：股息修正粗估 / FMP ratios」 |
| P0-2 | 加 `--audit-funnel`：对 sample 列表输出 light PEG vs 深度 PEG 相关矩阵 | `scripts/` | 本地脚本跑通 |
| P0-3 | 文档钉死：「full 漏斗 ≠ 完整林奇」 | `docs/使用说明.md` | 一节说明 |

#### Phase 1 — 堵致命偏离（P0，建议 2～3 天）**【优先做】**

| ID | 原书要求 | 现状问题 | 具体改法 | 主要文件 |
|----|----------|----------|----------|----------|
| P1-1 | PEG = P/E÷(增长+股息)，多年增长 | 漏斗用 FMP `priceToEarningsGrowthRatioTTM` | **方案甲（推荐）**：轻量层增加 `key-metrics-ttm` 或 profile 股息 + 自建「粗 CAGR」：用 `ratios-ttm` 的 PE + FMP `financial-growth` 的 3～5y EPS growth（若有）+ lastDiv 股息率，复用 `_peg_metric` 同一公式的简化版 `coarse_dividend_peg()`。**方案乙**：漏斗仍用 FMP PEG，但阈值收紧到 1.0，并把字段改名 `vendor_peg`，页眉标明「非林奇股息修正」。长期应用甲。 | `fmp.py` `_light_quick_screen`；新建 `lynch/coarse_metrics.py` |
| P1-2 | 长期债/权益 &lt; ~1/3；金融豁免 | 漏斗 `debtToEquity`≤0.50、无金融豁免 | QuickScreen 增加 `is_financial`；`debt_ratio` 改为 `long_term_debt/equity`（ratios 有则用之，否则 balance 缓存）；阈值默认 `FUNNEL_MAX_DEBT_RATIO=0.33`；金融跳过负债门 | `base.QuickScreen`；`funnel._passes_first_funnel`；`config.py` |
| P1-3 | 周期底部常亏损/高 P/E | 无 PEG → 漏斗出局 | 增加通道 C：`is_cyclical_rough(sector,industry)` 且 `quick_peg is None` 且（`inv_growth is None` 或 `inv_growth ≤ sales_growth`）则通过；可选再要求 FCF≥0 或净现金不极差 | `funnel.py`；`QuickScreen` 增 `sector/industry/inv_yoy/sales_yoy` |
| P1-4 | 隐蔽资产不依赖 PEG | 冷启动 `net_cash_ratio=None` | 轻量路径增加 1 次 `balance-sheet-statement?period=annual&limit=1`（或 key-metrics cash/share）；算净现金比；控制：仅当 PEG 未过时再拉，或批量用批量接口 | `fmp.py` |

**验收标准 Phase 1：**

- 用固定种子 universe 跑 `first_funnel`：幸存者中周期行业占比不应≈0。  
- 对照抽样：KO 类高股息稳企不应仅因厂商 PEG 怪异全灭。  
- 深度 `_peg_metric` 与粗筛 `coarse_dividend_peg` 同票相关（Spearman）显著高于现状 vendor PEG。  
- ALB/NUE 类周期在「亏损季」应能走通道 C 进幸存（若存货未堆积）。

#### Phase 2 — 分类分尺（P1，3～5 天）

| ID | 原书尺子 | 具体改法 | 文件 |
|----|----------|----------|------|
| P2-1 | 先分类再用尺 | 将 `classify_company` 拆成可测纯函数；**改优先级**：周期判定（行业）优先于「净现金≥30%→隐蔽资产」；净现金高仅 **加标签** `asset_play_hint`，不覆盖快增主类 | `data/base.py` |
| P2-2 | 快增 20–25%，&gt;25% 警惕 | 粗分类：20%≤g&lt;25% → Fast；g≥25% → Fast+`growth_cap_warn`；Prompt 强制提示不可持续 | `classify` + `prompt` |
| P2-3 | 稳增：历史低 P/E 错杀 | 通道 D：对非周期、非快增，用缓存/轻量 `pe_5y_avg`（已有 temporal，可把 5y 低价序列放进 weekly 静态缓存刷新）；`trailing_pe ≤ pe_5y_avg * FUNNEL_STALWART_PE_DISCOUNT(0.85)` 且 LTD/E ok → 通过 | `fmp` 静态包增 pe_hist；`funnel` |
| P2-4 | 缓慢型：股息 | 通道 E：`dividend_yield ≥ 4` 且 `payout` 粗检（或 FCF&gt;0）且非周期 → 通过；深度层缓慢型卖点仍看分红削减 | `funnel` + Prompt weekly 分支说明 |
| P2-5 | 困境反转：债降、现金升 | 通道 F：`earnings_growth &lt; 0` 且（`ltd` YoY &lt; −10% 或 net_cash YoY &gt; 0）→ 通过；分类勿仅「亏损+有债」 | `classify` + `funnel` |
| P2-6 | 邮件分轨 | `is_quality_pick` 改名或拆为：`quality_fast_grower` / 保留；简报增加与 cyclical_watch 同级的「隐蔽资产」「困境候选」「股息慢增」列表 | `funnel.py`；`notify.py` |

**验收：** 简报出现多类置顶桶；人工抽 20 票分类与林奇直觉一致率 ≥ 70%。

#### Phase 3 — 卖出与持仓纪律硬化（2～3 天）

| ID | 原书 | 改法 |
|----|------|------|
| P3-1 | 快增：连续两季增速掉档 | 代码侧：用 `income_quarterly` 最近 4 季 YoY，若连续两季 earn growth 明显下滑（如 &lt; 前两季均值 ×0.7）→ `fatal` 或强制 signal 降级钩子（held 时） | `fatal_warnings` 或新 `growth_stall_detector` |
| P3-2 | 周期见顶：利润靓 + 存货堆 | 已有 Prompt；代码补：cyclical & inventory gap 红 & trailing_pe 处历史低分位 → `cyclical_top_warning` 置顶 | `funnel`/`notify` |
| P3-3 | 稳增：P/E 透支 | held + `pe &gt; pe_5y_avg * 1.3` → quarterly 任务已覆盖；代码给 Gemini 明确字段 `pe_vs_5y` | `agent.build_data_block` |
| P3-4 | 不因为涨了而卖 | Prompt 已强调；加回归测试：叙事含「涨多了所以卖」应被拒绝（可选 LLM judge / 规则） | 测试 |

#### Phase 4 — 定性与信息优势（可选，持续）

| ID | 原书 | 工程化程度 | 建议 |
|----|------|------------|------|
| P4-1 | 机构持股低 | 中 | FMP institutional %；加分写入 data_block |
| P4-2 | 内部人增持 / 回购 | 中 | FMP insider + 注意与「政客交易」区分 |
| P4-3 | 产品占营收 &lt;10% 陷阱 | 低 | 继续依赖 user_note；可引导 watchlist note 结构化 |
| P4-4 | 无聊行业 / 名字枯燥 | 低 | 维持 Gemini；勿硬编码行业黑白名单 |
| P4-5 | 10–12 月税务抛售 | 低 | 季节性加权：Q4 放宽通道 D 折扣 | 

---

### B.3 配置项草案（落地时写入 `.env.example`）

```bash
# 漏斗多通道（贴合原书后）
FUNNEL_MAX_PEG=1.0                 # 快增/通用严格线；宽通道可用 1.5
FUNNEL_MAX_DEBT_RATIO=0.33         # 长期债/权益；与深度层对齐
FUNNEL_MIN_NETCASH_RATIO=0.30
FUNNEL_ENABLE_CYCLICAL_BYPASS=1
FUNNEL_ENABLE_STALWART_PE_BYPASS=1
FUNNEL_STALWART_PE_DISCOUNT=0.85
FUNNEL_ENABLE_SLOW_DIV_BYPASS=1
FUNNEL_MIN_DIV_YIELD=4.0
FUNNEL_ENABLE_TURNAROUND_BYPASS=1
FUNNEL_PEG_MODE=lynch_coarse       # lynch_coarse | vendor_ttm
```

默认建议：**新通道用 OR 放开漏斗**（幸存者可能从 ~70 升到 120+），用 `MAX_AI_ANALYSIS_COUNT` 控 Gemini 成本；排序 key 可改为「分类优先级分数」。

---

### B.4 模块级改造表（给开发者）

| 模块 | 现状职责 | 目标职责 |
|------|----------|----------|
| `data/fmp.py` `_light_quick_screen` | vendor PEG + 总债 | 输出粗林奇字段：coarse_peg、ltd_equity、net_cash_ratio、div_yield、sector、粗分类、存货差分（可选） |
| `data/base.py` `QuickScreen` | 窄字段 | 扩展上述字段 + `pass_channels: list[str]` |
| `data/base.py` `classify_company` | 单一字符串、优先级有坑 | 返回 `Classification(primary, tags, warnings)` |
| `funnel.py` `_passes_first_funnel` | 单 if | 多通道 OR + 负债门（金融豁免） |
| `funnel.py` `is_quality_pick` | 单一优质 | 多桶 `scorecards` |
| `metrics.py` | SSOT 深度尺 | 抽取共享 `dividend_adjusted_peg(pe, cagr, div)` 供漏斗复用 |
| `prompt.py` | 按 mode | weekly 根据 `pass_channels` / 分类注入「应用哪把尺子」 |
| `notify.py` | 优质/红灯/周期 | 六类或四桶置顶 |
| `config.py` | FUNNEL_* 三项 | B.3 全套开关 |

---

### B.5 测试与验收矩阵

| 用例 | 贴合前常见行为 | 贴合后期望 |
|------|----------------|------------|
| 周期股亏损季（存货未堆） | 漏斗刷掉 | 通道 C 幸存；简报进「周期观察」而非「优质」 |
| 净现金厚但增长 25% 的厂 | 标成隐蔽资产 | 主类快增 + asset_hint |
| 银行 high leverage | 可能被负债门杀掉 | 金融豁免进漏斗 |
| 稳增 P/E 远低于 5y 均 | 无 PEG 漂亮则出局 | 通道 D 幸存 |
| 快增 PEG 0.8、负债 20% | 幸存且优质 | 仍为优质快增 |
| held + 两季增速掉档 | 靠 Gemini | 代码 fatal/降级 + Prompt 双重 |

回归：固定 `seed` 的 universe 快照 JSON，对比幸存者集合的通道标签分布。

---

### B.6 工作量与风险

| 阶段 | 估时 | 风险 |
|------|------|------|
| Phase 0 | 0.5–1d | 低 |
| Phase 1 | 2–3d | FMP 多 1 次 balance/growth 调用 → 配额/耗时；需监控 Actions 30min |
| Phase 2 | 3–5d | 幸存者暴增 → AI 仍 30 只，简报变长但深度不够；需调排序 |
| Phase 3 | 2–3d | 误杀持仓；held 强制卖出要可 audit |
| Phase 4 | 持续 | 数据噪声（内部人、机构口径） |

**不建议：** 为「完全贴合」关掉第一层漏斗对 500～1200 只全拉深度 fundamentals（成本与超时不可接受）。

---

### B.7 建议落地顺序（执行清单）

1. **本周：** Phase 1（P1-1～P1-4）——漏斗尺子与深度对齐 + 周期旁路 + 负债/金融。  
2. **下周：** Phase 2 分类优先级 + 稳增 P/E 通道 + 简报多桶。  
3. **再下周：** Phase 3 持仓卖出硬化。  
4. **有余力：** Phase 4 定性数据。

每阶段结束跑一次 `weekly --scope full --market US`，对比：漏斗 `517→X`、通道命中表、邮件桶分布，再决定是否拧阈值。

---

## C. 一句话对照

| | 现在 | 贴合原书目标 |
|--|------|----------------|
| 全市场入口 | 单一 PEG×1.5 **或** 净现金×0.3，总债×0.5 | **分类多通道 OR**，负债 0.33 + 金融豁免 |
| PEG | 深度层林奇；漏斗层厂商 TTM | **同一股息修正公式**（粗/细精度不同） |
| 周期 | 进 AI 后豁免；漏斗常灭 | **底部旁路进漏斗**，顶部存货置顶 |
| 稳增 | 靠运气 PEG | **历史 P/E 错杀通道** |
| 输出 | 优质窄口径 + 周期观察 | **六类/多桶**与行动指令一致 |

---

*相关：`knowledge/lynch_playbook.md` · 契合度画布（会话产出）· `src/lynch/{funnel,metrics,prompt,data/base,data/fmp}.py`*

# Lynch 数据与计算审计报告

| 项目 | 值 |
|------|-----|
| 生成时间 | 2026-07-05 15:00 JST（2026-07-05 06:00 UTC） |
| 范围 | watchlist · market=US |
| 报告模式 | `weekly` |
| 标的数量 | 3 |
| 阶段 1 可信 | 3/3 |
| 阶段 2 验算全过 | 3/3 |

---

## 执行摘要

- **AMD**：阶段1 ✅ score 92 （0F/1W）· 阶段2 ✅ score 100 · PEG=3.70
- **NVDA**：阶段1 ✅ score 84 （0F/2W）· 阶段2 ✅ score 100 · PEG=0.84
- **META**：阶段1 ✅ score 84 （0F/2W）· 阶段2 ✅ score 89 · PEG=0.53

---

## 逐股明细

## AMD

> Advanced Micro Devices, Inc. · Technology · 现价 517.82 USD · TTM P/E 172.60667

### 阶段 1：原始数据质检 · AMD

| 级别 | 维度 | 字段 | 说明 |
|------|------|------|------|
| **WARN** | cross_source | earnings_growth | 增速来源打架：info.earningsGrowth=91.2% vs 年表 YoY=164.2% |
| **INFO** | cross_source | peg | 漏斗 quick_peg=1.89 vs 正式 PEG=3.70（口径不同，非必然错误） |

**质检结论**：✅ 可信 · score **92** · 0 fail / 1 warn

<details><summary>字段溯源</summary>

| 字段 | Yahoo 来源 |
|------|-----------|
| `dividend_yield` | info.dividendYield |
| `earnings_growth_yoy` | info.earningsGrowth（单季同比，非 CAGR） |
| `eps_series` | income_stmt.Diluted EPS | Basic EPS（年度列） |
| `forward_pe` | info.forwardPE |
| `free_cashflow` | info.freeCashflow | cashflow.Free Cash Flow |
| `inventory_series` | balance_sheet.Inventory（年度列） |
| `long_term_debt` | balance.Long Term Debt | info.longTermDebt |
| `market_cap` | info.marketCap |
| `net_income_series` | income_stmt.Net Income（年度列） |
| `price` | info.regularMarketPrice | info.currentPrice |
| `quote_time` | info.regularMarketTime=1783022401 |
| `revenue_growth_yoy` | info.revenueGrowth |
| `revenue_series` | income_stmt.Total Revenue（年度列） |
| `shares_outstanding` | info.sharesOutstanding |
| `stockholders_equity` | balance.Stockholders Equity |
| `total_cash` | info.totalCash | balance.Cash And Cash Equivalents |
| `total_debt` | info.totalDebt | balance.Total Debt |
| `trailing_pe` | info.trailingPE |

</details>

### 阶段 2：参数计算验算 · AMD

| 指标 | 公式 | 手算 | 引擎 | 结果 |
|------|------|------|------|------|
| 长期增长率 (CAGR) | `(末值/初值)^(1/年数) - 1；优先 EPS 序列，其次净利润…` | 0.46663551029184314 | 0.46663551029184314 | ✅ |
| 股息修正 PEG | `P/E ÷ (capped_CAGR×100 + dividend_yield_…` | 3.699 | 3.6989613133395776 | ✅ |
| 长期负债 / 股东权益 | `long_term_debt / stockholders_equity（金融股…` | 0.0373 | 0.04 | ✅ |
| 存货增速 − 销售增速 (百分点) | `YoY(inventory) - YoY(revenue)，再 ×100…` | 3.8 | 3.8 | ✅ |
| 每股净现金 | `(total_cash - total_debt) / shares_outst…` | 5.2 | 5.2 | ✅ |
| 自由现金流 (绝对值) | `info.freeCashflow | cashflow.Free Cash F…` | 7173374976 | 7173374976 | ✅ |
| FCF / 市值 | `free_cashflow / market_cap…` | 0.0085 | 0.0085 | ✅ |
| SBI/NISA 可交易 | `主板 + 市值≥3亿美元，排除 OTC…` | True | True | ✅ |
| 漏斗 quick_peg（粗筛口径） | `P/E ÷ (info.earningsGrowth × 100)；无股息修正…` | 1.8926 | 1.892616995614035 | ✅ |

**验算结论**：9/9 通过，score 100，全部一致 ✅

<details><summary>展开计算明细</summary>

#### 长期增长率 (CAGR) (`cagr`)
- **公式**：(末值/初值)^(1/年数) - 1；优先 EPS 序列，其次净利润
- **输入**：
  - `basis` = 4年摊薄EPS复合增长率(CAGR)
  - `eps_first` = 0.84
  - `eps_last` = 2.65
  - `eps_span_years` = 3
- **手算** = 0.46663551029184314
- **引擎** = 0.46663551029184314
- **结果** = pass
- **备注** = 4年摊薄EPS复合增长率(CAGR)

#### 股息修正 PEG (`peg`)
- **公式**：P/E ÷ (capped_CAGR×100 + dividend_yield_pct)
- **输入**：
  - `P/E` = 172.60667
  - `CAGR (decimal)` = 0.46663551029184314
  - `dividend_yield (pct pts)` = 0.0
  - `valuation_pe` = None
  - `trailing_pe` = 172.60667
  - `capped_growth (decimal)` = 0.46663551029184314
  - `denominator (pct pts)` = 46.66355102918431
- **手算** = 3.699
- **引擎** = 3.6989613133395776
- **结果** = pass

#### 长期负债 / 股东权益 (`debt`)
- **公式**：long_term_debt / stockholders_equity（金融股豁免）
- **输入**：
  - `long_term_debt` = 2348000000.0
  - `stockholders_equity` = 62999000000.0
  - `financial` = False
- **手算** = 0.0373
- **引擎** = 0.04
- **结果** = pass

#### 存货增速 − 销售增速 (百分点) (`inventory`)
- **公式**：YoY(inventory) - YoY(revenue)，再 ×100
- **输入**：
  - `inventory_yoy` = 0.3812347401464946
  - `revenue_yoy` = 0.34337793290672874
  - `inventory_years` = [2022, 2023, 2024, 2025]
  - `revenue_years` = [2022, 2023, 2024, 2025]
- **手算** = 3.8
- **引擎** = 3.8
- **结果** = pass

#### 每股净现金 (`net_cash`)
- **公式**：(total_cash - total_debt) / shares_outstanding
- **输入**：
  - `total_cash` = 12346999808
  - `total_debt` = 3871000064
  - `shares` = 1630600639
- **手算** = 5.2
- **引擎** = 5.2
- **结果** = pass

#### 自由现金流 (绝对值) (`fcf`)
- **公式**：info.freeCashflow | cashflow.Free Cash Flow
- **输入**：
  - `free_cashflow` = 7173374976
  - `market_cap` = 844357632000
- **手算** = 7173374976
- **引擎** = 7173374976
- **结果** = pass

#### FCF / 市值 (`fcf_yield`)
- **公式**：free_cashflow / market_cap
- **输入**：
  - `free_cashflow` = 7173374976
  - `market_cap` = 844357632000
- **手算** = 0.0085
- **引擎** = 0.0085
- **结果** = pass
- **备注** = 展示值（引擎写在 verdict 文案中）

#### SBI/NISA 可交易 (`sbi_tradable`)
- **公式**：主板 + 市值≥3亿美元，排除 OTC
- **输入**：
  - `exchange` = NMS
  - `market_cap` = 844357632000
  - `ticker` = AMD
- **手算** = True
- **引擎** = True
- **结果** = pass

#### 漏斗 quick_peg（粗筛口径） (`quick_peg`)
- **公式**：P/E ÷ (info.earningsGrowth × 100)；无股息修正
- **输入**：
  - `P/E` = 172.60667
  - `earningsGrowth` = 0.912
- **手算** = 1.8926
- **引擎** = 1.892616995614035
- **结果** = pass
- **备注** = 与正式 PEG 口径不同；验算漏斗自身是否自洽

</details>

---

## NVDA

> NVIDIA Corporation · Technology · 现价 194.83 USD · TTM P/E 29.83614

### 阶段 1：原始数据质检 · NVDA

| 级别 | 维度 | 字段 | 说明 |
|------|------|------|------|
| **WARN** | cross_source | earnings_growth | 增速来源打架：info.earningsGrowth=214.5% vs 年表 YoY=64.7% |
| **INFO** | cross_source | peg | 漏斗 quick_peg=0.14 vs 正式 PEG=0.84（口径不同，非必然错误） |
| **WARN** | plausibility | dividend_yield | dividendYield=0.5100 在 (0,1) 区间，疑为小数形式（应为百分比数值如 1.51） |

**质检结论**：✅ 可信 · score **84** · 0 fail / 2 warn

<details><summary>字段溯源</summary>

| 字段 | Yahoo 来源 |
|------|-----------|
| `dividend_yield` | info.dividendYield |
| `earnings_growth_yoy` | info.earningsGrowth（单季同比，非 CAGR） |
| `eps_series` | income_stmt.Diluted EPS | Basic EPS（年度列） |
| `forward_pe` | info.forwardPE |
| `free_cashflow` | info.freeCashflow | cashflow.Free Cash Flow |
| `inventory_series` | balance_sheet.Inventory（年度列） |
| `long_term_debt` | balance.Long Term Debt | info.longTermDebt |
| `market_cap` | info.marketCap |
| `net_income_series` | income_stmt.Net Income（年度列） |
| `price` | info.regularMarketPrice | info.currentPrice |
| `quote_time` | info.regularMarketTime=1783022401 |
| `revenue_growth_yoy` | info.revenueGrowth |
| `revenue_series` | income_stmt.Total Revenue（年度列） |
| `shares_outstanding` | info.sharesOutstanding |
| `stockholders_equity` | balance.Stockholders Equity |
| `total_cash` | info.totalCash | balance.Cash And Cash Equivalents |
| `total_debt` | info.totalDebt | balance.Total Debt |
| `trailing_pe` | info.trailingPE |

</details>

### 阶段 2：参数计算验算 · NVDA

| 指标 | 公式 | 手算 | 引擎 | 结果 |
|------|------|------|------|------|
| 长期增长率 (CAGR) | `(末值/初值)^(1/年数) - 1；优先 EPS 序列，其次净利润…` | 2.04239508347386 | 2.04239508347386 | ✅ |
| 股息修正 PEG | `P/E ÷ (capped_CAGR×100 + dividend_yield_…` | 0.8402 | 0.840217966769924 | ✅ |
| 长期负债 / 股东权益 | `long_term_debt / stockholders_equity（金融股…` | 0.0475 | 0.05 | ✅ |
| 存货增速 − 销售增速 (百分点) | `YoY(inventory) - YoY(revenue)，再 ×100…` | 46.9 | 46.9 | ✅ |
| 每股净现金 | `(total_cash - total_debt) / shares_outst…` | 1.67 | 1.67 | ✅ |
| 自由现金流 (绝对值) | `info.freeCashflow | cashflow.Free Cash F…` | 46335873024 | 46335873024 | ✅ |
| FCF / 市值 | `free_cashflow / market_cap…` | 0.0098 | 0.0098 | ✅ |
| SBI/NISA 可交易 | `主板 + 市值≥3亿美元，排除 OTC…` | True | True | ✅ |
| 漏斗 quick_peg（粗筛口径） | `P/E ÷ (info.earningsGrowth × 100)；无股息修正…` | 0.1391 | 0.13909622377622377 | ✅ |

**验算结论**：9/9 通过，score 100，全部一致 ✅

<details><summary>展开计算明细</summary>

#### 长期增长率 (CAGR) (`cagr`)
- **公式**：(末值/初值)^(1/年数) - 1；优先 EPS 序列，其次净利润
- **输入**：
  - `basis` = 4年摊薄EPS复合增长率(CAGR)
  - `eps_first` = 0.174
  - `eps_last` = 4.9
  - `eps_span_years` = 3
- **手算** = 2.04239508347386
- **引擎** = 2.04239508347386
- **结果** = pass
- **备注** = 4年摊薄EPS复合增长率(CAGR)

#### 股息修正 PEG (`peg`)
- **公式**：P/E ÷ (capped_CAGR×100 + dividend_yield_pct)
- **输入**：
  - `P/E` = 29.83614
  - `CAGR (decimal)` = 2.04239508347386
  - `dividend_yield (pct pts)` = 0.51
  - `valuation_pe` = None
  - `trailing_pe` = 29.83614
  - `capped_growth (decimal)` = 0.35
  - `denominator (pct pts)` = 35.51
- **手算** = 0.8402
- **引擎** = 0.840217966769924
- **结果** = pass
- **备注** = 增速>50%，分母已锚定 35%；⚠ dividendYield 可能为小数形式，PEG 分母或偏小

#### 长期负债 / 股东权益 (`debt`)
- **公式**：long_term_debt / stockholders_equity（金融股豁免）
- **输入**：
  - `long_term_debt` = 7469000000.0
  - `stockholders_equity` = 157293000000.0
  - `financial` = False
- **手算** = 0.0475
- **引擎** = 0.05
- **结果** = pass

#### 存货增速 − 销售增速 (百分点) (`inventory`)
- **公式**：YoY(inventory) - YoY(revenue)，再 ×100
- **输入**：
  - `inventory_yoy` = 1.123313492063492
  - `revenue_yoy` = 0.654735357900948
  - `inventory_years` = [2023, 2024, 2025, 2026]
  - `revenue_years` = [2023, 2024, 2025, 2026]
- **手算** = 46.9
- **引擎** = 46.9
- **结果** = pass

#### 每股净现金 (`net_cash`)
- **公式**：(total_cash - total_debt) / shares_outstanding
- **输入**：
  - `total_cash` = 53171998720
  - `total_debt` = 12814000128
  - `shares` = 24221000000
- **手算** = 1.67
- **引擎** = 1.67
- **结果** = pass

#### 自由现金流 (绝对值) (`fcf`)
- **公式**：info.freeCashflow | cashflow.Free Cash Flow
- **输入**：
  - `free_cashflow` = 46335873024
  - `market_cap` = 4718977351680
- **手算** = 46335873024
- **引擎** = 46335873024
- **结果** = pass

#### FCF / 市值 (`fcf_yield`)
- **公式**：free_cashflow / market_cap
- **输入**：
  - `free_cashflow` = 46335873024
  - `market_cap` = 4718977351680
- **手算** = 0.0098
- **引擎** = 0.0098
- **结果** = pass
- **备注** = 展示值（引擎写在 verdict 文案中）

#### SBI/NISA 可交易 (`sbi_tradable`)
- **公式**：主板 + 市值≥3亿美元，排除 OTC
- **输入**：
  - `exchange` = NMS
  - `market_cap` = 4718977351680
  - `ticker` = NVDA
- **手算** = True
- **引擎** = True
- **结果** = pass

#### 漏斗 quick_peg（粗筛口径） (`quick_peg`)
- **公式**：P/E ÷ (info.earningsGrowth × 100)；无股息修正
- **输入**：
  - `P/E` = 29.83614
  - `earningsGrowth` = 2.145
- **手算** = 0.1391
- **引擎** = 0.13909622377622377
- **结果** = pass
- **备注** = 与正式 PEG 口径不同；验算漏斗自身是否自洽

</details>

---

## META

> Meta Platforms, Inc. · Communication Services · 现价 582.9 USD · TTM P/E 21.18866

### 阶段 1：原始数据质检 · META

| 级别 | 维度 | 字段 | 说明 |
|------|------|------|------|
| **WARN** | cross_source | earnings_growth | 增速来源打架：info.earningsGrowth=62.4% vs 年表 YoY=-3.1% |
| **INFO** | cross_source | peg | 漏斗 quick_peg=0.34 vs 正式 PEG=0.53（口径不同，非必然错误） |
| **WARN** | plausibility | dividend_yield | dividendYield=0.3600 在 (0,1) 区间，疑为小数形式（应为百分比数值如 1.51） |

**质检结论**：✅ 可信 · score **84** · 0 fail / 2 warn

<details><summary>字段溯源</summary>

| 字段 | Yahoo 来源 |
|------|-----------|
| `dividend_yield` | info.dividendYield |
| `earnings_growth_yoy` | info.earningsGrowth（单季同比，非 CAGR） |
| `eps_series` | income_stmt.Diluted EPS | Basic EPS（年度列） |
| `forward_pe` | info.forwardPE |
| `free_cashflow` | info.freeCashflow | cashflow.Free Cash Flow |
| `inventory_series` | balance_sheet.Inventory（年度列） |
| `long_term_debt` | balance.Long Term Debt | info.longTermDebt |
| `market_cap` | info.marketCap |
| `net_income_series` | income_stmt.Net Income（年度列） |
| `price` | info.regularMarketPrice | info.currentPrice |
| `quote_time` | info.regularMarketTime=1783022400 |
| `revenue_growth_yoy` | info.revenueGrowth |
| `revenue_series` | income_stmt.Total Revenue（年度列） |
| `shares_outstanding` | info.sharesOutstanding |
| `stockholders_equity` | balance.Stockholders Equity |
| `total_cash` | info.totalCash | balance.Cash And Cash Equivalents |
| `total_debt` | info.totalDebt | balance.Total Debt |
| `trailing_pe` | info.trailingPE |

</details>

### 阶段 2：参数计算验算 · META

| 指标 | 公式 | 手算 | 引擎 | 结果 |
|------|------|------|------|------|
| 长期增长率 (CAGR) | `(末值/初值)^(1/年数) - 1；优先 EPS 序列，其次净利润…` | 0.398395285030712 | 0.398395285030712 | ✅ |
| 股息修正 PEG | `P/E ÷ (capped_CAGR×100 + dividend_yield_…` | 0.5271 | 0.5270872766177148 | ✅ |
| 长期负债 / 股东权益 | `long_term_debt / stockholders_equity（金融股…` | 0.2704 | 0.27 | ✅ |
| 存货增速 − 销售增速 (百分点) | `YoY(inventory) - YoY(revenue)，再 ×100…` | — | — | ⏭ |
| 每股净现金 | `(total_cash - total_debt) / shares_outst…` | -2.55 | -2.55 | ✅ |
| 自由现金流 (绝对值) | `info.freeCashflow | cashflow.Free Cash F…` | 25558249472 | 25558249472 | ✅ |
| FCF / 市值 | `free_cashflow / market_cap…` | 0.0173 | 0.0173 | ✅ |
| SBI/NISA 可交易 | `主板 + 市值≥3亿美元，排除 OTC…` | True | True | ✅ |
| 漏斗 quick_peg（粗筛口径） | `P/E ÷ (info.earningsGrowth × 100)；无股息修正…` | 0.3396 | 0.339561858974359 | ✅ |

**验算结论**：8/9 通过，score 89，全部一致 ✅

<details><summary>展开计算明细</summary>

#### 长期增长率 (CAGR) (`cagr`)
- **公式**：(末值/初值)^(1/年数) - 1；优先 EPS 序列，其次净利润
- **输入**：
  - `basis` = 4年摊薄EPS复合增长率(CAGR)
  - `eps_first` = 8.59
  - `eps_last` = 23.49
  - `eps_span_years` = 3
- **手算** = 0.398395285030712
- **引擎** = 0.398395285030712
- **结果** = pass
- **备注** = 4年摊薄EPS复合增长率(CAGR)

#### 股息修正 PEG (`peg`)
- **公式**：P/E ÷ (capped_CAGR×100 + dividend_yield_pct)
- **输入**：
  - `P/E` = 21.18866
  - `CAGR (decimal)` = 0.398395285030712
  - `dividend_yield (pct pts)` = 0.36
  - `valuation_pe` = None
  - `trailing_pe` = 21.18866
  - `capped_growth (decimal)` = 0.398395285030712
  - `denominator (pct pts)` = 40.1995285030712
- **手算** = 0.5271
- **引擎** = 0.5270872766177148
- **结果** = pass
- **备注** = ⚠ dividendYield 可能为小数形式，PEG 分母或偏小

#### 长期负债 / 股东权益 (`debt`)
- **公式**：long_term_debt / stockholders_equity（金融股豁免）
- **输入**：
  - `long_term_debt` = 58744000000.0
  - `stockholders_equity` = 217243000000.0
  - `financial` = False
- **手算** = 0.2704
- **引擎** = 0.27
- **结果** = pass

#### 存货增速 − 销售增速 (百分点) (`inventory`)
- **公式**：YoY(inventory) - YoY(revenue)，再 ×100
- **输入**：
  - `inventory_yoy` = None
  - `revenue_yoy` = 0.2216703849824621
  - `inventory_years` = []
  - `revenue_years` = [2022, 2023, 2024, 2025]
- **手算** = None
- **引擎** = None
- **结果** = skip

#### 每股净现金 (`net_cash`)
- **公式**：(total_cash - total_debt) / shares_outstanding
- **输入**：
  - `total_cash` = 81180000256
  - `total_debt` = 86769000448
  - `shares` = 2196045588
- **手算** = -2.55
- **引擎** = -2.55
- **结果** = pass

#### 自由现金流 (绝对值) (`fcf`)
- **公式**：info.freeCashflow | cashflow.Free Cash Flow
- **输入**：
  - `free_cashflow` = 25558249472
  - `market_cap` = 1479647035392
- **手算** = 25558249472
- **引擎** = 25558249472
- **结果** = pass

#### FCF / 市值 (`fcf_yield`)
- **公式**：free_cashflow / market_cap
- **输入**：
  - `free_cashflow` = 25558249472
  - `market_cap` = 1479647035392
- **手算** = 0.0173
- **引擎** = 0.0173
- **结果** = pass
- **备注** = 展示值（引擎写在 verdict 文案中）

#### SBI/NISA 可交易 (`sbi_tradable`)
- **公式**：主板 + 市值≥3亿美元，排除 OTC
- **输入**：
  - `exchange` = NMS
  - `market_cap` = 1479647035392
  - `ticker` = META
- **手算** = True
- **引擎** = True
- **结果** = pass

#### 漏斗 quick_peg（粗筛口径） (`quick_peg`)
- **公式**：P/E ÷ (info.earningsGrowth × 100)；无股息修正
- **输入**：
  - `P/E` = 21.18866
  - `earningsGrowth` = 0.624
- **手算** = 0.3396
- **引擎** = 0.339561858974359
- **结果** = pass
- **备注** = 与正式 PEG 口径不同；验算漏斗自身是否自洽

</details>

---

## 附录：验证方法论

### 阶段 1 — 原始数据是否干净
1. **完整性**：按 report_mode 检查必填字段
2. **新鲜度**：日 K 末根 vs 最近美股交易日；年表最新财年滞后
3. **内部自洽**：市值 ≈ price×shares；P/E 与 price/EPS 一致
4. **跨源一致**：info.debtToEquity vs 年表 ltd/equity；info.earningsGrowth vs 年表 YoY
5. **合理性**：dividendYield 单位、极端 P/E/存货
6. **溯源**：每个关键字段标注 Yahoo 路径

### 阶段 2 — 参数计算是否正确
在阶段 1 原始值基础上，独立展开公式手算，与 `compute_metrics()` 引擎输出逐项对比。
含：CAGR、股息修正 PEG、负债比、存货差、每股净现金、FCF、SBI 可交易、漏斗 quick_peg。

### 判定标准
- 阶段 1 **FAIL** → `trusted: NO`，不建议进 Gemini
- 阶段 2 数值容差：PEG ±1.5%相对误差；比率类 ±2%
- `quick_peg` 与正式 PEG 口径不同，仅验算漏斗自洽

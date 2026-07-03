export type UiStatus = "danger" | "ambush" | "signal" | "error";

export interface WatchlistItem {
  ticker: string;
  name: string;
  market: string;
  tier: number;
  note: string;
}

export interface StatusItem extends WatchlistItem {
  ui_status: UiStatus;
  alert_type: string;
  message: string;
  error: string | null;
  price: number | null;
  ma_weekly: number | null;
  ma_daily: number | null;
  deviation_pct: number | null;
  daily_gap_pct: number | null;
  market_index: string | null;
  market_is_downtrend: boolean | null;
}

export interface AlertHistoryItem {
  id: number;
  ticker: string;
  name: string;
  market: string;
  tier: number;
  alert_type: string;
  title: string;
  body: string;
  price: number | null;
  deviation_pct: number | null;
  created_at: string;
}

export interface WatchlistCreate {
  ticker: string;
  name: string;
  market: "JP" | "US";
  tier: 1 | 2;
  note?: string;
}

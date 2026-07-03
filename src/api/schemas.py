"""Pydantic schemas for the REST API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class WatchlistItem(BaseModel):
    ticker: str
    name: str
    market: str
    tier: int
    note: str = ""


class WatchlistCreate(BaseModel):
    ticker: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    market: str = Field(..., pattern="^(JP|US|jp|us)$")
    tier: int = Field(..., ge=1, le=2)
    note: str = ""


class StatusItem(BaseModel):
    ticker: str
    name: str
    market: str
    tier: int
    note: str
    ui_status: str
    alert_type: str
    message: str
    error: str | None = None
    price: float | None = None
    ma_weekly: float | None = None
    ma_daily: float | None = None
    deviation_pct: float | None = None
    daily_gap_pct: float | None = None
    market_index: str | None = None
    market_is_downtrend: bool | None = None


class AlertHistoryItem(BaseModel):
    id: int
    ticker: str
    name: str
    market: str
    tier: int
    alert_type: str
    title: str
    body: str
    price: float | None = None
    deviation_pct: float | None = None
    created_at: str


class PushTokenCreate(BaseModel):
    expo_push_token: str = Field(..., min_length=10)
    device_label: str = ""


class ScanRequest(BaseModel):
    market: str = "ALL"
    dry_run: bool = False


class ScanResponse(BaseModel):
    scanned: int
    signals: int
    pushed: int
    skipped: int
    errors: int
    items: list[StatusItem]


class AnalyzeRequest(BaseModel):
    ticker: str = Field(..., min_length=1)
    note: str = ""
    data_only: bool = False


class LynchMetricItem(BaseModel):
    key: str
    label: str
    value: float | None = None
    flag: str
    verdict: str


class AnalyzeResponse(BaseModel):
    ticker: str
    name: str | None = None
    sector: str | None = None
    peg: float | None = None
    growth_basis: str
    metrics: list[LynchMetricItem]
    data_block: str
    narrative: str | None = None

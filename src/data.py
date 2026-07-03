"""Fetch market data via yfinance with adjusted prices."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import yfinance as yf

from .config_loader import StrategyConfig


@dataclass(frozen=True)
class PriceSnapshot:
    ticker: str
    price: float
    ma_weekly: float
    ma_daily: float
    deviation_pct: float
    low: float | None = None


@dataclass(frozen=True)
class MarketSnapshot:
    index: str
    name: str
    price: float
    ma_daily: float
    is_downtrend: bool


class DataFetchError(Exception):
    """Raised when required market data cannot be retrieved."""


def _require_adj_close(history: pd.DataFrame, label: str) -> pd.Series:
    if history.empty:
        raise DataFetchError(f"{label}: no price history returned")

    if "Adj Close" in history.columns:
        series = history["Adj Close"]
    elif "Close" in history.columns:
        series = history["Close"]
    else:
        raise DataFetchError(f"{label}: missing Close / Adj Close column")

    cleaned = series.dropna()
    if cleaned.empty:
        raise DataFetchError(f"{label}: all prices are NaN")
    return cleaned


def _sma(series: pd.Series, window: int) -> float:
    if len(series) < window:
        raise DataFetchError(
            f"insufficient history: need {window} bars, got {len(series)}"
        )
    return float(series.rolling(window=window).mean().iloc[-1])


def fetch_stock_snapshot(
    ticker: str,
    strategy: StrategyConfig,
) -> PriceSnapshot:
    daily = yf.Ticker(ticker).history(period="2y", interval="1d", auto_adjust=False)
    daily_close = _require_adj_close(daily, ticker)

    weekly = yf.Ticker(ticker).history(period="5y", interval="1wk", auto_adjust=False)
    weekly_close = _require_adj_close(weekly, f"{ticker} (weekly)")

    price = float(daily_close.iloc[-1])
    ma_daily = _sma(daily_close, strategy.ma_days)
    ma_weekly = _sma(weekly_close, strategy.ma_weeks)
    deviation_pct = (price - ma_weekly) / ma_weekly * 100.0

    low = float(daily["Low"].iloc[-1]) if "Low" in daily.columns else None

    return PriceSnapshot(
        ticker=ticker,
        price=price,
        ma_weekly=ma_weekly,
        ma_daily=ma_daily,
        deviation_pct=deviation_pct,
        low=low,
    )


def fetch_market_snapshot(
    index_ticker: str,
    name: str,
    ma_days: int,
) -> MarketSnapshot:
    daily = yf.Ticker(index_ticker).history(
        period="6mo", interval="1d", auto_adjust=False
    )
    closes = _require_adj_close(daily, index_ticker)
    price = float(closes.iloc[-1])
    ma_daily = _sma(closes, ma_days)

    return MarketSnapshot(
        index=index_ticker,
        name=name,
        price=price,
        ma_daily=ma_daily,
        is_downtrend=price < ma_daily,
    )

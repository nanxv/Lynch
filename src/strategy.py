"""Iron Rule 2.5 strategy: three-phase scan logic."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .config_loader import AppConfig, StockEntry
from .data import DataFetchError, MarketSnapshot, PriceSnapshot, fetch_market_snapshot, fetch_stock_snapshot


class AlertType(str, Enum):
    EXCLUDED = "excluded"
    WATCHING = "watching"
    TIER2_BUY = "tier2_buy"
    TIER1_CORE = "tier1_core"


@dataclass(frozen=True)
class ScanResult:
    stock: StockEntry
    snapshot: PriceSnapshot | None
    alert_type: AlertType
    market: MarketSnapshot | None
    message: str
    error: str | None = None


def _fmt_money(value: float, market: str) -> str:
    if market == "JP":
        return f"¥{value:,.0f}"
    return f"${value:,.2f}"


def _phase1(snapshot: PriceSnapshot, exclude_pct: float) -> tuple[bool, str]:
    if snapshot.deviation_pct > exclude_pct:
        return False, (
            f"高位排雷：偏离周线 +{snapshot.deviation_pct:.1f}% "
            f"(阈值 {exclude_pct:.0f}%)"
        )
    return True, (
        f"进入伏击圈：偏离周线 {snapshot.deviation_pct:+.1f}% "
        f"(≤ {exclude_pct:.0f}%)"
    )


def _tier1_trigger(
    snapshot: PriceSnapshot,
    min_pct: float,
    max_pct: float,
) -> tuple[bool, str]:
    in_band = min_pct <= snapshot.deviation_pct <= max_pct
    detail = (
        f"周线偏离 {snapshot.deviation_pct:+.1f}% "
        f"(目标区间 {min_pct:+.0f}% ~ {max_pct:+.0f}%)"
    )
    return in_band, detail


def _tier2_trigger(
    snapshot: PriceSnapshot,
    market: MarketSnapshot,
) -> tuple[bool, str]:
    price_hit = snapshot.price <= snapshot.ma_daily
    low_touched = snapshot.low is not None and snapshot.low <= snapshot.ma_daily
    near_close = abs(snapshot.price - snapshot.ma_daily) / snapshot.ma_daily <= 0.01

    tactical = (price_hit or low_touched) and (price_hit or near_close)

    if not market.is_downtrend:
        return False, (
            f"日线条件未满足：{market.name} 未处跌势 "
            f"({market.price:,.0f} vs MA20 {market.ma_daily:,.0f})"
        )

    if not tactical:
        gap_daily = (snapshot.price - snapshot.ma_daily) / snapshot.ma_daily * 100
        return False, (
            f"大盘跌势中，但股价未击穿日线 MA20 "
            f"(偏离 {gap_daily:+.1f}%)"
        )

    return True, (
        f"战术血坑触发：{market.name} 处跌势，"
        f"股价击穿/触及日线 MA20"
    )


def scan_stock(stock: StockEntry, config: AppConfig) -> ScanResult:
    strategy = config.strategy

    try:
        snapshot = fetch_stock_snapshot(stock.ticker, strategy)
    except DataFetchError as exc:
        return ScanResult(
            stock=stock,
            snapshot=None,
            alert_type=AlertType.EXCLUDED,
            market=None,
            message="",
            error=f"{stock.ticker}: {exc}",
        )

    market_cfg = config.markets.get(stock.market)
    if market_cfg is None:
        return ScanResult(
            stock=stock,
            snapshot=snapshot,
            alert_type=AlertType.EXCLUDED,
            market=None,
            message="",
            error=f"{stock.ticker}: unknown market '{stock.market}'",
        )

    try:
        market = fetch_market_snapshot(
            market_cfg.index,
            market_cfg.name,
            strategy.ma_days,
        )
    except DataFetchError as exc:
        return ScanResult(
            stock=stock,
            snapshot=snapshot,
            alert_type=AlertType.EXCLUDED,
            market=None,
            message="",
            error=f"{market_cfg.index}: {exc}",
        )

    passed, phase1_msg = _phase1(snapshot, strategy.deviation_exclude_pct)
    if not passed:
        return ScanResult(
            stock=stock,
            snapshot=snapshot,
            alert_type=AlertType.EXCLUDED,
            market=market,
            message=phase1_msg,
        )

    if stock.tier == 1:
        triggered, detail = _tier1_trigger(
            snapshot,
            strategy.tier1_deviation_min_pct,
            strategy.tier1_deviation_max_pct,
        )
        if triggered:
            return ScanResult(
                stock=stock,
                snapshot=snapshot,
                alert_type=AlertType.TIER1_CORE,
                market=market,
                message=f"【核心标的阻击警报】{detail}",
            )
        return ScanResult(
            stock=stock,
            snapshot=snapshot,
            alert_type=AlertType.WATCHING,
            market=market,
            message=f"伏击圈中，等待周线回踩：{detail}",
        )

    triggered, detail = _tier2_trigger(snapshot, market)
    if triggered:
        return ScanResult(
            stock=stock,
            snapshot=snapshot,
            alert_type=AlertType.TIER2_BUY,
            market=market,
            message=f"【常规买入警报】{detail}",
        )

    return ScanResult(
        stock=stock,
        snapshot=snapshot,
        alert_type=AlertType.WATCHING,
        market=market,
        message=f"伏击圈中，{detail}",
    )


def scan_all(config: AppConfig) -> list[ScanResult]:
    return [scan_stock(stock, config) for stock in config.stocks]

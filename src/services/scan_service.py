"""Shared scan orchestration for CLI and API."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from ..alert_cache import alert_cache_key
from ..config_loader import AppConfig, StockEntry, load_config_from_db
from ..db.database import Database
from ..db.repository import AlertHistoryRepository, DedupRepository, PushTokenRepository
from ..notifier import build_alert
from ..services.push import send_expo_push
from ..strategy import AlertType, ScanResult, scan_all


@dataclass
class ScanSummary:
    scanned: int
    signals: int
    pushed: int
    skipped: int
    errors: int


def _ui_status(result: ScanResult) -> str:
    if result.error:
        return "error"
    if result.alert_type in (AlertType.TIER1_CORE, AlertType.TIER2_BUY):
        return "signal"
    if result.alert_type == AlertType.EXCLUDED:
        snap = result.snapshot
        if snap and snap.deviation_pct > 15:
            return "danger"
    if result.alert_type in (AlertType.WATCHING, AlertType.EXCLUDED):
        return "ambush"
    return "ambush"


def result_to_status_item(result: ScanResult) -> dict:
    snap = result.snapshot
    market = result.market
    stock = result.stock
    return {
        "ticker": stock.ticker,
        "name": stock.name,
        "market": stock.market,
        "tier": stock.tier,
        "note": stock.note,
        "ui_status": _ui_status(result),
        "alert_type": result.alert_type.value,
        "message": result.message,
        "error": result.error,
        "price": snap.price if snap else None,
        "ma_weekly": snap.ma_weekly if snap else None,
        "ma_daily": snap.ma_daily if snap else None,
        "deviation_pct": snap.deviation_pct if snap else None,
        "daily_gap_pct": (
            (snap.price - snap.ma_daily) / snap.ma_daily * 100 if snap else None
        ),
        "market_index": market.name if market else None,
        "market_is_downtrend": market.is_downtrend if market else None,
    }


def filter_market(config: AppConfig, market: str | None) -> AppConfig:
    if not market or market.upper() == "ALL":
        return config
    code = market.upper()
    stocks = [s for s in config.stocks if s.market.upper() == code]
    return AppConfig(
        strategy=config.strategy,
        markets=config.markets,
        notifications=config.notifications,
        stocks=stocks,
    )


def run_scan(
    *,
    market: str | None = None,
    dry_run: bool = False,
    use_telegram: bool = True,
    use_expo: bool = True,
    db: Database | None = None,
) -> tuple[list[ScanResult], ScanSummary, list[dict]]:
    database = db or Database()
    database.init()

    config = filter_market(load_config_from_db(database.path), market)
    results = scan_all(config)

    dedup = DedupRepository(database, ttl_hours=config.notifications.dedup_ttl_hours)
    history = AlertHistoryRepository(database)
    push_tokens = PushTokenRepository(database)

    pushed = 0
    skipped = 0
    footer = config.notifications.footer_hint

    for result in results:
        payload = build_alert(result, footer)
        if payload is None or not payload.is_signal:
            continue

        cache_key = alert_cache_key(result)
        if cache_key and dedup.was_sent_recently(cache_key):
            skipped += 1
            continue

        if dry_run:
            continue

        snap = result.snapshot
        history.add(
            ticker=result.stock.ticker,
            name=result.stock.name,
            market=result.stock.market,
            tier=result.stock.tier,
            alert_type=result.alert_type.value,
            title=payload.title,
            body=payload.body,
            price=snap.price if snap else None,
            deviation_pct=snap.deviation_pct if snap else None,
        )

        delivered = False
        if use_expo:
            ok, _ = send_expo_push(
                push_tokens.list_tokens(),
                title=payload.title,
                body=payload.body,
                data={
                    "ticker": result.stock.ticker,
                    "alert_type": result.alert_type.value,
                },
            )
            delivered = delivered or ok > 0

        if use_telegram and config.notifications.telegram_enabled:
            from ..notifier import send_telegram

            delivered = delivered or send_telegram(payload.body)

        if delivered and cache_key:
            dedup.mark_sent(cache_key)
            pushed += 1

    signals = sum(
        1
        for r in results
        if r.alert_type in (AlertType.TIER1_CORE, AlertType.TIER2_BUY) and not r.error
    )
    errors = sum(1 for r in results if r.error)
    summary = ScanSummary(
        scanned=len(results),
        signals=signals,
        pushed=pushed,
        skipped=skipped,
        errors=errors,
    )
    status_items = [result_to_status_item(r) for r in results]
    return results, summary, status_items

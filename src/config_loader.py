"""Load YAML config and watchlist."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class StrategyConfig:
    ma_weeks: int
    ma_days: int
    deviation_exclude_pct: float
    tier1_deviation_min_pct: float
    tier1_deviation_max_pct: float


@dataclass(frozen=True)
class MarketConfig:
    index: str
    name: str


@dataclass(frozen=True)
class NotificationConfig:
    telegram_enabled: bool
    footer_hint: str
    dedup_ttl_hours: float = 24.0
    alert_cache_path: str = ".alert_cache.json"


@dataclass(frozen=True)
class StockEntry:
    ticker: str
    name: str
    market: str
    tier: int
    note: str = ""


@dataclass(frozen=True)
class AppConfig:
    strategy: StrategyConfig
    markets: dict[str, MarketConfig]
    notifications: NotificationConfig
    stocks: list[StockEntry]


def load_config(
    config_path: Path | None = None,
    watchlist_path: Path | None = None,
    stocks: list[StockEntry] | None = None,
) -> AppConfig:
    config_path = config_path or ROOT / "config.yaml"
    watchlist_path = watchlist_path or ROOT / "watchlist.yaml"

    with config_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if stocks is None:
        with watchlist_path.open(encoding="utf-8") as f:
            watchlist = yaml.safe_load(f)
        stocks = [StockEntry(**s) for s in watchlist["stocks"]]

    strategy = StrategyConfig(**raw["strategy"])
    markets = {k: MarketConfig(**v) for k, v in raw["markets"].items()}
    notifications = NotificationConfig(**raw["notifications"])

    return AppConfig(
        strategy=strategy,
        markets=markets,
        notifications=notifications,
        stocks=stocks,
    )


def load_config_from_db(db_path: Path | None = None) -> AppConfig:
    from .db.database import Database
    from .db.repository import WatchlistRepository

    database = Database(db_path or Database().path)
    database.init()
    watchlist = WatchlistRepository(database)
    watchlist.seed_from_yaml_if_empty()
    return load_config(stocks=watchlist.list_stocks())

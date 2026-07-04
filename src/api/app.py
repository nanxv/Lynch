"""FastAPI application for the Iron Rule 2.5 monitor."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from ..config_loader import StockEntry
from ..db.database import Database
from ..db.repository import AlertHistoryRepository, PushTokenRepository, WatchlistRepository
from ..lynch import analyze_company
from ..lynch.fundamentals import FundamentalsError
from ..lynch.llm import LLMError
from ..services.scan_service import run_scan
from .schemas import (
    AlertHistoryItem,
    AnalyzeRequest,
    AnalyzeResponse,
    LynchMetricItem,
    PushTokenCreate,
    ScanRequest,
    ScanResponse,
    StatusItem,
    WatchlistCreate,
    WatchlistItem,
)

db = Database()
db.init()
watchlist_repo = WatchlistRepository(db)
watchlist_repo.seed_from_yaml_if_empty()
alert_repo = AlertHistoryRepository(db)
push_repo = PushTokenRepository(db)

app = FastAPI(
    title="Iron Rule 2.5 Monitor API",
    version="0.2.0",
    description="Backend brain for the stock monitoring mobile app.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "watchlist_count": len(watchlist_repo.list_stocks())}


@app.get("/api/watchlist", response_model=list[WatchlistItem])
def get_watchlist() -> list[WatchlistItem]:
    return [WatchlistItem(**stock.__dict__) for stock in watchlist_repo.list_stocks()]


@app.post("/api/watchlist", response_model=WatchlistItem, status_code=201)
def add_watchlist_item(payload: WatchlistCreate) -> WatchlistItem:
    entry = watchlist_repo.add_stock(
        StockEntry(
            ticker=payload.ticker.upper(),
            name=payload.name,
            market=payload.market.upper(),
            tier=payload.tier,
            note=payload.note,
        )
    )
    return WatchlistItem(**entry.__dict__)


@app.delete("/api/watchlist/{ticker}")
def delete_watchlist_item(ticker: str) -> dict:
    removed = watchlist_repo.remove_stock(ticker)
    if not removed:
        raise HTTPException(status_code=404, detail=f"{ticker} not in watchlist")
    return {"removed": ticker.upper()}


@app.get("/api/status", response_model=list[StatusItem])
def get_status(market: str = "ALL") -> list[StatusItem]:
    _, _, items = run_scan(market=market, dry_run=True, use_telegram=False, use_expo=False, db=db)
    return [StatusItem(**item) for item in items]


@app.get("/api/alerts", response_model=list[AlertHistoryItem])
def get_alerts(limit: int = 100) -> list[AlertHistoryItem]:
    rows = alert_repo.list_recent(limit=limit)
    return [AlertHistoryItem(**row) for row in rows]


@app.post("/api/scan", response_model=ScanResponse)
def trigger_scan(payload: ScanRequest) -> ScanResponse:
    _, summary, items = run_scan(
        market=payload.market,
        dry_run=payload.dry_run,
        db=db,
    )
    return ScanResponse(
        scanned=summary.scanned,
        signals=summary.signals,
        pushed=summary.pushed,
        skipped=summary.skipped,
        errors=summary.errors,
        items=[StatusItem(**item) for item in items],
    )


@app.post("/api/analyze", response_model=AnalyzeResponse)
def analyze(payload: AnalyzeRequest) -> AnalyzeResponse:
    """彼得·林奇 SOP 分析一家公司（基本面 + 硬指标 + Gemini 叙述）。"""
    try:
        result = analyze_company(
            payload.ticker, user_note=payload.note, data_only=payload.data_only
        )
    except FundamentalsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return AnalyzeResponse(
        ticker=result.ticker,
        name=result.fundamentals.name,
        sector=result.fundamentals.sector,
        peg=result.metrics.peg,
        growth_basis=result.metrics.growth_basis,
        metrics=[
            LynchMetricItem(
                key=m.key, label=m.label, value=m.value, flag=m.flag, verdict=m.verdict
            )
            for m in result.metrics.metrics
        ],
        data_block=result.data_block,
        narrative=result.narrative,
    )


@app.post("/api/push-tokens", status_code=201)
def register_push_token(payload: PushTokenCreate) -> dict:
    push_repo.register(payload.expo_push_token, payload.device_label)
    return {"registered": True, "token_count": len(push_repo.list_tokens())}


@app.delete("/api/push-tokens")
def unregister_push_token(expo_push_token: str) -> dict:
    removed = push_repo.remove(expo_push_token)
    if not removed:
        raise HTTPException(status_code=404, detail="push token not found")
    return {"removed": True}

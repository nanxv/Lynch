"""Alert delivery: terminal and Telegram."""

from __future__ import annotations

import os
from dataclasses import dataclass

import requests
from dotenv import load_dotenv

from .alert_cache import AlertCache, ROOT as CACHE_ROOT
from .config_loader import AppConfig
from .strategy import AlertType, ScanResult

load_dotenv()


@dataclass(frozen=True)
class AlertPayload:
    title: str
    body: str
    is_signal: bool


def _fmt_money(value: float, market: str) -> str:
    if market == "JP":
        return f"¥{value:,.0f}"
    return f"${value:,.2f}"


def build_alert(result: ScanResult, footer: str) -> AlertPayload | None:
    if result.error:
        return AlertPayload(
            title="数据错误",
            body=f"❌ {result.stock.name} ({result.stock.ticker})\n{result.error}",
            is_signal=False,
        )

    if result.alert_type in (AlertType.TIER1_CORE, AlertType.TIER2_BUY):
        snap = result.snapshot
        assert snap is not None
        market = result.stock.market
        gap_weekly = snap.deviation_pct
        gap_daily = (snap.price - snap.ma_daily) / snap.ma_daily * 100

        tier_label = f"Tier {result.stock.tier}"
        signal = "核心阻击" if result.alert_type == AlertType.TIER1_CORE else "常规买入"

        body = (
            f"🚨 {signal} | {result.stock.name} ({result.stock.ticker})\n"
            f"类别: {tier_label} | 市场: {market}\n"
            f"现价: {_fmt_money(snap.price, market)}\n"
            f"10周均线: {_fmt_money(snap.ma_weekly, market)} "
            f"(偏离 {gap_weekly:+.1f}%)\n"
            f"20日均线: {_fmt_money(snap.ma_daily, market)} "
            f"(偏离 {gap_daily:+.1f}%)\n"
            f"{result.message}\n"
            f"\n{footer}"
        )
        return AlertPayload(title=f"{signal}: {result.stock.ticker}", body=body, is_signal=True)

    return None


def format_status_report(results: list[ScanResult], config: AppConfig) -> str:
    lines = ["=" * 60, "铁律 2.5 扫描报告", "=" * 60, ""]

    for result in results:
        stock = result.stock
        lines.append(f"▸ {stock.name} ({stock.ticker}) | Tier {stock.tier} | {stock.market}")

        if result.error:
            lines.append(f"  ❌ 错误: {result.error}")
            lines.append("")
            continue

        snap = result.snapshot
        assert snap is not None
        market = result.market
        money = stock.market

        lines.append(f"  现价: {_fmt_money(snap.price, money)}")
        lines.append(
            f"  10周均线: {_fmt_money(snap.ma_weekly, money)} "
            f"(D = {snap.deviation_pct:+.1f}%)"
        )
        lines.append(
            f"  20日均线: {_fmt_money(snap.ma_daily, money)} "
            f"({(snap.price - snap.ma_daily) / snap.ma_daily * 100:+.1f}%)"
        )

        if market:
            trend = "跌势" if market.is_downtrend else "非跌势"
            lines.append(
                f"  大盘 {market.name}: {market.price:,.0f} "
                f"/ MA20 {market.ma_daily:,.0f} → {trend}"
            )

        status_map = {
            AlertType.EXCLUDED: "⛔ 剔除",
            AlertType.WATCHING: "👀 观察中",
            AlertType.TIER2_BUY: "🚨 常规买入信号",
            AlertType.TIER1_CORE: "⚡ 核心阻击信号",
        }
        lines.append(f"  状态: {status_map[result.alert_type]}")
        lines.append(f"  {result.message}")
        lines.append("")

    signals = [r for r in results if r.alert_type in (AlertType.TIER1_CORE, AlertType.TIER2_BUY)]
    errors = [r for r in results if r.error]
    lines.append("-" * 60)
    lines.append(f"信号数: {len(signals)} | 错误数: {len(errors)} | 监控数: {len(results)}")
    lines.append("-" * 60)

    return "\n".join(lines)


def print_report(results: list[ScanResult], config: AppConfig) -> None:
    print(format_status_report(results, config))


def _telegram_chat_ids() -> list[str]:
    raw = os.getenv("TELEGRAM_CHAT_IDS", "").strip()
    single = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    ids = [part.strip() for part in raw.split(",") if part.strip()]
    if single:
        ids.append(single)
    # Preserve order, drop duplicates.
    seen: set[str] = set()
    unique: list[str] = []
    for chat_id in ids:
        if chat_id not in seen:
            seen.add(chat_id)
            unique.append(chat_id)
    return unique


def _send_telegram_once(token: str, chat_id: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = requests.post(
        url,
        json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        timeout=15,
    )
    if response.ok:
        return True

    response = requests.post(
        url,
        json={"chat_id": chat_id, "text": text},
        timeout=15,
    )
    return response.ok


def send_telegram(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids = _telegram_chat_ids()

    if not token or not chat_ids:
        print(
            "⚠️  Telegram 未配置：请在 .env 中设置 TELEGRAM_BOT_TOKEN，"
            "以及 TELEGRAM_CHAT_ID 或 TELEGRAM_CHAT_IDS"
        )
        return False

    ok = 0
    for chat_id in chat_ids:
        if _send_telegram_once(token, chat_id, text):
            ok += 1
        else:
            print(f"⚠️  Telegram 发送失败 (chat_id={chat_id})")

    return ok == len(chat_ids)


def notify_signals(results: list[ScanResult], config: AppConfig) -> tuple[int, int]:
    footer = config.notifications.footer_hint
    cache_path = CACHE_ROOT / config.notifications.alert_cache_path
    cache = AlertCache(
        path=cache_path,
        ttl_hours=config.notifications.dedup_ttl_hours,
    )
    sent = 0
    skipped = 0

    for result in results:
        if result.error:
            payload = build_alert(result, footer)
            if payload:
                print(payload.body)
            continue

        payload = build_alert(result, footer)
        if payload is None:
            continue

        print(payload.body)

        if config.notifications.telegram_enabled:
            if not cache.should_send(result):
                skipped += 1
                print(
                    f"🔕 Telegram 跳过：{result.stock.ticker} 买入信号在 "
                    f"{config.notifications.dedup_ttl_hours:.0f}h 内已推送"
                )
            elif send_telegram(payload.body):
                cache.mark_sent(result)
                sent += 1

        print("-" * 40)

    return sent, skipped

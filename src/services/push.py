"""Expo Push Notification delivery."""

from __future__ import annotations

import os

import requests

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


def send_expo_push(
    tokens: list[str],
    *,
    title: str,
    body: str,
    data: dict | None = None,
) -> tuple[int, int]:
    if not tokens:
        return 0, 0

    messages = [
        {
            "to": token,
            "title": title,
            "body": body,
            "sound": "default",
            "priority": "high",
            "data": data or {},
        }
        for token in tokens
    ]

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    access_token = os.getenv("EXPO_ACCESS_TOKEN", "").strip()
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    response = requests.post(EXPO_PUSH_URL, json=messages, headers=headers, timeout=15)
    if not response.ok:
        print(f"⚠️  Expo Push 失败: {response.status_code} {response.text}")
        return 0, len(tokens)

    payload = response.json()
    tickets = payload.get("data", [])
    ok = sum(1 for ticket in tickets if ticket.get("status") == "ok")
    failed = len(tokens) - ok
    return ok, failed

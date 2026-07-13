"""双 Key 物理分流与按档熔断。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.lynch import llm


@pytest.fixture(autouse=True)
def _reset_circuits():
    llm._gemini_circuit_open["flash"] = False
    llm._gemini_circuit_open["pro"] = False
    llm._gemini_circuit_reason["flash"] = ""
    llm._gemini_circuit_reason["pro"] = ""
    yield
    llm._gemini_circuit_open["flash"] = False
    llm._gemini_circuit_open["pro"] = False


def test_resolve_api_key_routes_flash_and_pro(monkeypatch):
    monkeypatch.setenv("GEMINI_FLASH_API_KEY", "flash-key")
    monkeypatch.setenv("GEMINI_PRO_API_KEY", "pro-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert llm.resolve_api_key(api_tier="flash") == "flash-key"
    assert llm.resolve_api_key(api_tier="pro") == "pro-key"
    assert llm.resolve_api_key(model="gemini-2.5-flash") == "flash-key"
    assert llm.resolve_api_key(model="gemini-2.5-pro") == "pro-key"


def test_resolve_api_key_falls_back_to_legacy(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "legacy-key")
    monkeypatch.delenv("GEMINI_FLASH_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_PRO_API_KEY", raising=False)
    with patch.object(llm.config, "GEMINI_FLASH_API_KEY", ""), patch.object(
        llm.config, "GEMINI_PRO_API_KEY", ""
    ), patch.object(llm.config, "GEMINI_API_KEY", "legacy-key"):
        assert llm.resolve_api_key(api_tier="flash") == "legacy-key"
        assert llm.resolve_api_key(api_tier="pro") == "legacy-key"


def test_circuit_is_per_tier():
    llm.trip_gemini_circuit("flash out", api_tier="flash")
    assert llm.gemini_circuit_is_open("flash")
    assert not llm.gemini_circuit_is_open("pro")
    assert llm.gemini_circuit_is_open()


def test_resolve_api_tier_from_model():
    assert llm.resolve_api_tier("gemini-2.5-flash") == "flash"
    assert llm.resolve_api_tier("gemini-2.5-pro") == "pro"
    assert llm.resolve_api_tier("x", api_tier="pro") == "pro"
    assert llm.resolve_api_tier("gemini-2.5-pro", api_tier="flash") == "flash"

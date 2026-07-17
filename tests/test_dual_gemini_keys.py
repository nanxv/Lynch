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
    llm._pro_deep_unavailable = False
    yield
    llm._gemini_circuit_open["flash"] = False
    llm._gemini_circuit_open["pro"] = False
    llm._pro_deep_unavailable = False


def test_resolve_deep_falls_back_when_pro_unavailable():
    llm.mark_pro_deep_unavailable("limit 0")
    model, tier = llm.resolve_deep_model_and_tier("gemini-2.5-pro")
    assert tier == "flash"
    assert "flash" in model.lower()


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


def test_resolve_api_key_cross_fallback_to_flash(monkeypatch):
    """Pro 档在只有 Flash Key 时，与周报共用同一把。"""
    monkeypatch.setenv("GEMINI_FLASH_API_KEY", "flash-only")
    monkeypatch.delenv("GEMINI_PRO_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with patch.object(llm.config, "GEMINI_PRO_API_KEY", ""), patch.object(
        llm.config, "GEMINI_API_KEY", ""
    ), patch.object(llm.config, "GEMINI_FLASH_API_KEY", "flash-only"):
        assert llm.resolve_api_key(api_tier="pro") == "flash-only"
        cands = llm.iter_api_key_candidates(api_tier="pro")
        assert cands[0] == ("flash-only", "GEMINI_FLASH_API_KEY")


def test_resolve_api_key_prefers_own_then_cross(monkeypatch):
    monkeypatch.setenv("GEMINI_FLASH_API_KEY", "flash-key")
    monkeypatch.setenv("GEMINI_PRO_API_KEY", "pro-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with patch.object(llm.config, "GEMINI_API_KEY", ""), patch.object(
        llm.config, "GEMINI_FLASH_API_KEY", "flash-key"
    ), patch.object(llm.config, "GEMINI_PRO_API_KEY", "pro-key"):
        cands = llm.iter_api_key_candidates(api_tier="pro")
        assert [s for _, s in cands] == ["GEMINI_PRO_API_KEY", "GEMINI_FLASH_API_KEY"]

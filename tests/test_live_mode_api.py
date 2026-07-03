"""Тести /api/start для Live-режиму (§24, §35 "Wire up real order
execution"): реальні гроші дозволені лише на market_mode="live_realtime" і
strategy="classic" — незалежно від того, чи пройдено backtest/підтвердження
(перевірка комбінації йде ПЕРШОЮ, до решти live-гейту)."""
import pytest
from fastapi.testclient import TestClient

import api.main as main


@pytest.mark.parametrize("market_mode,strategy", [
    ("historical", "classic"),
    ("fast_sim", "classic"),
    ("live_realtime", "optimized"),
    ("live_realtime", "dca"),
])
def test_live_mode_rejects_unsafe_combinations_before_other_gates(market_mode, strategy):
    with TestClient(main.app) as client:
        r = client.post("/api/start", json={
            "mode": "live", "market_mode": market_mode, "strategy": strategy,
            "live_confirmed": True, "historical_year": 2023,
        })
    assert r.status_code == 422


def test_live_mode_with_safe_combination_falls_through_to_normal_live_gate():
    """live_realtime + classic не отримує 422 через комбінацію — далі його
    заблокує звичайний Live-гейт (немає backtest/ключів), тобто 403, а не 422."""
    with TestClient(main.app) as client:
        r = client.post("/api/start", json={
            "mode": "live", "market_mode": "live_realtime", "strategy": "classic",
            "live_confirmed": True,
        })
    assert r.status_code == 403


def test_paper_mode_unaffected_by_live_combination_rules():
    with TestClient(main.app) as client:
        r = client.post("/api/start", json={
            "mode": "paper", "market_mode": "historical", "strategy": "optimized",
            "historical_year": 2023, "assets": ["BTC/USDT"],
        })
        assert r.status_code == 200
        client.post("/api/stop")

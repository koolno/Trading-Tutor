"""Тести режиму market_mode="live_realtime" (реальні ціни в реальному темпі,
гроші все одно паперові — див. AGENTS.md п.1, core/session.py SessionConfig)."""
import pytest
from fastapi.testclient import TestClient

import api.main as main
from core.data.providers import Candle, MarketDataProvider, SyntheticProvider
from core.session import Session, SessionConfig


class _CountingProvider(MarketDataProvider):
    """Обгортка над SyntheticProvider, що рахує звернення до fetch_ohlcv —
    потрібно, щоб перевірити троттлінг live_interval_sec без реальної мережі."""
    def __init__(self):
        self.name = "counting"
        self._inner = SyntheticProvider(seed=1, start_price=100, drift=0.001)
        self.calls = 0

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 200) -> list[Candle]:
        self.calls += 1
        return self._inner.fetch_ohlcv(symbol, timeframe, limit)


def test_market_mode_defaults_to_fast_sim():
    assert SessionConfig().market_mode == "fast_sim"


def test_live_realtime_session_uses_injected_provider():
    provider = _CountingProvider()
    session = Session(
        SessionConfig(market_mode="live_realtime", assets=["BTC/USDT"]), provider=provider)
    assert provider.calls == 1  # початкове завантаження історії в __init__
    session.start()
    session.tick()
    assert provider.calls == 2  # перший тік одразу тягне свіжі дані
    assert session.equity_curve  # тік реально обробився, крива капіталу оновилась


def test_live_realtime_throttles_between_fetches():
    provider = _CountingProvider()
    session = Session(
        SessionConfig(market_mode="live_realtime", assets=["BTC/USDT"],
                      live_interval_sec=60), provider=provider)
    session.start()
    session.tick()
    calls_after_first = provider.calls
    session.tick()  # одразу ще раз — інтервал (60с) ще не минув
    assert provider.calls == calls_after_first
    assert "наступне оновлення" in session.last_action.lower()


def test_fast_sim_session_does_not_use_live_tick_path():
    session = Session(SessionConfig(market_mode="fast_sim", assets=["BTC/USDT"]))
    session.start()
    session.tick()
    assert session._last_live_fetch is None


def test_dashboard_exposes_market_mode():
    fast = Session(SessionConfig(assets=["BTC/USDT"]))
    assert fast.dashboard()["market_mode"] == "fast_sim"

    live = Session(
        SessionConfig(market_mode="live_realtime", assets=["BTC/USDT"]),
        provider=_CountingProvider())
    assert live.dashboard()["market_mode"] == "live_realtime"


def test_api_start_rejects_unknown_market_mode():
    with TestClient(main.app) as client:
        r = client.post("/api/start", json={"market_mode": "turbo"})
    assert r.status_code == 422


def test_api_start_live_realtime_uses_default_provider(monkeypatch):
    provider = _CountingProvider()
    monkeypatch.setattr(Session, "_default_live_provider", staticmethod(lambda: provider))
    with TestClient(main.app) as client:
        r = client.post("/api/start", json={
            "mode": "paper", "market_mode": "live_realtime", "assets": ["BTC/USDT"],
        })
        assert r.status_code == 200
        d = client.get("/api/dashboard").json()
    assert d["market_mode"] == "live_realtime"
    assert provider.calls >= 1

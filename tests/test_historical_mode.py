"""Тести режиму market_mode="historical" (реальна історія Binance за обраний
рік, відтворена прискорено — той самий cursor-based playback, що й fast_sim,
але на СПРАВЖНІХ цінах замість synthetic завжди-зростаючого ряду)."""
from fastapi.testclient import TestClient

import api.main as main
from core.data.providers import Candle, MarketDataProvider, SyntheticProvider
from core.session import Session, SessionConfig


class _CountingHistoricalProvider(MarketDataProvider):
    """Імітує HistoricalProvider без мережі: фіксований ряд свічок,
    незалежний від того, скільки разів і з яким limit його попросили."""
    def __init__(self):
        self.name = "counting-historical"
        self._inner = SyntheticProvider(seed=5, start_price=20000, drift=0.0005)
        self.calls = 0

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100_000) -> list[Candle]:
        self.calls += 1
        return self._inner.fetch_ohlcv(symbol, timeframe, min(limit, 1000))


def test_historical_requires_year_via_api():
    with TestClient(main.app) as client:
        r = client.post("/api/start", json={
            "mode": "paper", "market_mode": "historical", "assets": ["BTC/USDT"],
        })
    assert r.status_code == 422


def test_historical_session_uses_injected_provider():
    provider = _CountingHistoricalProvider()
    session = Session(
        SessionConfig(market_mode="historical", historical_year=2022, assets=["BTC/USDT"]),
        provider=provider)
    assert provider.calls == 1  # одне завантаження історії в __init__
    session.start()
    session.tick()
    assert session.equity_curve  # тік реально обробився, крива капіталу оновилась


def test_historical_uses_fast_tick_path_not_live():
    provider = _CountingHistoricalProvider()
    session = Session(
        SessionConfig(market_mode="historical", historical_year=2022, assets=["BTC/USDT"]),
        provider=provider)
    session.start()
    session.tick()
    assert session._last_live_fetch is None  # не троттлиться, як live_realtime
    assert provider.calls == 1  # cursor-based playback не тягне нові дані щотік


def test_dashboard_exposes_historical_year():
    provider = _CountingHistoricalProvider()
    session = Session(
        SessionConfig(market_mode="historical", historical_year=2022, assets=["BTC/USDT"]),
        provider=provider)
    d = session.dashboard()
    assert d["market_mode"] == "historical"
    assert d["historical_year"] == 2022


def test_api_start_historical_uses_default_provider(monkeypatch):
    provider = _CountingHistoricalProvider()
    monkeypatch.setattr(Session, "_default_historical_provider", staticmethod(lambda year: provider))
    with TestClient(main.app) as client:
        r = client.post("/api/start", json={
            "mode": "paper", "market_mode": "historical", "historical_year": 2023,
            "assets": ["BTC/USDT"],
        })
        assert r.status_code == 200
        d = client.get("/api/dashboard").json()
    assert d["market_mode"] == "historical"
    assert d["historical_year"] == 2023
    assert provider.calls >= 1

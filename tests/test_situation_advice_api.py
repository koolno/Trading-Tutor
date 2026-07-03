"""Тести /api/situation і /api/advice (§B1/§B2).

Раніше обидва ендпойнти генерували свічки з synthetic-seed, але просили різну
кількість свічок (200 і 400) з ОДНОГО детермінованого блукання — тому
"поточна ціна" на двох екранах розходилась в один і той самий момент часу.
Тепер обидва читають реальні свічки Binance через спільний кеш; ці тести
перевіряють це на фейковому провайдері (без мережі), як у test_live_realtime.py.
"""
from fastapi.testclient import TestClient

import api.main as main
from core.data.providers import Candle, MarketDataProvider, SyntheticProvider


class _FixedProvider(MarketDataProvider):
    """Імітує реальну біржу: один і той самий ряд свічок незалежно від limit
    (на відміну від SyntheticProvider, чий ряд залежить від довжини запиту)."""
    def __init__(self, candles: list[Candle]):
        self.name = "fixed"
        self._candles = candles

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 200) -> list[Candle]:
        return self._candles[-limit:]


class _FailingProvider(MarketDataProvider):
    def __init__(self):
        self.name = "failing"

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 200) -> list[Candle]:
        raise RuntimeError("біржа недоступна")


def _reset_cache():
    main._candle_cache.clear()


def test_situation_and_advice_agree_on_current_price(monkeypatch):
    _reset_cache()
    fixed_series = SyntheticProvider(seed=1, start_price=60000, drift=0.001).fetch_ohlcv(
        "BTC/USDT", "1h", 400)
    monkeypatch.setattr(main, "_get_live_provider", lambda: _FixedProvider(fixed_series))

    with TestClient(main.app) as client:
        situation = client.get("/api/situation?asset=BTC/USDT").json()
        advice = client.get("/api/advice?asset=BTC/USDT").json()

    assert not situation.get("error")
    assert not advice.get("error")
    assert situation["price"] == advice["price"]


def test_situation_reflects_falling_market(monkeypatch):
    _reset_cache()
    falling_series = SyntheticProvider(seed=2, start_price=60000, drift=-0.01).fetch_ohlcv(
        "BTC/USDT", "1h", 400)
    monkeypatch.setattr(main, "_get_live_provider", lambda: _FixedProvider(falling_series))

    with TestClient(main.app) as client:
        situation = client.get("/api/situation?asset=BTC/USDT").json()

    assert "падає" in situation["headline"] or "падає" in " ".join(situation["details"])


def test_situation_and_advice_report_error_on_exchange_failure(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(main, "_get_live_provider", lambda: _FailingProvider())

    with TestClient(main.app) as client:
        situation = client.get("/api/situation?asset=BTC/USDT")
        advice = client.get("/api/advice?asset=BTC/USDT")

    assert situation.status_code == 200
    assert situation.json()["error"] is True
    assert advice.status_code == 200
    assert advice.json()["error"] is True

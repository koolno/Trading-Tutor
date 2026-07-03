"""Тести HistoricalProvider (§10) — реальна історія за рік з CSV-кешем.
Без мережі: підміняємо CcxtProvider фейковою біржею, що віддає свічки
сторінками (як реальний Binance через since/limit)."""
from datetime import datetime, timezone

import core.data.providers as providers
from core.data.providers import Candle, HistoricalProvider


class _FakeCcxt:
    """Імітує CcxtProvider.fetch_ohlcv: до `limit` годинних свічок,
    починаючи з `since_ms` — так само, як пагінація реальної біржі."""
    def __init__(self, exchange_id: str = "binance"):
        self.exchange_id = exchange_id

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 1000,
                     since_ms: int | None = None) -> list[Candle]:
        step_ms = 3_600_000
        since_ms = since_ms or 0
        out = []
        for i in range(limit):
            ts_ms = since_ms + i * step_ms
            out.append(Candle(
                ts=datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
                open=100.0, high=101.0, low=99.0, close=100.5, volume=10.0,
            ))
        return out


def test_historical_provider_fetches_full_year_and_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(providers, "CcxtProvider", _FakeCcxt)
    hp = HistoricalProvider(year=2022, cache_dir=tmp_path)

    candles = hp.fetch_ohlcv("BTC/USDT", "1h")

    assert len(candles) > 8000  # ~24*365 годинних свічок за рік
    assert all(c.ts.year == 2022 for c in candles)
    cache_file = tmp_path / "BTC_USDT_1h_2022.csv"
    assert cache_file.exists()


def test_historical_provider_reuses_csv_cache_without_refetching(tmp_path, monkeypatch):
    calls = {"n": 0}

    class _CountingFakeCcxt(_FakeCcxt):
        def fetch_ohlcv(self, *args, **kwargs):
            calls["n"] += 1
            return super().fetch_ohlcv(*args, **kwargs)

    monkeypatch.setattr(providers, "CcxtProvider", _CountingFakeCcxt)

    first = HistoricalProvider(year=2022, cache_dir=tmp_path)
    candles_first = first.fetch_ohlcv("BTC/USDT", "1h")
    calls_after_first = calls["n"]
    assert calls_after_first > 0

    second = HistoricalProvider(year=2022, cache_dir=tmp_path)
    candles_second = second.fetch_ohlcv("BTC/USDT", "1h")

    assert calls["n"] == calls_after_first  # другий раз — з CSV, без нових запитів
    assert len(candles_second) == len(candles_first)

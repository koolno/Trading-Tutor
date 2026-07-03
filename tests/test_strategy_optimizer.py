"""Тести strategy_optimizer.py ("Оптимізована по історії" — навмисна
демонстрація overfitting). Без реальної мережі: підміняємо джерело даних і
grid зменшений для швидкості тесту."""
import json

import core.engines.strategy_optimizer as opt
from core.data.providers import SyntheticProvider


def test_aggregate_daily_compresses_hourly_candles():
    hourly = SyntheticProvider(seed=1, start_price=100, drift=0.001).fetch_ohlcv(
        "BTC/USDT", "1h", 240)  # 10 днів
    daily = opt._aggregate_daily(hourly)
    assert 9 <= len(daily) <= 11  # 240 годин ~ 10 днів; межові дні залежать від "зараз"
    assert daily[0].open == hourly[0].open
    assert daily[-1].close == hourly[-1].close
    assert daily[0].high == max(c.high for c in hourly[:24])


def test_grid_search_returns_valid_params(monkeypatch):
    # маленький grid — щоб тест був швидким, а не суттю ідентичний production
    monkeypatch.setattr(opt, "_GRID_MIN_CONFIRMATIONS", (1,))
    monkeypatch.setattr(opt, "_GRID_ATR_STOP_MULT", (1.5,))
    monkeypatch.setattr(opt, "_GRID_RR_TARGET", (2.0,))
    monkeypatch.setattr(opt, "_GRID_RSI_OVERSOLD", (30.0,))
    monkeypatch.setattr(opt, "_GRID_RSI_OVERBOUGHT", (70.0,))
    monkeypatch.setattr(opt, "_MIN_TRADES_TO_QUALIFY", 0)

    series = SyntheticProvider(seed=3, start_price=100, drift=0.01, vol=0.02).fetch_ohlcv(
        "BTC/USDT", "1h", 24 * 400)  # ~400 днів після агрегації
    daily = opt._aggregate_daily(series)

    params = opt._grid_search(daily)
    assert params.min_confirmations == 1
    assert params.atr_stop_mult == 1.5
    assert params.rr_target == 2.0
    assert params.fit_years == "2021-2025"
    # engine, що будується з підібраних параметрів, справді ними параметризований
    engine = params.to_signal_engine()
    assert engine.rr_target == 2.0
    assert engine.rsi_oversold == 30.0


def test_fit_optimized_params_caches_to_disk(tmp_path, monkeypatch):
    cache_path = tmp_path / "optimized_params.json"
    monkeypatch.setattr(opt, "_CACHE_PATH", cache_path)
    monkeypatch.setattr(opt, "_GRID_MIN_CONFIRMATIONS", (1,))
    monkeypatch.setattr(opt, "_GRID_ATR_STOP_MULT", (1.5,))
    monkeypatch.setattr(opt, "_GRID_RR_TARGET", (2.0,))
    monkeypatch.setattr(opt, "_GRID_RSI_OVERSOLD", (30.0,))
    monkeypatch.setattr(opt, "_GRID_RSI_OVERBOUGHT", (70.0,))
    monkeypatch.setattr(opt, "_MIN_TRADES_TO_QUALIFY", 0)

    series = SyntheticProvider(seed=4, start_price=100, drift=0.01).fetch_ohlcv(
        "BTC/USDT", "1h", 24 * 200)
    monkeypatch.setattr(opt, "_fetch_fit_series", lambda *a, **kw: opt._aggregate_daily(series))

    fetch_calls = {"n": 0}
    orig_fetch = opt._fetch_fit_series

    def counting_fetch(*a, **kw):
        fetch_calls["n"] += 1
        return orig_fetch(*a, **kw)
    monkeypatch.setattr(opt, "_fetch_fit_series", counting_fetch)

    assert not cache_path.exists()
    params1 = opt.fit_optimized_params()
    assert cache_path.exists()
    assert fetch_calls["n"] == 1

    params2 = opt.fit_optimized_params()  # другий раз — з кешу, без нового підбору
    assert fetch_calls["n"] == 1
    assert params2 == params1

    cached_raw = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cached_raw["min_confirmations"] == params1.min_confirmations

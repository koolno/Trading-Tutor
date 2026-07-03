"""Тест продуктивності: TechnicalAnalysis рахує індикатори за O(розмір
вікна), тому Session._tick_fast() раніше передавало ВЕСЬ ряд від початку
(series[:cursor+1]) — на повному році 1h-свічок (~8760) це O(n^2), і повний
історичний прогін ("прискорено, хвилини") насправді займав багато хвилин.
Session._TA_WINDOW_CANDLES обмежує вікно — цей тест фіксує, що обмежене
вікно дає ТІ САМІ фактори, що й необмежене (бо 300 свічок з великим запасом
покриває найдовший індикатор, EMA(50)), тобто це суто прискорення, а не
зміна поведінки."""
from core.data.providers import SyntheticProvider
from core.engines.technical import TechnicalAnalysis
from core.session import _TA_WINDOW_CANDLES


def test_bounded_window_matches_unbounded_window_far_into_history():
    candles = SyntheticProvider(seed=7, start_price=20000, drift=0.001).fetch_ohlcv(
        "BTC/USDT", "1h", _TA_WINDOW_CANDLES * 3)
    ta = TechnicalAnalysis()

    cursor = _TA_WINDOW_CANDLES * 2 + 50  # достатньо далеко, щоб обидва вікна відрізнялись розміром
    unbounded = candles[: cursor + 1]
    bounded = candles[max(0, cursor + 1 - _TA_WINDOW_CANDLES): cursor + 1]
    assert len(bounded) < len(unbounded)  # переконуємось, що обмеження справді щось відрізає

    factors_full, snapshot_full = ta.analyze("BTC/USDT", unbounded)
    factors_bounded, snapshot_bounded = ta.analyze("BTC/USDT", bounded)

    assert factors_full == factors_bounded
    assert snapshot_full.price == snapshot_bounded.price
    assert snapshot_full.regime == snapshot_bounded.regime

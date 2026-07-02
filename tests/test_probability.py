"""Тест модуля ймовірностей (PLAN A4): чесна статистика, без порад "купуй"."""
from core.data.providers import SyntheticProvider
from core.engines.probability import ProbabilityEngine


def _candles(n=300, seed=11, drift=0.002):
    return SyntheticProvider(seed=seed, start_price=100, drift=drift).fetch_ohlcv("X/Y", "1h", n)


def test_none_when_history_too_short():
    eng = ProbabilityEngine(horizon_candles=12)
    insight = eng.analyze("X/Y", _candles(50))
    assert insight is None


def test_probability_percentages_sum_to_full_sample():
    eng = ProbabilityEngine(horizon_candles=12)
    insight = eng.analyze("X/Y", _candles(300))
    assert insight is not None
    assert insight.sample_size == insight.up_count + insight.down_count + insight.flat_count
    assert abs(insight.up_pct + insight.down_pct + insight.flat_pct - 100.0) < 0.2


def test_small_sample_is_flagged_unreliable():
    eng = ProbabilityEngine(horizon_candles=12)
    small = eng.analyze("X/Y", _candles(70))
    if small is not None and small.sample_size < 20:
        assert not small.sample_is_sufficient
        assert "мала" in small.explanation_uk().lower()


def test_explanation_never_recommends_buying():
    eng = ProbabilityEngine(horizon_candles=12)
    insight = eng.analyze("X/Y", _candles(300))
    assert insight is not None
    text = insight.explanation_uk().lower()
    assert "не порада купувати чи продавати" in text
    assert "рішення" in text
    assert "рекомендуємо купити" not in text
    assert "варто купити" not in text


def test_explanation_mentions_sample_and_asset():
    eng = ProbabilityEngine(horizon_candles=12)
    insight = eng.analyze("X/Y", _candles(300))
    assert insight is not None
    text = insight.explanation_uk()
    assert "X/Y" in text
    assert str(insight.sample_size) in text

"""Тест екрана №2 «Що радить і чому» (PLAN B2): ймовірність + фактори за/проти, без "купуй"."""
from core.data.providers import SyntheticProvider
from core.engines.advice import AdviceEngine, factors_for_against_uk
from core.engines.signal_engine import TechnicalFactors

JARGON = ["rsi", "macd", "atr", "ema", "sma"]


def _candles(n=400, seed=17, drift=0.001):
    return SyntheticProvider(seed=seed, start_price=100, drift=drift).fetch_ohlcv("X/Y", "1h", n)


def test_factors_for_against_split_correctly():
    for_up, for_down = factors_for_against_uk(
        TechnicalFactors(trend_up=True, macd_bullish=True, near_support=True, atr_pct=2.0))
    assert for_up and not for_down

    for_up2, for_down2 = factors_for_against_uk(
        TechnicalFactors(trend_down=True, macd_bearish=True, near_resistance=True, atr_pct=2.0))
    assert for_down2 and not for_up2


def test_factors_contain_no_jargon():
    for_up, for_down = factors_for_against_uk(
        TechnicalFactors(trend_up=True, macd_bullish=True, near_support=True,
                          breakout_up=True, rsi=25, atr_pct=2.0))
    text = " ".join(for_up + for_down).lower()
    for term in JARGON:
        assert term not in text


def test_explain_returns_probability_and_factors():
    engine = AdviceEngine()
    result = engine.explain("X/Y", _candles(400))
    assert result.asset == "X/Y"
    assert result.price > 0
    assert isinstance(result.factors_for_uk, list)
    assert isinstance(result.factors_against_uk, list)
    # достатньо історії -> ймовірність має порахуватись
    assert result.probability is not None
    assert result.probability.sample_size > 0


def test_explain_handles_short_history_honestly():
    engine = AdviceEngine()
    result = engine.explain("X/Y", _candles(65), warmup=60)
    # замало даних для ймовірності -> чесно None, а не вигадана статистика
    assert result.probability is None


def test_to_dict_never_recommends_buying():
    engine = AdviceEngine()
    result = engine.explain("X/Y", _candles(400))
    data = result.to_dict()
    blob = str(data).lower()
    assert "купи " not in blob
    assert "рекомендуємо купити" not in blob
    assert "варто купити" not in blob


def test_to_dict_probability_shape():
    engine = AdviceEngine()
    result = engine.explain("X/Y", _candles(400))
    data = result.to_dict()
    p = data["probability"]
    assert p is not None
    assert set(["up_pct", "down_pct", "flat_pct", "sample_size",
                "sample_is_sufficient", "why", "what_could_go_wrong",
                "horizon_candles"]).issubset(p.keys())

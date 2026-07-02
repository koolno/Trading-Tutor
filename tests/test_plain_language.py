"""Тест перекладу технічних факторів у просту мову (PLAN B1) — без жаргону."""
from core.engines.plain_language import describe_situation_uk
from core.engines.signal_engine import TechnicalFactors
from core.models.types import MarketRegime, MarketSnapshot

JARGON = ["rsi", "macd", "atr", "ema", "sma"]


def _snapshot(**kw):
    base = dict(asset="BTC/USDT", price=60000.0, regime=MarketRegime.RANGING,
                volatility_atr_pct=2.0)
    base.update(kw)
    return MarketSnapshot(**base)


def test_uptrend_described_in_plain_words():
    s = describe_situation_uk(TechnicalFactors(trend_up=True, atr_pct=2.0),
                               _snapshot(regime=MarketRegime.TRENDING_UP))
    assert s.asset == "BTC/USDT"
    assert "рост" in s.headline_uk.lower()
    assert s.details_uk


def test_downtrend_described_in_plain_words():
    s = describe_situation_uk(TechnicalFactors(trend_down=True, atr_pct=2.0),
                               _snapshot(regime=MarketRegime.TRENDING_DOWN))
    assert "пада" in s.headline_uk.lower()


def test_no_clear_trend_is_honest_about_it():
    s = describe_situation_uk(TechnicalFactors(atr_pct=2.0), _snapshot())
    assert "немає" in s.headline_uk.lower()


def test_support_and_resistance_mentioned_in_plain_words():
    s = describe_situation_uk(
        TechnicalFactors(trend_up=True, near_support=True, atr_pct=2.0),
        _snapshot(regime=MarketRegime.TRENDING_UP),
    )
    assert any("підтрим" in d.lower() or "відштовхувалась" in d.lower() for d in s.details_uk)


def test_output_contains_no_technical_jargon():
    situations = [
        describe_situation_uk(TechnicalFactors(trend_up=True, macd_bullish=True, rsi=25, atr_pct=9.0),
                               _snapshot(regime=MarketRegime.HIGH_VOLATILITY)),
        describe_situation_uk(TechnicalFactors(trend_down=True, near_resistance=True, rsi=80, atr_pct=0.5),
                               _snapshot(regime=MarketRegime.TRENDING_DOWN, data_is_reliable=False,
                                         data_issues=["пропуски"])),
    ]
    for s in situations:
        text = (s.headline_uk + " " + " ".join(s.details_uk)).lower()
        for term in JARGON:
            assert term not in text, f"жаргонний термін '{term}' просочився у просте пояснення"


def test_to_dict_matches_dataclass_fields():
    s = describe_situation_uk(TechnicalFactors(trend_up=True, atr_pct=2.0),
                               _snapshot(regime=MarketRegime.TRENDING_UP))
    d = s.to_dict()
    assert d["asset"] == s.asset
    assert d["headline"] == s.headline_uk
    assert d["details"] == s.details_uk

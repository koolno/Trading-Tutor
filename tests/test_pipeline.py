"""Тести для модулів даних, аналізу, paper trading і навчання."""
from datetime import datetime, timedelta, timezone

from core.data.providers import Candle, SyntheticProvider
from core.data.quality import DataQualityEngine
from core.engines.journal import Journal, JournalEntry
from core.engines.learning import ParetoAnalyzer, compute_stats
from core.engines.paper_trading import PaperBroker
from core.engines.signal_engine import SignalEngine
from core.engines.technical import TechnicalAnalysis, atr_pct, ema, rsi
from core.knowledge.constitution import build_seed_constitution
from core.models.types import Direction, MarketRegime, TradeIdea, Confidence


def _candles(n=120):
    return SyntheticProvider(seed=5, start_price=100, drift=0.003).fetch_ohlcv("X/Y", "1h", n)


# --- Indicators -------------------------------------------------------- #
def test_ema_basic():
    assert round(ema([1, 2, 3, 4, 5], 3)[-1], 2) > 3


def test_rsi_bounds():
    val = rsi([c.close for c in _candles()])
    assert 0 <= val <= 100


def test_atr_positive():
    assert atr_pct(_candles()) > 0


# --- Technical analysis ------------------------------------------------ #
def test_technical_analysis_outputs_snapshot():
    f, s = TechnicalAnalysis().analyze("X/Y", _candles())
    assert s.asset == "X/Y"
    assert s.price > 0
    assert s.regime in MarketRegime
    assert 0 <= s.liquidity_score <= 1


# --- Data quality ------------------------------------------------------ #
def test_clean_data_is_reliable():
    r = DataQualityEngine().check(_candles(), "1h", check_staleness=False)
    assert r.reliable


def test_too_few_candles_unreliable():
    r = DataQualityEngine().check(_candles(10), "1h", check_staleness=False)
    assert not r.reliable


def test_price_jump_flagged():
    cs = _candles(60)
    cs[30].close *= 3  # штучний стрибок
    r = DataQualityEngine().check(cs, "1h", check_staleness=False)
    assert not r.reliable


def test_invalid_candle_flagged():
    cs = _candles(60)
    cs[20].high = cs[20].low - 1  # high < low
    r = DataQualityEngine().check(cs, "1h", check_staleness=False)
    assert not r.reliable


# --- Paper broker ------------------------------------------------------ #
def test_paper_broker_long_take_profit():
    b = PaperBroker(starting_equity=500, commission_pct=0, slippage_pct=0)
    idea = TradeIdea(asset="X/Y", direction=Direction.LONG, time_horizon="t",
                     entry_price=100, stop_loss=97, take_profit=106,
                     why_now="t", confidence=Confidence.STRONG)
    b.open(idea, size=1.0, risk_usd=3.0)
    closed = b.update_candle("X/Y", high=107, low=100)  # тейк
    assert len(closed) == 1
    assert closed[0][2] == "win"
    assert b.equity > 500


def test_paper_broker_long_stop_loss():
    b = PaperBroker(starting_equity=500, commission_pct=0, slippage_pct=0)
    idea = TradeIdea(asset="X/Y", direction=Direction.LONG, time_horizon="t",
                     entry_price=100, stop_loss=97, take_profit=106,
                     why_now="t", confidence=Confidence.STRONG)
    b.open(idea, size=1.0, risk_usd=3.0)
    closed = b.update_candle("X/Y", high=100, low=96)  # стоп
    assert closed[0][2] == "loss"
    assert b.equity < 500
    assert b.consecutive_losses == 1


# --- Learning / stats -------------------------------------------------- #
def test_compute_stats_winrate():
    entries = [
        JournalEntry(ts="t", asset="A", mode="paper", direction="long",
                     decision="closed", reason="r", pnl_usd=10, result="win",
                     supporting=["Висхідний тренд"]),
        JournalEntry(ts="t", asset="A", mode="paper", direction="long",
                     decision="closed", reason="r", pnl_usd=-5, result="loss",
                     supporting=["RSI високий"]),
    ]
    s = compute_stats(entries)
    assert s.trades == 2
    assert s.win_rate == 50.0
    assert s.total_pnl == 5


def test_pareto_ranks_factors():
    entries = [
        JournalEntry(ts="t", asset="A", mode="paper", direction="long",
                     decision="closed", reason="r", pnl_usd=20, result="win",
                     supporting=["Висхідний тренд"]),
        JournalEntry(ts="t", asset="A", mode="paper", direction="long",
                     decision="closed", reason="r", pnl_usd=-8, result="loss",
                     supporting=["Слабкий сигнал"]),
    ]
    helpful, harmful = ParetoAnalyzer().top_contributors(entries)
    assert helpful[0][0] == "Висхідний тренд"
    assert harmful[0][1] < 0


# --- Integration smoke ------------------------------------------------- #
def test_signal_engine_with_real_indicators():
    f, s = TechnicalAnalysis().analyze("X/Y", _candles())
    idea, why = SignalEngine(build_seed_constitution(), 2).generate(s, f)
    # ідея може бути None (немає переваги) — головне, без помилок і коректний тип
    assert idea is None or idea.stop_is_on_correct_side()

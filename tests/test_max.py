"""Тести для модулів максимум-версії: news, fundamental, backtest, memory, live, storage."""
import os

import pytest

from core.data.providers import SyntheticProvider
from core.engines.backtester import Backtester
from core.engines.fundamental import FundamentalAnalysis
from core.engines.live_adapter import LiveTradingAdapter
from core.engines.news_engine import (
    MockNewsProvider, NewsEngine, NewsItem, NewsProvider, Sentiment,
)
from core.engines.risk_engine import RiskConfig, RiskEngine
from core.engines.signal_engine import SignalEngine, TechnicalFactors
from core.engines.technical import TechnicalAnalysis
from core.knowledge.constitution import build_seed_constitution
from core.models.types import Direction, TradeIdea, Confidence
from core.storage.db import init_db, get_session, TradeRecord, reset_db


@pytest.fixture(autouse=True)
def _mem_db():
    init_db("sqlite:///:memory:")
    reset_db()
    yield


# --- News Engine ------------------------------------------------------- #
def test_news_neutral_by_default():
    ctx = NewsEngine(MockNewsProvider()).analyze("BTC/USDT")
    assert -0.3 < ctx.score < 0.3


def test_news_strong_negative_detected():
    class Neg(NewsProvider):
        def fetch(self, asset):
            return [NewsItem("Заборона регулятора", "regulator", Sentiment.NEGATIVE, 0.95),
                    NewsItem("ЦБ підняв ставку", "central_bank", Sentiment.NEGATIVE, 0.9)]
    ctx = NewsEngine(Neg()).analyze("BTC/USDT")
    assert ctx.is_strong_negative


def test_news_unavailable_is_neutral_not_crash():
    class Broken(NewsProvider):
        def fetch(self, asset):
            raise RuntimeError("no internet")
    ctx = NewsEngine(Broken()).analyze("BTC/USDT")
    assert ctx.score == 0.0 and ctx.strength == 0.0


def test_source_trust_ranking():
    reg = NewsItem("x", "regulator", Sentiment.NEGATIVE, 1.0)
    soc = NewsItem("x", "social", Sentiment.NEGATIVE, 1.0)
    assert reg.weight > soc.weight


# --- News blocks contradicting trade ----------------------------------- #
def test_news_blocks_long_on_strong_negative():
    class Neg(NewsProvider):
        def fetch(self, asset):
            return [NewsItem("crash", "regulator", Sentiment.NEGATIVE, 0.95),
                    NewsItem("crash2", "central_bank", Sentiment.NEGATIVE, 0.95)]
    news = NewsEngine(Neg()).analyze("BTC/USDT")
    ta = TechnicalAnalysis()
    c = SyntheticProvider(seed=1, start_price=60000, drift=0.004, vol=0.012).fetch_ohlcv("BTC/USDT", "1h", 120)
    f, s = ta.analyze("BTC/USDT", c)
    idea, why = SignalEngine(build_seed_constitution(), 2).generate(s, f, news=news)
    assert idea is None
    assert "заблокована" in why.lower() or "чекати" in why.lower()


# --- Fundamental ------------------------------------------------------- #
def test_fundamental_crypto_weak_on_low_liquidity():
    ctx = FundamentalAnalysis().analyze_crypto("X/Y", liquidity_score=0.1, regulatory_risk=0.8)
    assert ctx.is_weak


def test_fundamental_stock_strong_on_growth():
    ctx = FundamentalAnalysis().analyze_stock("AAPL", pe=15, earnings_growth=0.2, profit_margin=0.2)
    assert ctx.is_strong


# --- Backtester -------------------------------------------------------- #
def test_backtest_produces_metrics_and_gate():
    bt = Backtester(SignalEngine(build_seed_constitution(), 2),
                    RiskEngine(RiskConfig(min_risk_reward=1.5)))
    series = {"BTC/USDT": SyntheticProvider(seed=1, start_price=60000, drift=0.004, vol=0.012).fetch_ohlcv("BTC/USDT", "1h", 400)}
    res = bt.run(series, starting_equity=500, min_trades=10)
    assert res.trades > 0
    assert res.max_drawdown_pct >= 0
    assert isinstance(res.passed_gate, bool)


def test_backtest_gate_fails_on_too_few_trades():
    bt = Backtester(SignalEngine(build_seed_constitution(), 2), RiskEngine(RiskConfig()))
    series = {"X/Y": SyntheticProvider(seed=99, start_price=100, drift=0.0, vol=0.005).fetch_ohlcv("X/Y", "1h", 120)}
    res = bt.run(series, starting_equity=500, min_trades=1000)
    assert not res.passed_gate
    assert any("замало угод" in r for r in res.gate_reasons)


# --- Live adapter safety ----------------------------------------------- #
def test_live_disabled_by_default():
    a = LiveTradingAdapter()
    assert not a.enabled and a.dry_run


def test_live_order_rejected_when_disabled():
    idea = TradeIdea(asset="BTC/USDT", direction=Direction.LONG, time_horizon="t",
                     entry_price=100, stop_loss=97, take_profit=106, why_now="t",
                     confidence=Confidence.STRONG)
    r = LiveTradingAdapter(enabled=False).place_order(idea, 0.1)
    assert not r.accepted


def test_live_dry_run_does_not_send():
    idea = TradeIdea(asset="BTC/USDT", direction=Direction.LONG, time_horizon="t",
                     entry_price=100, stop_loss=97, take_profit=106, why_now="t",
                     confidence=Confidence.STRONG)
    r = LiveTradingAdapter(enabled=True, dry_run=True).place_order(idea, 0.1)
    assert r.accepted and r.dry_run
    assert "DRY-RUN" in r.detail


# --- Storage ----------------------------------------------------------- #
def test_storage_persists_trade():
    s = get_session()
    s.add(TradeRecord(session_id="t", asset="BTC/USDT", mode="paper",
                      direction="long", decision="opened", reason="x"))
    s.commit()
    assert s.query(TradeRecord).count() == 1
    s.close()


# --- Investment memory ------------------------------------------------- #
def test_memory_promotes_after_threshold():
    from core.engines.investment_memory import InvestmentMemory, Observation
    m = InvestmentMemory()
    for _ in range(5):
        m.remember(Observation("патерн X", "crypto", "trending_up"))
    useful = m.useful_observations()
    assert len(useful) == 1
    assert useful[0]["confirmations"] >= 5

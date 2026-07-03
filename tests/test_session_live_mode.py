"""Тести Session-рівня для Live-режиму (§24, §35 "Wire up real order
execution"): Session має вибирати LiveBroker лише коли реальні гроші
справді увімкнені й підтверджені, і відмовлятись від небезпечних
комбінацій (historical/fast_sim дані чи не-класична стратегія з реальними
грошима)."""
import pytest

from core.data.providers import SyntheticProvider
from core.engines.live_broker import LiveBroker
from core.engines.paper_trading import PaperBroker
from core.models.types import Mode
from core.session import Session, SessionConfig


def test_paper_mode_never_uses_live_broker():
    provider = SyntheticProvider(seed=1, start_price=100, drift=0.001)
    session = Session(SessionConfig(mode=Mode.PAPER, assets=["BTC/USDT"]), provider=provider)
    assert isinstance(session.broker, PaperBroker)
    assert not isinstance(session.broker, LiveBroker)
    assert session.is_real_live is False


def test_live_mode_without_confirmation_still_uses_paper_broker():
    """live_enabled=True саме по собі недостатньо — потрібне явне
    live_confirmed (та ж умова, що вже й будувала self.live раніше)."""
    provider = SyntheticProvider(seed=1, start_price=100, drift=0.001)
    session = Session(
        SessionConfig(mode=Mode.LIVE, market_mode="live_realtime", strategy="classic",
                     live_enabled=True, live_confirmed=False, assets=["BTC/USDT"]),
        provider=provider)
    assert not isinstance(session.broker, LiveBroker)
    assert session.is_real_live is False


def test_live_mode_fully_confirmed_uses_live_broker():
    provider = SyntheticProvider(seed=1, start_price=100, drift=0.001)
    session = Session(
        SessionConfig(mode=Mode.LIVE, market_mode="live_realtime", strategy="classic",
                     live_enabled=True, live_confirmed=True, amount_usd=500,
                     assets=["BTC/USDT"]),
        provider=provider)
    assert isinstance(session.broker, LiveBroker)
    assert session.is_real_live is True
    assert session.engine.mode == "live"


@pytest.mark.parametrize("market_mode,strategy", [
    ("historical", "classic"),
    ("fast_sim", "classic"),
    ("live_realtime", "optimized"),
    ("live_realtime", "dca"),
])
def test_live_mode_rejects_unsafe_data_or_strategy_combinations(market_mode, strategy):
    provider = SyntheticProvider(seed=1, start_price=100, drift=0.001)
    with pytest.raises(ValueError):
        Session(
            SessionConfig(mode=Mode.LIVE, market_mode=market_mode, strategy=strategy,
                         live_enabled=True, live_confirmed=True,
                         historical_year=2023 if market_mode == "historical" else None,
                         assets=["BTC/USDT"]),
            provider=provider)


def test_dashboard_exposes_is_real_live():
    provider = SyntheticProvider(seed=1, start_price=100, drift=0.001)
    session = Session(
        SessionConfig(mode=Mode.LIVE, market_mode="live_realtime", strategy="classic",
                     live_enabled=True, live_confirmed=True, assets=["BTC/USDT"]),
        provider=provider)
    d = session.dashboard()
    assert d["is_real_live"] is True
    assert d["mode"] == "live"

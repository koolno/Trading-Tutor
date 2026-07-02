"""Тест кейс-білдера (PLAN A3): кейс формується і містить чесну статистику."""
from core.data.providers import SyntheticProvider
from core.engines.case_builder import Case, CaseBuilder
from core.engines.risk_engine import RiskEngine
from core.engines.signal_engine import SignalEngine
from core.knowledge.constitution import build_seed_constitution


def _candles(n=400, seed=9, drift=0.001):
    return SyntheticProvider(seed=seed, start_price=100, drift=drift).fetch_ohlcv("X/Y", "1h", n)


def _build(seed=9, drift=0.001, n=400) -> Case:
    cb = CaseBuilder(SignalEngine(build_seed_constitution(), 2), RiskEngine())
    return cb.build("X/Y", _candles(n, seed, drift), starting_equity=500.0)


def test_case_has_period_and_trades_with_real_dates():
    case = _build()
    assert case.asset == "X/Y"
    assert case.period_start < case.period_end
    assert len(case.trades) > 0
    for t in case.trades:
        # реальні історичні дати з candles, а не однакові значення "зараз"
        assert t.opened_at <= t.closed_at
        assert t.result in ("win", "loss", "breakeven")


def test_case_records_stop_loss_protection_moments():
    case = _build()
    losses = [t for t in case.trades if t.result == "loss"]
    assert losses, "у цьому детермінованому прогоні мають бути збиткові угоди"
    # кожна збиткова угода мала закритись саме по стоп-лосу (захист спрацював)
    assert all(t.protected_from_loss for t in losses)
    assert len(case.stop_loss_saves) == len(losses)
    # прибуткові угоди НЕ позначені як "захист від збитку"
    assert all(not t.protected_from_loss for t in case.trades if t.result == "win")


def test_case_is_honest_even_when_result_is_negative():
    # спадний тренд -> система з великою ймовірністю завершить кейс у мінусі
    case = _build(seed=3, drift=-0.01, n=300)
    assert case.trades  # кейс не приховує угоди навіть при негативному результаті
    summary = case.summary_uk()
    assert str(len(case.trades)) in summary
    if case.total_return_pct <= 0:
        assert "чесно" in summary.lower()


def test_case_serializes_all_trades_to_dict():
    case = _build()
    data = case.to_dict()
    assert data["asset"] == "X/Y"
    assert len(data["trades"]) == len(case.trades)
    assert data["stop_loss_saves"] == len(case.stop_loss_saves)


def test_case_builder_rejects_too_short_history():
    cb = CaseBuilder(SignalEngine(build_seed_constitution(), 2), RiskEngine())
    try:
        cb.build("X/Y", _candles(30), warmup=60)
        assert False, "мало бути виключення для замалої історії"
    except ValueError:
        pass

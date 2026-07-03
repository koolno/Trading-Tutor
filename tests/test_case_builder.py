"""Тест кейс-білдера (PLAN A3): кейс формується і містить чесну статистику."""
import json

from core.data.providers import SyntheticProvider
from core.engines.case_builder import Case, CaseBuilder, case_from_journal
from core.engines.journal import TRIGGERED_EXIT_REASON, JournalEntry
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


def test_case_save_json_writes_utf8_cyrillic_without_crashing(tmp_path):
    # регрес: save_json раніше писав без encoding="utf-8" і падав на Windows
    # (cp1252) через українські літери у supporting-факторах
    case = _build()
    path = case.save_json(tmp_path / "case.json")
    text = path.read_text(encoding="utf-8")
    assert case.asset in text
    loaded = json.loads(text)
    assert loaded["asset"] == case.asset
    assert len(loaded["trades"]) == len(case.trades)


def test_case_builder_defaults_to_real_history_source():
    case = _build()
    assert case.source == "real_history"
    assert case.to_dict()["source"] == "real_history"


# --- case_from_journal (§PLAN E2 — «модель фотостоку») ------------------- #
def _entry(**kw):
    base = dict(ts="2026-07-02T10:00:00+00:00", asset="BTC/USDT", mode="paper",
                direction="long", reason="r")
    if kw.get("decision") == "closed":
        base["reason"] = TRIGGERED_EXIT_REASON  # справжнє спрацювання стопу/тейку
    base.update(kw)
    return JournalEntry(**base)


def test_case_from_journal_builds_case_from_closed_entries():
    entries = [
        _entry(decision="opened"),
        _entry(decision="rejected"),
        _entry(decision="closed", result="loss", entry=100.0, stop_loss=97.0,
               take_profit=106.0, exit=97.0, pnl_usd=-3.0),
        _entry(decision="closed", result="win", entry=100.0, stop_loss=97.0,
               take_profit=106.0, exit=106.0, pnl_usd=6.0),
    ]
    case = case_from_journal(entries, starting_equity=500.0, ending_equity=503.0)
    assert case.source == "trainer_synthetic"
    assert len(case.trades) == 2
    assert case.rejected_by_risk == 1
    assert case.asset == "BTC/USDT"
    assert case.starting_equity == 500.0
    assert case.ending_equity == 503.0


def test_case_from_journal_marks_losses_as_protected():
    entries = [
        _entry(decision="closed", result="loss", pnl_usd=-3.0),
        _entry(decision="closed", result="win", pnl_usd=6.0),
    ]
    case = case_from_journal(entries, 500.0, 503.0)
    losses = [t for t in case.trades if t.result == "loss"]
    wins = [t for t in case.trades if t.result == "win"]
    assert all(t.protected_from_loss for t in losses)
    assert all(not t.protected_from_loss for t in wins)


def test_case_from_journal_does_not_credit_forced_closes_as_protected():
    """DCA/forced-close втрати не мають позначатись як "стоп-лос захистив"
    (§ critical review — раніше будь-який result=="loss" рахувався захистом)."""
    entries = [
        _entry(decision="closed", result="loss", pnl_usd=-3.0, reason="Закрито примусово"),
    ]
    case = case_from_journal(entries, 500.0, 497.0)
    assert not case.trades[0].protected_from_loss
    assert len(case.stop_loss_saves) == 0


def test_case_from_journal_raises_without_closed_trades():
    entries = [_entry(decision="opened"), _entry(decision="rejected")]
    try:
        case_from_journal(entries, 500.0, 500.0)
        assert False, "мало бути виключення — нема закритих угод"
    except ValueError:
        pass


def test_case_from_journal_labels_multiple_assets():
    entries = [
        _entry(decision="closed", result="win", asset="BTC/USDT"),
        _entry(decision="closed", result="loss", asset="ETH/USDT"),
    ]
    case = case_from_journal(entries, 500.0, 500.0)
    assert "BTC/USDT" in case.asset and "ETH/USDT" in case.asset

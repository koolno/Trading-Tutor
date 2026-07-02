"""Тести ядра: Risk Engine має блокувати небезпечні угоди (§39)."""
from core.engines.risk_engine import RiskConfig, RiskEngine
from core.engines.signal_engine import SignalEngine, TechnicalFactors
from core.knowledge.constitution import RuleStatus, build_seed_constitution
from core.models.types import (
    AccountState,
    Confidence,
    Direction,
    MarketRegime,
    MarketSnapshot,
    TradeIdea,
)


def _good_market(asset="BTC/USDT", price=60000.0):
    return MarketSnapshot(
        asset=asset, price=price, spread_pct=0.02,
        liquidity_score=0.95, volatility_atr_pct=2.0,
        regime=MarketRegime.TRENDING_UP,
    )


def _good_idea(price=60000.0):
    return TradeIdea(
        asset="BTC/USDT", direction=Direction.LONG, time_horizon="swing",
        entry_price=price, stop_loss=price * 0.97, take_profit=price * 1.06,
        why_now="test", confidence=Confidence.STRONG,
    )


def _fresh_account(equity=500.0):
    return AccountState(equity=equity, peak_equity=equity)


# --- Risk Engine ------------------------------------------------------- #
def test_good_trade_is_approved():
    v = RiskEngine().evaluate(_good_idea(), _good_market(), _fresh_account())
    assert v.approved
    assert v.risk_amount_usd > 0


def test_no_stop_loss_is_blocked():
    idea = _good_idea()
    idea.stop_loss = idea.entry_price  # стоп = вхід => немає стопу
    v = RiskEngine().evaluate(idea, _good_market(), _fresh_account())
    assert v.is_blocked


def test_bad_risk_reward_is_blocked():
    idea = _good_idea()
    idea.take_profit = idea.entry_price * 1.01  # R:R ~0.33
    v = RiskEngine().evaluate(idea, _good_market(), _fresh_account())
    assert v.is_blocked
    assert any("ризик/прибуток" in r.lower() for r in v.blocking_reasons)


def test_unreliable_data_is_blocked():
    m = _good_market()
    m.data_is_reliable = False
    m.data_issues = ["пропуски даних"]
    v = RiskEngine().evaluate(_good_idea(), m, _fresh_account())
    assert v.is_blocked


def test_illiquid_asset_is_blocked():
    m = _good_market()
    m.liquidity_score = 0.1
    v = RiskEngine().evaluate(_good_idea(), m, _fresh_account())
    assert v.is_blocked


def test_drawdown_limit_blocks_new_trades():
    acc = AccountState(equity=450.0, peak_equity=500.0)  # 10% просадка
    v = RiskEngine().evaluate(_good_idea(), _good_market(), acc)
    assert v.is_blocked


def test_loss_streak_triggers_cooldown_block():
    acc = AccountState(equity=500.0, peak_equity=500.0, consecutive_losses=3)
    v = RiskEngine().evaluate(_good_idea(), _good_market(), acc)
    assert v.is_blocked


def test_position_size_respects_risk_pct():
    # ризик 0.5% від 500 = 2.5 USD
    v = RiskEngine(RiskConfig(risk_per_trade_pct=0.5)).evaluate(
        _good_idea(), _good_market(), _fresh_account(500.0)
    )
    assert abs(v.risk_amount_usd - 2.5) < 0.01


def test_no_leverage_caps_position_at_equity():
    v = RiskEngine(RiskConfig(allow_leverage=False)).evaluate(
        _good_idea(), _good_market(), _fresh_account(500.0)
    )
    assert v.position_value_usd <= 500.0 + 1e-6


def test_emergency_stop_on_drawdown():
    acc = AccountState(equity=460.0, peak_equity=500.0)  # 8%
    triggered, _ = RiskEngine().emergency_stop_triggered(acc)
    assert triggered


# --- Signal Engine ----------------------------------------------------- #
def test_signal_needs_min_confirmations():
    eng = SignalEngine(build_seed_constitution(), min_confirmations=2)
    idea, why = eng.generate(_good_market(), TechnicalFactors(trend_up=True, atr_pct=2.0))
    assert idea is None
    assert "чекати" in why.lower() or "недостатньо" in why.lower()


def test_signal_builds_idea_with_stop_and_rr():
    eng = SignalEngine(build_seed_constitution(), min_confirmations=2)
    tech = TechnicalFactors(trend_up=True, macd_bullish=True, near_support=True, atr_pct=2.0)
    idea, _ = eng.generate(_good_market(), tech)
    assert idea is not None
    assert idea.stop_is_on_correct_side()
    assert idea.risk_reward > 1.0


# --- Constitution ------------------------------------------------------ #
def test_core_safety_rules_exist():
    rules = build_seed_constitution()
    core = [r for r in rules if r.status == RuleStatus.CORE_SAFETY]
    assert len(core) >= 3
    assert all(r.is_core_safety for r in core)

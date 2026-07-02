"""Тест режиму «Тренажер» (PLAN C1): м'якші правила, трендові дані, швидкий перший результат."""
from core.session import Session, SessionConfig


def test_demo_risk_config_is_looser_than_conservative():
    demo = SessionConfig(risk_level="demo").to_risk_config()
    conservative = SessionConfig(risk_level="conservative").to_risk_config()
    assert demo.risk_per_trade_pct > conservative.risk_per_trade_pct
    assert demo.max_drawdown_pct > conservative.max_drawdown_pct
    assert demo.min_risk_reward <= conservative.min_risk_reward
    assert demo.loss_streak_cooldown >= conservative.loss_streak_cooldown


def test_is_demo_property():
    assert SessionConfig(risk_level="demo").is_demo
    assert not SessionConfig(risk_level="conservative").is_demo
    assert not SessionConfig(risk_level="moderate").is_demo


def test_demo_signal_engine_needs_fewer_confirmations():
    demo_session = Session(SessionConfig(risk_level="demo", assets=["BTC/USDT"]))
    conservative_session = Session(SessionConfig(risk_level="conservative", assets=["BTC/USDT"]))
    assert demo_session.signal.min_confirmations < conservative_session.signal.min_confirmations


def test_demo_session_produces_a_trade_quickly():
    session = Session(SessionConfig(risk_level="demo", assets=["BTC/USDT"], amount_usd=500))
    session.start()
    for _ in range(150):
        session.tick()
        if session.journal.closed_trades() or session.broker.positions:
            break
    assert session.journal.closed_trades() or session.broker.positions, (
        "тренажер мав показати хоч одну угоду за розумну кількість тіків, "
        "щоб новачок одразу побачив, як усе працює"
    )


def test_dashboard_exposes_trainer_mode():
    demo = Session(SessionConfig(risk_level="demo", assets=["BTC/USDT"]))
    d = demo.dashboard()
    assert d["is_demo"] is True
    assert d["risk_level"] == "demo"

    normal = Session(SessionConfig(risk_level="conservative", assets=["BTC/USDT"]))
    assert normal.dashboard()["is_demo"] is False
    assert normal.dashboard()["risk_level"] == "conservative"

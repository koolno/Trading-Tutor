"""Тест інтеграції hard-stop (§paper_trading.py) із Session: коли стратегія
чесно зупиняється через повторну просадку, сесія має справді завершитись
(running=False), а не тікати далі вічно (§ critical review)."""
from core.data.providers import SyntheticProvider
from core.session import Session, SessionConfig


def test_session_stops_running_when_broker_hard_stops():
    provider = SyntheticProvider(seed=1, start_price=100, drift=0.001)
    session = Session(
        SessionConfig(strategy="classic", assets=["BTC/USDT"], amount_usd=500),
        provider=provider)
    session.start()
    session.tick()
    assert session.running

    session.broker.hard_stopped = True
    session.tick()

    assert not session.running


def test_session_last_action_explains_hard_stop_honestly():
    """Раніше причина зупинки могла загубитись за звичайним закриттям
    позиції на тому ж тіку (§_process_window пріоритет наративів) —
    користувач бачив би "стоп-лос спрацював" замість справжньої причини."""
    provider = SyntheticProvider(seed=1, start_price=100, drift=0.001)
    session = Session(
        SessionConfig(strategy="classic", assets=["BTC/USDT"], amount_usd=500),
        provider=provider)
    session.start()
    session.tick()  # прогріваємо: створює news_cache тощо

    session.broker.hard_stopped = True
    session.broker.drawdown_pause_count = 4
    session.tick()

    assert not session.running
    assert "просадка повторилась" in session.last_action
    assert "явно не працює" in session.last_action

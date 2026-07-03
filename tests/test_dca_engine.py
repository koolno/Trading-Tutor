"""Тести DCAEngine ("Надійна (усереднення)") — регулярні внески за
розкладом, без вибору моменту входу, без стоп-лосу/тейку (buy & hold)."""
from core.data.providers import SyntheticProvider
from core.engines.dca_engine import DCAEngine
from core.engines.journal import Journal
from core.engines.paper_trading import PaperBroker
from core.engines.signal_engine import TechnicalFactors
from core.models.types import MarketSnapshot


def _market(asset="BTC/USDT", price=100.0):
    return MarketSnapshot(asset=asset, price=price, liquidity_score=1.0)


def test_dca_buys_at_regular_intervals_up_to_num_tranches():
    broker = PaperBroker(starting_equity=1000, commission_pct=0, slippage_pct=0)
    journal = Journal()
    engine = DCAEngine(broker, journal, assets=["BTC/USDT"], starting_equity=1000,
                       total_ticks=40, num_tranches=4)  # interval = 10
    assert engine.interval == 10

    bought_ticks = []
    for i in range(40):
        msg = engine.step(_market(price=100 + i), TechnicalFactors())
        if msg.startswith("✅"):
            bought_ticks.append(i)

    assert bought_ticks == [0, 10, 20, 30]
    assert len(broker.positions) == 4


def test_dca_stops_after_num_tranches_exhausted():
    broker = PaperBroker(starting_equity=1000, commission_pct=0, slippage_pct=0)
    journal = Journal()
    engine = DCAEngine(broker, journal, assets=["BTC/USDT"], starting_equity=1000,
                       total_ticks=20, num_tranches=2)  # interval = 10
    for i in range(100):  # набагато більше тіків, ніж потрібно
        engine.step(_market(price=100), TechnicalFactors())
    assert len(broker.positions) == 2  # більше не купує, навіть якщо тіки тривають


def test_dca_position_never_hits_stop_or_take_on_normal_price_moves():
    broker = PaperBroker(starting_equity=1000, commission_pct=0, slippage_pct=0)
    journal = Journal()
    engine = DCAEngine(broker, journal, assets=["BTC/USDT"], starting_equity=1000,
                       total_ticks=10, num_tranches=1)
    engine.step(_market(price=100), TechnicalFactors())
    assert len(broker.positions) == 1

    # навіть різкий, але реалістичний рух ціни не повинен закрити DCA-позицію
    closed = broker.update_candle("BTC/USDT", high=150, low=50)
    assert closed == []
    assert len(broker.positions) == 1


def test_dca_budget_split_across_assets_and_tranches():
    broker = PaperBroker(starting_equity=1000, commission_pct=0, slippage_pct=0)
    journal = Journal()
    engine = DCAEngine(broker, journal, assets=["BTC/USDT", "ETH/USDT"],
                       starting_equity=1000, total_ticks=10, num_tranches=5)
    assert engine.tranche_usd == 1000 / 2 / 5  # порівну між активами і траншами


def test_close_all_positions_forces_realized_pnl_for_dca():
    """Раніше broker.update()/_check() закривали позицію лише при
    спрацюванні стопу/тейку — для DCA (яка навмисно ніколи їх не досягає) це
    означало, що позиції лишались відкритими НАЗАВЖДИ. close_all_positions()
    закриває примусово за будь-якою ціною."""
    broker = PaperBroker(starting_equity=1000, commission_pct=0, slippage_pct=0)
    journal = Journal()
    engine = DCAEngine(broker, journal, assets=["BTC/USDT"], starting_equity=1000,
                       total_ticks=10, num_tranches=1)
    engine.step(_market(price=100), TechnicalFactors())
    assert len(broker.positions) == 1

    # звичайний update() НЕ закриє DCA-позицію (стоп/тейк недосяжні навмисно)
    assert broker.update("BTC/USDT", 120) == []
    assert len(broker.positions) == 1

    # а примусове закриття — так, і фіксує реальний pnl
    closed = broker.close_all_positions("BTC/USDT", 120)
    assert len(closed) == 1
    assert broker.positions == []
    pos, pnl, result = closed[0]
    assert pnl > 0
    assert result == "win"

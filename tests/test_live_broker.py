"""Тести LiveBroker (§24, §35 "Wire up real order execution") — реальні
ордери на біржі через мокнуту ccxt-подібну біржу (без мережі/ключів).
Мокаємо на рівні LiveTradingAdapter._exchange (кешоване підключення), тому
LiveTradingAdapter._connect() ніколи не викликає import ccxt насправді."""
from core.engines.live_adapter import LiveTradingAdapter
from core.engines.live_broker import LiveBroker
from core.engines.paper_trading import PaperTradingEngine
from core.engines.risk_engine import RiskConfig, RiskEngine
from core.engines.signal_engine import SignalEngine, TechnicalFactors
from core.knowledge.constitution import build_seed_constitution
from core.engines.journal import Journal
from core.models.types import Direction, MarketSnapshot, TradeIdea, Confidence


class _FakeExchange:
    """Мінімальний фейк біржі — лише методи, які реально викликає
    LiveTradingAdapter: create_order, fetch_order, cancel_order,
    fetch_balance, amount_to_precision."""
    def __init__(self, balance_usdt: float = 1000.0):
        self.orders: dict[str, dict] = {}
        self._next_id = 1
        self.cancelled: set[str] = set()
        self.balance_usdt = balance_usdt

    def create_order(self, symbol, order_type, side, amount, price=None, params=None):
        oid = str(self._next_id)
        self._next_id += 1
        order = {"id": oid, "symbol": symbol, "type": order_type, "side": side,
                 "amount": amount, "price": price, "status": "open"}
        if order_type == "market":
            # ринковий ордер виконується миттєво
            order["status"] = "closed"
            order["average"] = price or 100.0
        self.orders[oid] = order
        return order

    def fetch_order(self, order_id, symbol):
        return self.orders[order_id]

    def cancel_order(self, order_id, symbol):
        self.orders[order_id]["status"] = "canceled"
        self.cancelled.add(order_id)

    def fetch_balance(self):
        return {"total": {"USDT": self.balance_usdt}}

    def amount_to_precision(self, symbol, amount):
        return round(amount, 6)

    def fill(self, order_id, price=None):
        """Тестовий хелпер — імітує заповнення відкладеного ордера на біржі."""
        o = self.orders[order_id]
        o["status"] = "closed"
        if price is not None:
            o["average"] = price


def _adapter(fake_exchange, enabled=True, dry_run=False):
    a = LiveTradingAdapter(enabled=enabled, dry_run=dry_run)
    a._exchange = fake_exchange  # оминає _connect()/import ccxt повністю
    return a


def _idea(direction=Direction.LONG, entry=100.0, stop=97.0, take=106.0):
    return TradeIdea(asset="BTC/USDT", direction=direction, time_horizon="t",
                     entry_price=entry, stop_loss=stop, take_profit=take,
                     why_now="t", confidence=Confidence.STRONG)


def test_open_places_entry_stop_and_take_profit_orders():
    ex = _FakeExchange()
    broker = LiveBroker(_adapter(ex), starting_equity_hint=500)

    pos = broker.open(_idea(), size=0.01, risk_usd=3.0)

    assert pos.live is True
    assert pos.stop_order_id is not None
    assert pos.take_order_id is not None
    stop_order = ex.orders[pos.stop_order_id]
    take_order = ex.orders[pos.take_order_id]
    assert stop_order["type"] == "stop_loss_limit" and stop_order["side"] == "sell"
    assert take_order["type"] == "limit" and take_order["side"] == "sell"
    assert len(broker.positions) == 1


def test_stop_fill_cancels_take_profit_and_journals_real_loss():
    ex = _FakeExchange()
    broker = LiveBroker(_adapter(ex), starting_equity_hint=500)
    pos = broker.open(_idea(), size=0.01, risk_usd=3.0)

    ex.fill(pos.stop_order_id, price=96.5)  # стоп спрацював на біржі
    closed = broker.update_candle("BTC/USDT", high=101, low=96)

    assert len(closed) == 1
    closed_pos, pnl, result = closed[0]
    assert result == "loss"
    assert pnl < 0
    assert pos.take_order_id in ex.cancelled  # тейк скасовано, бо стоп уже виконав вихід
    assert broker.positions == []


def test_take_fill_cancels_stop_and_journals_real_win():
    ex = _FakeExchange()
    broker = LiveBroker(_adapter(ex), starting_equity_hint=500)
    pos = broker.open(_idea(), size=0.01, risk_usd=3.0)

    ex.fill(pos.take_order_id, price=106.2)
    closed = broker.update_candle("BTC/USDT", high=107, low=99)

    assert len(closed) == 1
    closed_pos, pnl, result = closed[0]
    assert result == "win"
    assert pnl > 0
    assert pos.stop_order_id in ex.cancelled


def test_neither_order_filled_position_stays_open():
    ex = _FakeExchange()
    broker = LiveBroker(_adapter(ex), starting_equity_hint=500)
    broker.open(_idea(), size=0.01, risk_usd=3.0)

    closed = broker.update_candle("BTC/USDT", high=101, low=99)

    assert closed == []
    assert len(broker.positions) == 1


def test_close_all_positions_cancels_orders_and_market_closes():
    ex = _FakeExchange()
    broker = LiveBroker(_adapter(ex), starting_equity_hint=500)
    pos = broker.open(_idea(), size=0.01, risk_usd=3.0)

    closed = broker.close_all_positions("BTC/USDT", price=103.0)

    assert len(closed) == 1
    assert pos.stop_order_id in ex.cancelled
    assert pos.take_order_id in ex.cancelled
    assert broker.positions == []
    closed_pos, pnl, result = closed[0]
    assert pnl > 0  # closed above entry (100 -> 103)


def test_account_state_reflects_real_exchange_balance():
    ex = _FakeExchange(balance_usdt=742.5)
    broker = LiveBroker(_adapter(ex), starting_equity_hint=500)

    acc = broker.account_state()

    assert acc.equity == 742.5


def test_account_state_falls_back_to_last_known_on_fetch_failure():
    ex = _FakeExchange(balance_usdt=800.0)
    broker = LiveBroker(_adapter(ex), starting_equity_hint=500)
    broker.account_state()  # успішно рефрешить до 800

    def _boom():
        raise RuntimeError("network blip")
    ex.fetch_balance = _boom

    acc = broker.account_state()
    assert acc.equity == 800.0  # не впало, лишився останній відомий баланс


def test_entry_order_rejection_raises_and_engine_journals_it_not_crashes():
    """LiveBroker.open() кидає виняток, якщо реальний ордер відхилено —
    PaperTradingEngine.step() має це журналювати як відмову, а не впасти."""
    class _RejectingExchange(_FakeExchange):
        def create_order(self, *a, **kw):
            raise RuntimeError("insufficient balance")

    ex = _RejectingExchange()
    broker = LiveBroker(_adapter(ex), starting_equity_hint=500)
    risk = RiskEngine(RiskConfig())
    signal = SignalEngine(build_seed_constitution(), min_confirmations=1)
    journal = Journal()
    engine = PaperTradingEngine(signal, risk, broker, journal, mode="live")

    market = MarketSnapshot(asset="BTC/USDT", price=100, liquidity_score=1.0)
    tech = TechnicalFactors(trend_up=True, macd_bullish=True, atr_pct=2.0)

    msg = engine.step(market, tech, update_positions=False)

    assert msg.startswith("🚫")
    assert broker.positions == []
    rejected = [e for e in journal.entries if e.decision == "rejected"]
    assert rejected and rejected[0].mode == "live"


def test_journal_entries_use_live_mode_not_paper():
    ex = _FakeExchange()
    broker = LiveBroker(_adapter(ex), starting_equity_hint=500)
    risk = RiskEngine(RiskConfig())
    signal = SignalEngine(build_seed_constitution(), min_confirmations=1)
    journal = Journal()
    engine = PaperTradingEngine(signal, risk, broker, journal, mode="live")

    market = MarketSnapshot(asset="BTC/USDT", price=100, liquidity_score=1.0)
    tech = TechnicalFactors(trend_up=True, macd_bullish=True, atr_pct=2.0)
    engine.step(market, tech, update_positions=False)

    opened = [e for e in journal.entries if e.decision == "opened"]
    assert opened and opened[0].mode == "live"

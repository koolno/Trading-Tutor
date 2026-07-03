"""
Live Broker (§24, §35) — той самий інтерфейс, що й PaperBroker, але позиції
відкриваються й закриваються РЕАЛЬНИМИ ордерами на біржі через
LiveTradingAdapter, а не симуляцією. Успадковує PaperBroker заради
cooldown/drawdown-pause бухгалтерії (advance_cooldown, start_drawdown_pause
тощо) — вона суто локальна (лічильники тіків) і однаково чесна для обох
режимів; тут перевизначені лише методи, що торкаються РЕАЛЬНОГО рахунку й
ордерів: open(), update()/update_candle(), close_all_positions(),
account_state(). Завдяки цьому PaperTradingEngine і RiskEngine не
змінюються взагалі — вони працюють з будь-яким брокером через спільний
інтерфейс.

Кожна позиція отримує стоп-лос і тейк-профіт як ДВА окремих ордери на
біржі (не атомарна OCO-пара — простіше й портативніше між версіями ccxt).
update()/update_candle() опитують стан обох щотік і скасовують той, що не
знадобився.

ВАЖЛИВО (межі цієї реалізації, § план "Wire up real order execution"): якщо
процес перезапуститься, поки позиція відкрита, застосунок втратить її з
локального стану — але вже виставлені на біржі стоп/тейк ордери
залишаються активними і виконаються самі. Позиція не лишається
незахищеною, але дашборд про неї більше не знатиме, доки хтось не звірить
це вручну. Персистентності відкритих live-позицій між перезапусками тут
немає — це свідоме обмеження першої версії, задокументоване, а не
приховане.
"""
from __future__ import annotations

from datetime import datetime, timezone

from core.engines.live_adapter import LiveTradingAdapter
from core.engines.paper_trading import PaperBroker, Position
from core.models.types import AccountState, TradeIdea


class LiveBroker(PaperBroker):
    def __init__(self, adapter: LiveTradingAdapter, starting_equity_hint: float = 500.0):
        super().__init__(starting_equity=starting_equity_hint)
        self.adapter = adapter
        self._last_known_equity = starting_equity_hint

    # --------------------------------------------------------------------- #
    #  Реальний вхід — ринковий ордер + стоп-лос + тейк-профіт на біржі
    # --------------------------------------------------------------------- #
    def open(self, idea: TradeIdea, size: float, risk_usd: float,
             opened_at: datetime | None = None) -> Position:
        size = self.adapter.round_amount(idea.asset, size)
        entry_result = self.adapter.place_order(idea, size)
        if not entry_result.accepted:
            raise RuntimeError(entry_result.detail)

        # тейк не встановився — позиція все одно реальна й (найімовірніше)
        # захищена стопом, який place_order уже спробував виставити (і
        # повідомив у detail, якщо не вдалось) — не скасовуємо вхід через це
        take_result = self.adapter.place_take_profit(idea, size)

        pos = Position(
            asset=idea.asset, direction=idea.direction,
            entry=entry_result.fill_price or idea.entry_price,
            stop_loss=idea.stop_loss, take_profit=idea.take_profit,
            size=size, risk_usd=risk_usd,
            opened_at=(opened_at or datetime.now(timezone.utc)).isoformat(),
            supporting=idea.supporting_factors, rules_fired=idea.rules_fired,
            live=True, stop_order_id=entry_result.stop_order_id,
            take_order_id=take_result.order_id if take_result.accepted else None,
        )
        self.positions.append(pos)
        return pos

    # --------------------------------------------------------------------- #
    #  Звірка: чи спрацював стоп чи тейк на біржі (замість симуляції за high/low)
    # --------------------------------------------------------------------- #
    def update(self, asset: str, price: float) -> list[tuple[Position, float, str]]:
        return self._reconcile(asset)

    def update_candle(self, asset: str, high: float, low: float
                      ) -> list[tuple[Position, float, str]]:
        return self._reconcile(asset)

    def _reconcile(self, asset: str) -> list[tuple[Position, float, str]]:
        closed: list[tuple[Position, float, str]] = []
        still_open: list[Position] = []
        for pos in self.positions:
            if pos.asset != asset or not pos.live:
                still_open.append(pos)
                continue
            settled = self._check_fills(pos)
            if settled is not None:
                closed.append(settled)
            else:
                still_open.append(pos)
        self.positions = still_open
        return closed

    def _check_fills(self, pos: Position):
        stop_order = self.adapter.fetch_order(pos.stop_order_id, pos.asset)
        if stop_order is not None and stop_order.get("status") == "closed":
            self.adapter.cancel_order(pos.take_order_id, pos.asset)
            fill_price = stop_order.get("average") or stop_order.get("price") or pos.stop_loss
            return self._settle(pos, fill_price)

        take_order = self.adapter.fetch_order(pos.take_order_id, pos.asset)
        if take_order is not None and take_order.get("status") == "closed":
            self.adapter.cancel_order(pos.stop_order_id, pos.asset)
            fill_price = take_order.get("average") or take_order.get("price") or pos.take_profit
            return self._settle(pos, fill_price)

        return None

    def _settle(self, pos: Position, fill_price: float) -> tuple[Position, float, str]:
        """Реалізований результат за фактичною ціною виконання на біржі —
        комісії вже враховані в самому балансі, тому тут лише валовий pnl.
        Результат (win/loss/breakeven) визначається за знаком pnl, а НЕ за
        тим, який саме ордер спрацював — рідкісний випадок (стоп спрацював,
        але з прослизанням у плюс) усе одно має бути показаний чесно."""
        if pos.direction.value == "long":
            pnl = (fill_price - pos.entry) * pos.size
        else:
            pnl = (pos.entry - fill_price) * pos.size
        self.realized_pnl += pnl
        if pnl < 0:
            self.consecutive_losses += 1
        elif pnl > 0:
            self.consecutive_losses = 0
        result = "win" if pnl > 0 else ("loss" if pnl < 0 else "breakeven")
        return (pos, round(pnl, 4), result)

    # --------------------------------------------------------------------- #
    #  Примусове закриття — скасовує обидва захисних ордери, закриває ринковим
    # --------------------------------------------------------------------- #
    def close_all_positions(self, asset: str, price: float
                            ) -> list[tuple[Position, float, str]]:
        closed_positions: list[Position] = []
        still_open: list[Position] = []
        for pos in self.positions:
            if pos.asset != asset or not pos.live:
                still_open.append(pos)
                continue
            self.adapter.cancel_order(pos.stop_order_id, pos.asset)
            self.adapter.cancel_order(pos.take_order_id, pos.asset)
            closed_positions.append(pos)
        if closed_positions:
            self.adapter.emergency_close_all(closed_positions)
        self.positions = still_open
        return [self._settle(pos, price) for pos in closed_positions]

    # --------------------------------------------------------------------- #
    #  Реальний баланс замість симульованого
    # --------------------------------------------------------------------- #
    def account_state(self) -> AccountState:
        try:
            self._last_known_equity = self.adapter.get_balance()
        except Exception:
            pass  # тимчасова помилка мережі — використовуємо останній відомий баланс
        self.equity = self._last_known_equity
        self.peak_equity = max(self.peak_equity, self.equity)
        return super().account_state()

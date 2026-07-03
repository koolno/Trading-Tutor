"""
Paper Trading Engine (§23).

Симулює реальну торгівлю на справжніх даних: відкриття/закриття позицій,
комісії, slippage, оновлення рахунку, перевірку стопів і тейків. Жодних
реальних грошей. Це безпечний полігон перед live.

Цикл одного «тіку»:
  1. оновити відкриті позиції за новою ціною (стоп/тейк);
  2. отримати знімок ринку + технічні фактори;
  3. Signal Engine -> ідея (або «чекати»);
  4. Risk Engine -> вето або розмір позиції;
  5. відкрити позицію або записати відмову в журнал.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.engines.journal import Journal, JournalEntry
from core.engines.risk_engine import RiskEngine
from core.engines.signal_engine import SignalEngine, TechnicalFactors
from core.models.types import (
    AccountState,
    Direction,
    MarketSnapshot,
    TradeIdea,
)


@dataclass
class Position:
    asset: str
    direction: Direction
    entry: float
    stop_loss: float
    take_profit: float
    size: float
    risk_usd: float
    opened_at: str
    supporting: list[str] = field(default_factory=list)
    rules_fired: list[str] = field(default_factory=list)


class PaperBroker:
    """Стан рахунку + симуляція виконання."""
    def __init__(self, starting_equity: float = 500.0,
                 commission_pct: float = 0.1, slippage_pct: float = 0.05):
        self.equity = starting_equity
        self.peak_equity = starting_equity
        self.commission_pct = commission_pct
        self.slippage_pct = slippage_pct
        self.positions: list[Position] = []
        self.realized_pnl = 0.0
        self.consecutive_losses = 0
        self.daily_risk_used_pct = 0.0
        self.weekly_risk_used_pct = 0.0
        self.cooldown_remaining = 0   # тіків/свічок лишилось до кінця cooldown

    def account_state(self) -> AccountState:
        return AccountState(
            equity=round(self.equity, 2),
            peak_equity=round(self.peak_equity, 2),
            daily_risk_used_pct=self.daily_risk_used_pct,
            weekly_risk_used_pct=self.weekly_risk_used_pct,
            open_positions=len(self.positions),
            consecutive_losses=self.consecutive_losses,
            in_cooldown=self.cooldown_remaining > 0,
        )

    def start_cooldown(self, ticks: int) -> None:
        """Пауза на певну кількість тіків, а не назавжди — інакше після
        loss streak Risk Engine блокував би угоди безкінечно (жодна нова
        угода = жодного шансу на перемогу, яка скидає consecutive_losses)."""
        self.cooldown_remaining = ticks
        self.consecutive_losses = 0

    def advance_cooldown(self) -> None:
        """Викликається раз на тік, незалежно від того, чи була угода —
        інакше лічильник ніколи б не дійшов до нуля."""
        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1

    def _apply_slippage(self, price: float, direction: Direction, entering: bool) -> float:
        s = self.slippage_pct / 100
        # вхід дорожчий, вихід дешевший — реалістично проти нас
        if (direction == Direction.LONG) == entering:
            return price * (1 + s)
        return price * (1 - s)

    def open(self, idea: TradeIdea, size: float, risk_usd: float,
             opened_at: datetime | None = None) -> Position:
        fill = self._apply_slippage(idea.entry_price, idea.direction, entering=True)
        commission = fill * size * self.commission_pct / 100
        self.equity -= commission
        pos = Position(
            asset=idea.asset, direction=idea.direction, entry=fill,
            stop_loss=idea.stop_loss, take_profit=idea.take_profit,
            size=size, risk_usd=risk_usd,
            opened_at=(opened_at or datetime.now(timezone.utc)).isoformat(),
            supporting=idea.supporting_factors, rules_fired=idea.rules_fired,
        )
        self.positions.append(pos)
        return pos

    def update(self, asset: str, price: float) -> list[tuple[Position, float, str]]:
        """Перевіряє стопи/тейки за поточною ціною (close)."""
        return self._check(asset, low=price, high=price)

    def update_candle(self, asset: str, high: float, low: float
                      ) -> list[tuple[Position, float, str]]:
        """Реалістична перевірка: стоп/тейк могли спрацювати всередині свічки."""
        return self._check(asset, low=low, high=high)

    def close_all_positions(self, asset: str, price: float
                            ) -> list[tuple[Position, float, str]]:
        """Примусово закриває ВСІ позиції по активу за заданою ціною —
        незалежно від того, чи спрацював стоп/тейк. Потрібно для DCA-позицій
        (§DCAEngine), у яких стоп/тейк навмисно ніколи не спрацьовує (buy &
        hold) — без цього "Закрити всі угоди"/кінець циклу лишали б їх
        відкритими назавжди."""
        closed = []
        still_open = []
        for pos in self.positions:
            if pos.asset != asset:
                still_open.append(pos)
                continue
            pnl, result = self._close(pos, price)
            closed.append((pos, pnl, result))
        self.positions = still_open
        return closed

    def _check(self, asset: str, low: float, high: float
              ) -> list[tuple[Position, float, str]]:
        closed = []
        still_open = []
        for pos in self.positions:
            if pos.asset != asset:
                still_open.append(pos)
                continue
            if pos.direction == Direction.LONG:
                hit_stop = low <= pos.stop_loss
                hit_take = high >= pos.take_profit
            else:
                hit_stop = high >= pos.stop_loss
                hit_take = low <= pos.take_profit
            if hit_stop or hit_take:
                # консервативно: якщо обидва в одній свічці — вважаємо стоп
                exit_price = pos.stop_loss if hit_stop else pos.take_profit
                pnl, result = self._close(pos, exit_price)
                closed.append((pos, pnl, result))
            else:
                still_open.append(pos)
        self.positions = still_open
        return closed

    def _close(self, pos: Position, exit_price: float) -> tuple[float, str]:
        fill = self._apply_slippage(exit_price, pos.direction, entering=False)
        if pos.direction == Direction.LONG:
            gross = (fill - pos.entry) * pos.size
        else:
            gross = (pos.entry - fill) * pos.size
        commission = fill * pos.size * self.commission_pct / 100
        pnl = gross - commission
        self.equity += pnl
        self.realized_pnl += pnl
        self.peak_equity = max(self.peak_equity, self.equity)
        if pnl < 0:
            self.consecutive_losses += 1
        elif pnl > 0:
            self.consecutive_losses = 0
        result = "win" if pnl > 0 else ("loss" if pnl < 0 else "breakeven")
        return round(pnl, 4), result


class PaperTradingEngine:
    """Звʼязує дані, аналіз, сигнал, ризик, виконання і журнал."""
    def __init__(self, signal: SignalEngine, risk: RiskEngine,
                 broker: PaperBroker, journal: Journal):
        self.signal = signal
        self.risk = risk
        self.broker = broker
        self.journal = journal

    def step(self, market: MarketSnapshot, tech: TechnicalFactors,
             update_positions: bool = True, news=None,
             as_of: datetime | None = None) -> str:
        # as_of — час свічки, що зараз обробляється: у fast_sim/historical це
        # симульований момент з минулого, а НЕ реальний "зараз" (інакше
        # журнал показував би поточний час для подій, що "сталися" в 2022
        # чи 2025 роках). У live_realtime as_of — це фактично реальний час,
        # бо це час останньої отриманої з біржі свічки.
        as_of = as_of or datetime.now(timezone.utc)

        # 0. просунути cooldown-таймер на один тік (незалежно від результату
        # цього тіку — інакше лічильник ніколи б не дійшов до нуля)
        self.broker.advance_cooldown()

        # 1. оновити відкриті позиції (можна пропустити, якщо викликач робить це сам)
        if update_positions:
            for pos, pnl, result in self.broker.update(market.asset, market.price):
                self.journal.add(JournalEntry(
                    ts=as_of.isoformat(),
                    asset=pos.asset, mode="paper", direction=pos.direction.value,
                    decision="closed", reason="стоп/тейк",
                    rules_fired=pos.rules_fired, supporting=pos.supporting,
                    entry=pos.entry, stop_loss=pos.stop_loss, take_profit=pos.take_profit,
                    exit=market.price, position_size=pos.size, pnl_usd=pnl, result=result,
                    lesson="перемога — сетап спрацював" if result == "win"
                           else "збиток — переглянути фактори входу",
                ))

        # якщо серія збитків щойно досягла порогу — стартуємо cooldown НА ЧАС,
        # а не назавжди (без нової угоди не буде перемоги, яка б скинула
        # consecutive_losses, тож вічний поріг = вічне блокування)
        c = self.risk.config
        if (self.broker.cooldown_remaining == 0
                and self.broker.consecutive_losses >= c.loss_streak_cooldown):
            self.broker.start_cooldown(c.cooldown_ticks)

        # 2. emergency stop?
        triggered, reason = self.risk.emergency_stop_triggered(self.broker.account_state())
        if triggered:
            return f"⛔ Emergency stop: {reason}"

        # 3. сигнал
        idea, why = self.signal.generate(market, tech, news=news)
        if idea is None:
            return f"⏳ {why}"

        # 4. ризик
        verdict = self.risk.evaluate(idea, market, self.broker.account_state())
        if verdict.is_blocked:
            self.journal.record_rejection(
                market.asset, "paper", idea.direction.value,
                "; ".join(verdict.blocking_reasons), idea.rules_fired, ts=as_of,
            )
            return f"🚫 {verdict.explanation_uk}"

        # 5. відкриваємо
        pos = self.broker.open(idea, verdict.position_size_units, verdict.risk_amount_usd,
                               opened_at=as_of)
        self.journal.add(JournalEntry(
            ts=as_of.isoformat(),
            asset=pos.asset, mode="paper", direction=pos.direction.value,
            decision="opened", reason=idea.why_now,
            rules_fired=idea.rules_fired, supporting=idea.supporting_factors,
            opposing=idea.opposing_factors, entry=pos.entry,
            stop_loss=pos.stop_loss, take_profit=pos.take_profit,
            risk_reward=round(idea.risk_reward, 2), position_size=pos.size,
        ))
        return f"✅ Відкрито {pos.direction.value.upper()} {pos.asset} @ {pos.entry:.4f}"

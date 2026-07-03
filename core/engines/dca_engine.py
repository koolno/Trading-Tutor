"""
DCA Engine — "Надійна (усереднення)" стратегія.

На відміну від Signal/Risk Engine (які намагаються вгадати ЧАС входу),
тут немає вибору моменту: капітал розподіляється на рівні частки і
вкладається через регулярні інтервали протягом усього циклу (dollar-cost
averaging / buy-and-hold). Це навмисно "нудна", але математично захищена
поведінка — вона не намагається перехитрити ринок, і саме тому не залежить
від якості прогнозу.

Позиції не мають реального стоп-лосу/тейк-профіту (buy & hold): вихід —
лише в кінці циклу, коли Session.close_all()/stop_and_review() закриває
все за останньою ціною і фіксує реальний підсумок.
"""
from __future__ import annotations

from datetime import datetime, timezone

from core.engines.journal import Journal, JournalEntry
from core.engines.paper_trading import PaperBroker
from core.engines.signal_engine import TechnicalFactors
from core.models.types import Direction, MarketSnapshot, TradeIdea

# Стоп/тейк для DCA-позиції навмисно поза межами будь-якої реальної ціни —
# це buy & hold, а не угода з таймінгом виходу; єдиний вихід —
# Session.close_all()/stop_and_review() у кінці циклу.
_NEVER_STOP_FACTOR = 0.01
_NEVER_TAKE_FACTOR = 100.0


class DCAEngine:
    def __init__(self, broker: PaperBroker, journal: Journal,
                 assets: list[str], starting_equity: float,
                 num_tranches: int = 30, total_ticks: int | None = None,
                 interval_ticks: int | None = None):
        """total_ticks — довжина ряду наперед відома (fast_sim/historical):
        інтервал рахується як total_ticks // num_tranches. interval_ticks —
        фіксований інтервал напряму (live_realtime, де загальна тривалість
        циклу наперед невідома)."""
        self.broker = broker
        self.journal = journal
        self.num_tranches = max(1, num_tranches)
        if interval_ticks is not None:
            self.interval = max(1, interval_ticks)
        elif total_ticks is not None:
            self.interval = max(1, total_ticks // self.num_tranches)
        else:
            self.interval = 1
        # бюджет ділиться порівну між активами і траншами — фіксований
        # план внесків, а не "скільки лишилось капіталу" (щоб уникнути
        # прив'язки до broker.equity, яка в цій системі не зменшується при
        # відкритті позиції — див. PaperBroker.open())
        self.tranche_usd = starting_equity / len(assets) / self.num_tranches
        self._tick_count: dict[str, int] = {a: 0 for a in assets}
        self._bought: dict[str, int] = {a: 0 for a in assets}

    def step(self, market: MarketSnapshot, tech: TechnicalFactors,
             update_positions: bool = True, news=None,
             as_of: datetime | None = None) -> str:
        as_of = as_of or datetime.now(timezone.utc)
        asset = market.asset

        n = self._tick_count.get(asset, 0)
        self._tick_count[asset] = n + 1
        bought = self._bought.get(asset, 0)

        if bought >= self.num_tranches or n % self.interval != 0:
            return f"⏳ DCA: {asset} чекає наступного планового внеску."

        size = self.tranche_usd / market.price
        reason = f"Плановий внесок DCA №{bought + 1} з {self.num_tranches} (усереднення)"
        idea = TradeIdea(
            asset=asset, direction=Direction.LONG, time_horizon="buy & hold",
            entry_price=market.price,
            stop_loss=market.price * _NEVER_STOP_FACTOR,
            take_profit=market.price * _NEVER_TAKE_FACTOR,
            why_now=reason,
        )
        pos = self.broker.open(idea, size, risk_usd=0.0, opened_at=as_of)
        self.journal.add(JournalEntry(
            ts=as_of.isoformat(), asset=asset, mode="paper", direction="long",
            decision="opened", reason=reason, entry=pos.entry,
            stop_loss=pos.stop_loss, take_profit=pos.take_profit,
            position_size=pos.size,
        ))
        self._bought[asset] = bought + 1
        return f"✅ DCA: куплено {asset} @ {pos.entry:.4f} (внесок {bought + 1}/{self.num_tranches})"

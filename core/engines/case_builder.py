"""
Case Builder (PLAN, етап A2) — перший чесний кейс на реальній історії.

Проганяє ТУ САМУ торгову логіку, що й Backtester/Session (Signal Engine →
Risk Engine → Paper Broker), на конкретному історичному періоді одного
активу і зберігає результат як «кейс»: реальні дати відкриття й закриття
кожної угоди, і моменти, де Risk Engine захистив від збитку (спрацював
стоп-лос).

ВАЖЛИВО (чесність): цей модуль нічого не підбирає й не прикрашає. Період і
дані задає викликач — сам білдер лише чесно документує, що сталось, навіть
якщо результат нульовий або від'ємний. Немає жодної гілки коду, що ховає чи
змінює збиткові угоди. Торгову логіку (Signal/Risk/PaperBroker) цей модуль
не змінює — лише викликає й записує реальні історичні дати.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from core.data.providers import Candle
from core.data.quality import DataQualityEngine
from core.engines.journal import Journal, JournalEntry
from core.engines.paper_trading import PaperBroker, PaperTradingEngine
from core.engines.risk_engine import RiskEngine
from core.engines.signal_engine import SignalEngine
from core.engines.technical import TechnicalAnalysis
from core.models.types import Direction


@dataclass
class CaseTrade:
    """Одна угода в кейсі — з реальними історичними датами (не поточним часом)."""
    asset: str
    direction: str
    opened_at: str
    closed_at: str
    entry: float
    stop_loss: float
    take_profit: float
    exit: float
    pnl_usd: float
    result: str                        # "win" | "loss" | "breakeven"
    protected_from_loss: bool          # True, якщо закрито саме по стоп-лосу
    supporting: list[str] = field(default_factory=list)


@dataclass
class Case:
    """Чесний кейс: період, усі угоди, статистика — без прикрашання."""
    asset: str
    period_start: str
    period_end: str
    starting_equity: float
    ending_equity: float
    trades: list[CaseTrade] = field(default_factory=list)
    rejected_by_risk: int = 0          # скільки разів Risk Engine заветував угоду
    source: str = "real_history"       # "real_history" | "trainer_synthetic" (§E2)

    @property
    def total_return_pct(self) -> float:
        if self.starting_equity == 0:
            return 0.0
        return round((self.ending_equity / self.starting_equity - 1) * 100, 2)

    @property
    def stop_loss_saves(self) -> list[CaseTrade]:
        """Угоди, де спрацював стоп-лос — момент, коли систему захистили від більшого збитку."""
        return [t for t in self.trades if t.protected_from_loss]

    def summary_uk(self) -> str:
        wins = [t for t in self.trades if t.result == "win"]
        losses = [t for t in self.trades if t.result == "loss"]
        lines = [
            f"── Кейс: {self.asset} ({self.period_start} → {self.period_end}) ──",
            f"Стартовий капітал: {self.starting_equity:.2f} USD",
            f"Кінцевий капітал: {self.ending_equity:.2f} USD ({self.total_return_pct:+.2f}%)",
            f"Угод: {len(self.trades)} | Прибуткових: {len(wins)} | Збиткових: {len(losses)}",
            f"Стоп-лос захистив від збитку: {len(self.stop_loss_saves)} раз(и)",
            f"Система відмовилась від угоди через ризик: {self.rejected_by_risk} раз(и)",
        ]
        if self.total_return_pct <= 0:
            lines.append(
                "Результат нульовий або від'ємний — це показано чесно. "
                "Цінність кейсу в контролі ризику, а не в обіцянці прибутку."
            )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "asset": self.asset,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "starting_equity": self.starting_equity,
            "ending_equity": self.ending_equity,
            "total_return_pct": self.total_return_pct,
            "rejected_by_risk": self.rejected_by_risk,
            "stop_loss_saves": len(self.stop_loss_saves),
            "source": self.source,
            "trades": [asdict(t) for t in self.trades],
        }

    def save_json(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def _stop_loss_hit(pos, high: float, low: float) -> bool:
    """Та сама умова, за якою PaperBroker._check визначає закриття по стопу."""
    if pos.direction == Direction.LONG:
        return low <= pos.stop_loss
    return high >= pos.stop_loss


class CaseBuilder:
    """Проганяє торгову логіку на реальній історії й формує чесний кейс."""

    def __init__(self, signal: SignalEngine, risk: RiskEngine):
        self.signal = signal
        self.risk = risk
        self.ta = TechnicalAnalysis()
        self.dq = DataQualityEngine()

    def build(
        self,
        asset: str,
        candles: list[Candle],
        starting_equity: float = 500.0,
        warmup: int = 60,
    ) -> Case:
        """Прогонити систему на candles (реальна історія одного активу) і
        побудувати чесний кейс. warmup — кількість перших свічок, потрібних
        технічному аналізу для розігріву (не торгуємо на них)."""
        if len(candles) <= warmup:
            raise ValueError(
                f"Замало свічок для кейсу: {len(candles)} (потрібно більше за warmup={warmup})."
            )

        broker = PaperBroker(starting_equity=starting_equity)
        journal = Journal()
        engine = PaperTradingEngine(self.signal, self.risk, broker, journal)

        trades: list[CaseTrade] = []

        for end in range(warmup, len(candles)):
            window = candles[: end + 1]
            current = window[-1]

            for pos, pnl, result in broker.update_candle(asset, current.high, current.low):
                stop_hit = _stop_loss_hit(pos, current.high, current.low)
                trades.append(CaseTrade(
                    asset=pos.asset, direction=pos.direction.value,
                    opened_at=pos.opened_at, closed_at=current.ts.isoformat(),
                    entry=round(pos.entry, 8), stop_loss=round(pos.stop_loss, 8),
                    take_profit=round(pos.take_profit, 8),
                    exit=round(pos.stop_loss if stop_hit else pos.take_profit, 8),
                    pnl_usd=pnl, result=result, protected_from_loss=stop_hit,
                    supporting=pos.supporting,
                ))

            report = self.dq.check(window, "1h", check_staleness=False)
            factors, snapshot = self.ta.analyze(asset, window, report.reliable, report.issues)
            engine.step(snapshot, factors, update_positions=False, as_of=current.ts)

        # позиції, що лишились відкритими на кінець періоду, закриваємо по
        # останній ціні — так само, як це робить Backtester/Session.close_all()
        last_candle = candles[-1]
        for pos, pnl, result in broker.update(asset, last_candle.close):
            trades.append(CaseTrade(
                asset=pos.asset, direction=pos.direction.value,
                opened_at=pos.opened_at, closed_at=last_candle.ts.isoformat(),
                entry=round(pos.entry, 8), stop_loss=round(pos.stop_loss, 8),
                take_profit=round(pos.take_profit, 8), exit=round(last_candle.close, 8),
                pnl_usd=pnl, result=result, protected_from_loss=False,
                supporting=pos.supporting,
            ))

        rejected = len([e for e in journal.entries if e.decision == "rejected"])

        return Case(
            asset=asset,
            period_start=candles[warmup].ts.isoformat(),
            period_end=candles[-1].ts.isoformat(),
            starting_equity=starting_equity,
            ending_equity=round(broker.equity, 2),
            trades=trades,
            rejected_by_risk=rejected,
        )


def case_from_journal(
    entries: list[JournalEntry],
    starting_equity: float,
    ending_equity: float,
    source: str = "trainer_synthetic",
) -> Case:
    """
    Будує Case з уже завершеної сесії (§PLAN E2 — «модель фотостоку»: людина
    сама ділиться своїм кейсом). На відміну від CaseBuilder.build(), нічого
    не проганяє наново — просто чесно конвертує вже записаний журнал.

    ВАЖЛИВО: source за замовчуванням "trainer_synthetic", бо сесії
    тренажера/paper зараз працюють на синтетичних даних (SyntheticProvider),
    а не на реальній історії — приховувати це було б нечесно (§3).
    """
    closed = [e for e in entries if e.decision == "closed"]
    if not closed:
        raise ValueError("Немає закритих угод — нема чого зберігати як кейс.")

    trades = [
        CaseTrade(
            asset=e.asset, direction=e.direction,
            opened_at=e.ts, closed_at=e.ts,
            entry=e.entry or 0.0, stop_loss=e.stop_loss or 0.0,
            take_profit=e.take_profit or 0.0, exit=e.exit or 0.0,
            pnl_usd=e.pnl_usd or 0.0, result=e.result or "breakeven",
            protected_from_loss=(e.result == "loss"),
            supporting=e.supporting or [],
        )
        for e in closed
    ]
    rejected = len([e for e in entries if e.decision == "rejected"])
    assets = sorted({t.asset for t in trades})
    asset_label = assets[0] if len(assets) == 1 else " + ".join(assets)

    return Case(
        asset=asset_label,
        period_start=trades[0].opened_at,
        period_end=trades[-1].closed_at,
        starting_equity=starting_equity,
        ending_equity=round(ending_equity, 2),
        trades=trades,
        rejected_by_risk=rejected,
        source=source,
    )

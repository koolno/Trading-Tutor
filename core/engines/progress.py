"""
Progress Summary (PLAN, етап E1) — збережений прогрес користувача.

Той самий принцип, що й Understanding Summary (§C3): НЕ бали, НЕ рівні.
Тут — чесний накопичений підсумок усіх минулих навчальних циклів (не лише
останнього): скільки циклів пройдено, скільки разів стоп-лос захистив від
збитку, скільки разів систем відмовилась через ризик. Дані зберігаються в
БД (core/storage/db.py), щоб не губитися між запусками застосунку (§37) —
людина повертається і бачить, що вже пройшла, а не починає з нуля щоразу.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.engines.understanding import _times_uk


@dataclass
class ProgressSummary:
    """Накопичений прогрес за весь час — без балів і рівнів."""
    cycles: int
    total_trades: int
    total_stop_loss_saves: int
    total_rejected: int
    insights_uk: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "cycles": self.cycles,
            "total_trades": self.total_trades,
            "total_stop_loss_saves": self.total_stop_loss_saves,
            "total_rejected": self.total_rejected,
            "insights": self.insights_uk,
        }


def build_progress_summary(
    cycles: int, total_trades: int, total_stop_loss_saves: int, total_rejected: int,
) -> ProgressSummary:
    if cycles == 0:
        return ProgressSummary(
            cycles=0, total_trades=0, total_stop_loss_saves=0, total_rejected=0,
            insights_uk=["Ти ще не завершив жодного циклу — почни з тренажера, "
                         "щоб побачити свій перший прогрес."],
        )

    insights: list[str] = [
        f"За весь час ти пройшов {cycles} {_cycles_uk(cycles)} і побачив "
        f"{total_trades} угод системи."
    ]
    if total_stop_loss_saves:
        insights.append(
            f"Стоп-лос захистив тебе від більшого збитку {total_stop_loss_saves} "
            f"{_times_uk(total_stop_loss_saves)} за весь час — це і є контроль ризику в дії."
        )
    if total_rejected:
        insights.append(
            f"Система відмовилась від угоди через ризик {total_rejected} "
            f"{_times_uk(total_rejected)} — вона не намагається торгувати завжди."
        )
    return ProgressSummary(
        cycles=cycles, total_trades=total_trades,
        total_stop_loss_saves=total_stop_loss_saves, total_rejected=total_rejected,
        insights_uk=insights,
    )


def _cycles_uk(n: int) -> str:
    """Українське відмінювання «цикл/цикли/циклів» за числом n."""
    if n % 10 == 1 and n % 100 != 11:
        return "цикл"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return "цикли"
    return "циклів"

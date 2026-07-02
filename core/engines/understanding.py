"""
Understanding Summary (PLAN, етап C3) — розуміння, а не очки.

НЕ бали, НЕ рівні (ігрова механіка, що затягує — суперечить продукту про
обережність). Натомість — чесні прості підсумки того, що людина побачила:
«тепер ти знаєш, що таке стоп-лос», «ти побачив, як захист спрацював
3 рази». Мета — щоб людина стала спокійнішою і свідомішою, а не залежною
від застосунку (§2.3, PLAN C3).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.engines.journal import JournalEntry


def _times_uk(n: int) -> str:
    """Українське відмінювання «раз/рази/разів» за числом n."""
    if n % 10 == 1 and n % 100 != 11:
        return "раз"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return "рази"
    return "разів"


@dataclass
class UnderstandingSummary:
    """Список простих тверджень про розуміння — жодних балів чи рівнів."""
    insights_uk: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"insights": self.insights_uk}


def build_understanding_summary(entries: list[JournalEntry]) -> UnderstandingSummary:
    closed = [e for e in entries if e.decision == "closed"]
    opened = [e for e in entries if e.decision == "opened"]
    rejected = [e for e in entries if e.decision == "rejected"]
    losses = [e for e in closed if e.result == "loss"]
    wins = [e for e in closed if e.result == "win"]

    insights: list[str] = []

    if losses:
        insights.append(
            f"Тепер ти знаєш, що таке стоп-лос: ти побачив, як захист спрацював "
            f"{len(losses)} {_times_uk(len(losses))} і зупинив збиток, перш ніж він виріс."
        )
    if wins:
        insights.append(
            f"Ти побачив, як система бере прибуток, коли ціна досягає цілі "
            f"({len(wins)} {_times_uk(len(wins))})."
        )
    if rejected:
        insights.append(
            f"Ти побачив, що система не входить у кожну можливість — вона "
            f"пропустила {len(rejected)} {_times_uk(len(rejected))}, коли ризик був завеликий."
        )
    if opened:
        insights.append(
            "Ти побачив, як система відкриває угоду лише тоді, коли має "
            "кілька підтверджень, а не навмання."
        )
    if not insights:
        insights.append(
            "Система ще не встигла нічого показати за цей цикл — спробуй "
            "пройти довший цикл, щоб побачити, як вона працює."
        )
    return UnderstandingSummary(insights_uk=insights)

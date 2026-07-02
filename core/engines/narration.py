"""
Trainer Narration (PLAN, етап C2) — пояснення кожного кроку простою мовою.

Перетворює те, що вже сталося (журнал угод, причина очікування, аварійна
зупинка), на прості речення на кшталт «система почекала, бо…», «система
вийшла зі збитком, бо спрацював захист…». Нічого не вирішує і не змінює
торгову логіку — лише пояснює вже ухвалене рішення людською мовою
(§2.3 «Простота як розмова», PLAN C2).
"""
from __future__ import annotations

from core.engines.journal import JournalEntry

_WAIT_PREFIXES = "⏳🚫✅⛔ "


def _clean_reason(text: str) -> str:
    """Прибирає емодзі-префікс і технічний хвіст «Краще чекати.»."""
    reason = text.strip().lstrip(_WAIT_PREFIXES).strip()
    for suffix in ("Краще чекати.", "краще чекати."):
        if reason.endswith(suffix):
            reason = reason[: -len(suffix)].strip()
    return reason.rstrip(".")


def narrate_wait_uk(asset: str, why: str) -> str:
    """«Система почекала з BTC/USDT, бо ...» — коли ідеї для угоди немає."""
    reason = _clean_reason(why)
    tail = reason.lower() if reason else "ще не побачила достатньо підстав для входу"
    return f"Система почекала з {asset}, бо {tail}."


def narrate_emergency_stop_uk(asset: str, reason: str) -> str:
    """«Система зупинилась, бо ...» — коли спрацював аварійний стоп рахунку."""
    tail = _clean_reason(reason)
    return f"Система зупинилась на {asset}, бо ризик зашкалив: {tail.lower()}."


def narrate_entry_uk(entry: JournalEntry) -> str:
    """Одне просте речення про те, що сталося з угодою — без жаргону."""
    if entry.decision == "opened":
        direction = "купівлю" if entry.direction == "long" else "продаж"
        return (f"Система відкрила угоду ({direction}) по {entry.asset}, "
                f"бо побачила кілька підтверджень за цим напрямом.")
    if entry.decision == "rejected":
        reason = _clean_reason(entry.reason.split(";")[0])
        tail = reason.lower() if reason else "ризик-контроль не дозволив"
        return f"Система відмовилась від угоди по {entry.asset}, бо {tail}."
    if entry.decision == "closed":
        if entry.result == "win":
            return (f"Система вийшла з прибутком по {entry.asset} — "
                     f"ціна дійшла до цілі (тейк-профіт).")
        if entry.result == "loss":
            return (f"Система вийшла зі збитком по {entry.asset}, бо спрацював захист "
                     f"(стоп-лос) — він обмежив втрату, а не дав їй вирости.")
        return f"Система закрила угоду по {entry.asset} без прибутку і без збитку."
    return entry.reason

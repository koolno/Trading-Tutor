"""
Journal (§28).

Зберігає кожну ідею і кожну угоду з повним контекстом. Це джерело даних
для Learning Engine і звітів. Запис у памʼяті + опційне збереження у JSON.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class JournalEntry:
    ts: str
    asset: str
    mode: str
    direction: str
    decision: str                 # "opened" | "rejected" | "closed"
    reason: str                   # причина входу або відмови
    rules_fired: list[str] = field(default_factory=list)
    supporting: list[str] = field(default_factory=list)
    opposing: list[str] = field(default_factory=list)
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    exit: Optional[float] = None
    risk_reward: Optional[float] = None
    position_size: Optional[float] = None
    pnl_usd: Optional[float] = None
    result: Optional[str] = None  # "win" | "loss" | "breakeven"
    lesson: str = ""              # що система вивчила


class Journal:
    def __init__(self, path: str | None = None):
        self.entries: list[JournalEntry] = []
        self.path = Path(path) if path else None

    def add(self, entry: JournalEntry) -> None:
        self.entries.append(entry)
        if self.path:
            self._flush()

    def record_rejection(self, asset, mode, direction, reason, rules=None,
                          ts: datetime | None = None):
        self.add(JournalEntry(
            ts=(ts or datetime.now(timezone.utc)).isoformat(),
            asset=asset, mode=mode, direction=direction,
            decision="rejected", reason=reason, rules_fired=rules or [],
        ))

    def add_close(self, pos, pnl, result, exit_price, mode="paper", ts: datetime | None = None):
        """Єдиний спосіб журналювати закриття позиції (використовують і сесія, і бектест)."""
        self.add(JournalEntry(
            ts=(ts or datetime.now(timezone.utc)).isoformat(),
            asset=pos.asset, mode=mode, direction=pos.direction.value,
            decision="closed", reason="стоп/тейк", rules_fired=pos.rules_fired,
            supporting=pos.supporting, entry=pos.entry, stop_loss=pos.stop_loss,
            take_profit=pos.take_profit, exit=exit_price, position_size=pos.size,
            pnl_usd=pnl, result=result,
            lesson="перемога — сетап спрацював" if result == "win"
                   else "збиток — переглянути фактори входу",
        ))

    def closed_trades(self) -> list[JournalEntry]:
        return [e for e in self.entries if e.decision == "closed"]

    def _flush(self) -> None:
        self.path.write_text(
            json.dumps([asdict(e) for e in self.entries], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

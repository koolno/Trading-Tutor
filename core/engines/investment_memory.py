"""
Investment Memory (§19).

Зберігає ринкові ситуації, патерни, реакції на новини/події, успішні й
невдалі сценарії. Кожне спостереження має опис, джерела, кількість
підтверджень, confidence, режим ринку, клас активу, корисність, статус.

Ключове правило §19: слабкі спостереження НЕ перетворюються на жорсткі
правила. Пам'ять лише накопичує спостереження; підвищення статусу вимагає
достатньої кількості підтверджень.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.storage.db import MemoryObservation, get_session


@dataclass
class Observation:
    description: str
    asset_class: str = ""
    market_regime: str = ""
    confidence: float = 0.5
    confirmations: int = 1
    usefulness: str = "uncertain"
    sources: list[str] = field(default_factory=list)


class InvestmentMemory:
    """Обгортка над таблицею memory з обережною логікою підвищення статусу."""

    # скільки підтверджень треба, щоб спостереження стало «корисним»
    PROMOTE_THRESHOLD = 5

    def remember(self, obs: Observation) -> int:
        """Додає спостереження або підсилює наявне (за однаковим описом)."""
        s = get_session()
        try:
            existing = (s.query(MemoryObservation)
                        .filter(MemoryObservation.description == obs.description)
                        .first())
            if existing:
                existing.confirmations += 1
                existing.confidence = min(1.0, existing.confidence + 0.05)
                if existing.confirmations >= self.PROMOTE_THRESHOLD:
                    existing.usefulness = "useful"
                s.commit()
                return existing.id
            row = MemoryObservation(
                description=obs.description, asset_class=obs.asset_class,
                market_regime=obs.market_regime, confirmations=obs.confirmations,
                confidence=obs.confidence, usefulness=obs.usefulness,
                sources=obs.sources, status="active",
            )
            s.add(row)
            s.commit()
            return row.id
        finally:
            s.close()

    def recall(self, asset_class: str | None = None, min_confirmations: int = 1,
               limit: int = 20) -> list[dict]:
        s = get_session()
        try:
            q = s.query(MemoryObservation).filter(
                MemoryObservation.confirmations >= min_confirmations)
            if asset_class:
                q = q.filter(MemoryObservation.asset_class == asset_class)
            q = q.order_by(MemoryObservation.confirmations.desc()).limit(limit)
            return [{
                "description": r.description, "confirmations": r.confirmations,
                "confidence": round(r.confidence, 2), "usefulness": r.usefulness,
                "regime": r.market_regime, "asset_class": r.asset_class,
            } for r in q.all()]
        finally:
            s.close()

    def useful_observations(self) -> list[dict]:
        """Лише ті, що набрали достатньо підтверджень (§19)."""
        return [o for o in self.recall(min_confirmations=self.PROMOTE_THRESHOLD)]

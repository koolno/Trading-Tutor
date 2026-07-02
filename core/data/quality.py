"""
Data Quality Engine (§10).

Якщо дані ненадійні — система НЕ повинна відкривати угоди (Core Safety CS-004).
Цей модуль перевіряє свічки і повертає вердикт, який потім потрапляє у
MarketSnapshot.data_is_reliable.

Перевіряємо:
  • достатню кількість свічок;
  • пропуски у часовій сітці;
  • невалідні свічки (high < low, нульові/відʼємні ціни);
  • аномальні стрибки ціни між свічками;
  • застарілість останньої свічки.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from core.data.providers import Candle

_TIMEFRAME_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400,
}


@dataclass
class DataQualityReport:
    reliable: bool
    issues: list[str] = field(default_factory=list)
    checked: int = 0


class DataQualityEngine:
    def __init__(self, max_jump_pct: float = 35.0, max_gap_factor: float = 2.5,
                 min_candles: int = 30, max_stale_factor: float = 3.0):
        self.max_jump_pct = max_jump_pct
        self.max_gap_factor = max_gap_factor
        self.min_candles = min_candles
        self.max_stale_factor = max_stale_factor

    def check(self, candles: list[Candle], timeframe: str = "1h",
              check_staleness: bool = True) -> DataQualityReport:
        issues: list[str] = []

        if len(candles) < self.min_candles:
            issues.append(f"Замало свічок ({len(candles)} < {self.min_candles}).")
            return DataQualityReport(False, issues, len(candles))

        tf_sec = _TIMEFRAME_SECONDS.get(timeframe, 3600)

        for i, c in enumerate(candles):
            # невалідні свічки
            if c.high < c.low:
                issues.append(f"Свічка #{i}: high < low.")
            if min(c.open, c.high, c.low, c.close) <= 0:
                issues.append(f"Свічка #{i}: недодатна ціна.")
            # аномальні стрибки
            if i > 0 and candles[i - 1].close > 0:
                jump = abs(c.close - candles[i - 1].close) / candles[i - 1].close * 100
                if jump > self.max_jump_pct:
                    issues.append(f"Свічка #{i}: стрибок ціни {jump:.1f}%.")
                # пропуск у часі
                gap = (c.ts - candles[i - 1].ts).total_seconds()
                if gap > tf_sec * self.max_gap_factor:
                    issues.append(f"Пропуск даних перед свічкою #{i}.")

        # застарілість останньої свічки (пропускаємо у режимі реплею/бектесту)
        if check_staleness:
            age = (datetime.now(timezone.utc) - candles[-1].ts).total_seconds()
            if age > tf_sec * self.max_stale_factor:
                issues.append("Останні дані застарілі.")

        # обмежуємо список, щоб не засмічувати журнал
        unique = list(dict.fromkeys(issues))[:8]
        return DataQualityReport(reliable=len(unique) == 0, issues=unique, checked=len(candles))

"""
Probability Engine (PLAN, етап A4) — ймовірності на основі історії, з поясненням.

Для поточної ситуації (технічні фактори) рахує просту статистику: як часто
в РЕАЛЬНІЙ історії схожі умови вели до зростання чи падіння ціни за заданий
горизонт. Це проста статистика по минулих схожих випадках, а НЕ порада
"купуй" — завжди з поясненням чому і що може піти не так. Рішення завжди
лишається за людиною (§1, §3 принцип «Довіра через контроль»).

Використовує ту саму TechnicalAnalysis, що й Signal Engine, — щоб «схожа
ситуація» означала те саме, за чим система і зараз оцінює ринок.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.data.providers import Candle
from core.engines.signal_engine import TechnicalFactors
from core.engines.technical import TechnicalAnalysis


def _rsi_bucket(rsi_val: float) -> str:
    if rsi_val < 30:
        return "low"
    if rsi_val > 70:
        return "high"
    return "mid"


def _situation_signature(tech: TechnicalFactors) -> tuple:
    """Грубий «підпис» ситуації — для пошуку СХОЖИХ (не ідентичних) випадків."""
    return (
        tech.trend_up, tech.trend_down, tech.macd_bullish, tech.macd_bearish,
        tech.near_support, tech.near_resistance, _rsi_bucket(tech.rsi),
    )


def _situation_label_uk(tech: TechnicalFactors) -> str:
    parts = []
    if tech.trend_up:
        parts.append("висхідний тренд")
    if tech.trend_down:
        parts.append("низхідний тренд")
    if tech.macd_bullish:
        parts.append("MACD бичачий")
    if tech.macd_bearish:
        parts.append("MACD ведмежий")
    if tech.near_support:
        parts.append("ціна біля підтримки")
    if tech.near_resistance:
        parts.append("ціна біля опору")
    if tech.rsi < 30:
        parts.append(f"RSI низький ({tech.rsi:.0f})")
    elif tech.rsi > 70:
        parts.append(f"RSI високий ({tech.rsi:.0f})")
    return ", ".join(parts) if parts else "нейтральна ситуація без явних сигналів"


def _why_uk() -> str:
    return (
        "статистика рахується по всіх минулих моментах в історії з такими ж "
        "технічними ознаками (тренд, MACD, RSI, підтримка/опір) і показує, "
        "чим вони закінчувались через кілька свічок."
    )


def _what_could_go_wrong_uk() -> str:
    return (
        "минулі схожі ситуації не гарантують такого самого результату зараз — "
        "ринок може змінитися через новини, низьку ліквідність або умови, "
        "яких не було в історії. Це статистика по минулому, а не пророцтво."
    )


@dataclass
class ProbabilityInsight:
    """Чесна статистика по минулому. НЕ порада купувати чи продавати (§34)."""
    asset: str
    situation_label: str
    sample_size: int
    up_count: int
    down_count: int
    flat_count: int
    horizon_candles: int
    why_uk: str
    what_could_go_wrong_uk: str

    @property
    def up_pct(self) -> float:
        return round(self.up_count / self.sample_size * 100, 1) if self.sample_size else 0.0

    @property
    def down_pct(self) -> float:
        return round(self.down_count / self.sample_size * 100, 1) if self.sample_size else 0.0

    @property
    def flat_pct(self) -> float:
        return round(self.flat_count / self.sample_size * 100, 1) if self.sample_size else 0.0

    @property
    def sample_is_sufficient(self) -> bool:
        """Мала вибірка не має видаватись за надійний висновок (§9, §18)."""
        return self.sample_size >= 20

    def explanation_uk(self) -> str:
        lines = [
            f"У схожих ситуаціях по {self.asset} ({self.situation_label}) — "
            f"{self.sample_size} таких випадків в історії:",
            f"  • ціна зростала у {self.up_pct}% випадків",
            f"  • ціна падала у {self.down_pct}% випадків",
            f"  • майже не змінювалась у {self.flat_pct}% випадків",
            f"Чому: {self.why_uk}",
            f"Що може піти не так: {self.what_could_go_wrong_uk}",
            "Це статистика по минулому, а НЕ порада купувати чи продавати. "
            "Минуле не гарантує майбутнього. Рішення — за тобою.",
        ]
        if not self.sample_is_sufficient:
            lines.append(
                f"⚠ Вибірка мала ({self.sample_size} випадків) — висновок ненадійний, "
                "довіряти йому варто обережно."
            )
        return "\n".join(lines)


class ProbabilityEngine:
    """Рахує, як часто схожі технічні ситуації в історії вели до зростання/падіння."""

    def __init__(self, horizon_candles: int = 12, flat_threshold_pct: float = 0.2):
        self.horizon_candles = horizon_candles
        self.flat_threshold_pct = flat_threshold_pct
        self.ta = TechnicalAnalysis()

    def analyze(self, asset: str, candles: list[Candle], warmup: int = 60
                ) -> ProbabilityInsight | None:
        """
        Знаходить у попередній історії моменти зі СХОЖОЮ ситуацією до поточної
        (останньої свічки) і рахує, чим вони закінчувались через
        horizon_candles свічок. Повертає None, якщо історії замало, щоб
        узагалі оцінити поточну ситуацію — чесніше нічого не сказати, ніж
        видати статистику з порожньої чи випадкової вибірки.
        """
        if len(candles) <= warmup + self.horizon_candles:
            return None

        current_factors, _ = self.ta.analyze(asset, candles)
        current_sig = _situation_signature(current_factors)

        up = down = flat = 0
        # історія без останніх horizon_candles свічок — для них ще немає "майбутнього" в даних
        for end in range(warmup, len(candles) - self.horizon_candles):
            window = candles[: end + 1]
            factors, _ = self.ta.analyze(asset, window)
            if _situation_signature(factors) != current_sig:
                continue
            now_price = window[-1].close
            future_price = candles[end + self.horizon_candles].close
            change_pct = (future_price - now_price) / now_price * 100 if now_price else 0.0
            if change_pct > self.flat_threshold_pct:
                up += 1
            elif change_pct < -self.flat_threshold_pct:
                down += 1
            else:
                flat += 1

        sample = up + down + flat
        if sample == 0:
            return None

        return ProbabilityInsight(
            asset=asset,
            situation_label=_situation_label_uk(current_factors),
            sample_size=sample,
            up_count=up, down_count=down, flat_count=flat,
            horizon_candles=self.horizon_candles,
            why_uk=_why_uk(),
            what_could_go_wrong_uk=_what_could_go_wrong_uk(),
        )

"""
Advice Explanation (PLAN, етап B2) — екран №2 «Що радить і чому».

Поєднує ймовірність з історії (Probability Engine, §A4) із простими,
без жаргону факторами «за» і «проти» поточного руху ціни. Модуль НІЧОГО не
радить купувати чи продавати — лише пояснює, чому система бачить ситуацію
саме так, і завжди лишає рішення людині (§1, §3, §14, §34).

Фактори тут навмисно НЕ беруться напряму з Signal Engine (яка відфільтровує
слабкі сигнали й повертає None, ховаючи фактори) — щоб екран міг чесно
пояснити ситуацію навіть тоді, коли системі бракує впевненості для ідеї.
Торгову логіку (Signal/Risk Engine) цей модуль не змінює.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.data.providers import Candle
from core.engines.probability import ProbabilityEngine, ProbabilityInsight
from core.engines.signal_engine import TechnicalFactors
from core.engines.technical import TechnicalAnalysis


def factors_for_against_uk(tech: TechnicalFactors) -> tuple[list[str], list[str]]:
    """Прості причини «за зростання» / «за падіння» — без жаргону (RSI/MACD не звучать)."""
    for_up: list[str] = []
    for_down: list[str] = []

    if tech.trend_up:
        for_up.append("Ціна останнім часом частіше росте, ніж падає.")
    if tech.macd_bullish:
        for_up.append("Короткострокова динаміка ціни прискорюється вгору.")
    if tech.near_support:
        for_up.append("Ціна біля рівня, звідки вона раніше вже відскакувала вгору.")
    if tech.breakout_up:
        for_up.append("Ціна щойно пробила свій недавній максимум.")
    if tech.rsi < 30:
        for_up.append("Ціна впала дуже сильно і швидко — часто після такого буває відскок.")

    if tech.trend_down:
        for_down.append("Ціна останнім часом частіше падає, ніж росте.")
    if tech.macd_bearish:
        for_down.append("Короткострокова динаміка ціни прискорюється вниз.")
    if tech.near_resistance:
        for_down.append("Ціна біля рівня, звідки вона раніше вже відкочувалась вниз.")
    if tech.rsi > 70:
        for_down.append("Ціна виросла дуже сильно і швидко — часто після такого буває відкат.")

    return for_up, for_down


@dataclass
class AdviceExplanation:
    """Вміст екрана №2: ймовірність + пояснення + фактори за/проти. НЕ порада купувати (§34)."""
    asset: str
    price: float
    factors_for_uk: list[str] = field(default_factory=list)
    factors_against_uk: list[str] = field(default_factory=list)
    probability: ProbabilityInsight | None = None

    def to_dict(self) -> dict:
        prob = None
        if self.probability is not None:
            p = self.probability
            prob = {
                "up_pct": p.up_pct, "down_pct": p.down_pct, "flat_pct": p.flat_pct,
                "sample_size": p.sample_size, "sample_is_sufficient": p.sample_is_sufficient,
                "why": p.why_uk, "what_could_go_wrong": p.what_could_go_wrong_uk,
                "horizon_candles": p.horizon_candles,
            }
        return {
            "asset": self.asset,
            "price": self.price,
            "factors_for": self.factors_for_uk,
            "factors_against": self.factors_against_uk,
            "probability": prob,
        }


class AdviceEngine:
    """Будує вміст екрана №2 — жодної команди «купуй», лише пояснення (§B2)."""

    def __init__(self, probability_engine: ProbabilityEngine | None = None):
        self.ta = TechnicalAnalysis()
        self.prob = probability_engine or ProbabilityEngine()

    def explain(self, asset: str, candles: list[Candle], warmup: int = 60) -> AdviceExplanation:
        factors, snapshot = self.ta.analyze(asset, candles)
        for_up, for_down = factors_for_against_uk(factors)
        insight = self.prob.analyze(asset, candles, warmup=warmup)
        return AdviceExplanation(
            asset=asset,
            price=round(snapshot.price, 4),
            factors_for_uk=for_up,
            factors_against_uk=for_down,
            probability=insight,
        )

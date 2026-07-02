"""
Plain Language Module (PLAN, етап B1) — переклад технічних факторів у
просту мову без жаргону, для екрана «Що система бачить».

Жодних RSI/MACD/ATR/EMA у виводі — лише прості речення про те, куди
рухається ціна, наскільки спокійно чи неспокійно зараз на ринку, і чи ціна
близько до рівня, де вона раніше вже відштовхувалась. Це лише опис
ситуації, без порад купувати чи продавати (§1, §3 «Простота як розмова»).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.engines.signal_engine import TechnicalFactors
from core.models.types import MarketRegime, MarketSnapshot


@dataclass
class PlainSituation:
    """Опис поточної ситуації простою мовою — вхід для екрана №1 (B1)."""
    asset: str
    price: float
    headline_uk: str
    details_uk: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "asset": self.asset,
            "price": self.price,
            "headline": self.headline_uk,
            "details": self.details_uk,
        }


def describe_situation_uk(factors: TechnicalFactors, snapshot: MarketSnapshot) -> PlainSituation:
    """Перетворює технічні фактори й знімок ринку на прості українські речення."""
    details: list[str] = []

    if factors.trend_up:
        headline = f"{snapshot.asset}: ціна останнім часом переважно росте."
        details.append("Ціна рухається вгору частіше, ніж вниз.")
    elif factors.trend_down:
        headline = f"{snapshot.asset}: ціна останнім часом переважно падає."
        details.append("Ціна рухається вниз частіше, ніж вгору.")
    else:
        headline = f"{snapshot.asset}: чіткого напряму зараз немає."
        details.append("Ціна тримається приблизно на одному рівні, без чіткого напряму.")

    if snapshot.regime == MarketRegime.HIGH_VOLATILITY:
        details.append("Зараз ціна стрибає сильніше, ніж зазвичай — ринок неспокійний.")
    elif snapshot.volatility_atr_pct < 1.0:
        details.append("Ціна змінюється плавно, без різких стрибків.")
    else:
        details.append("Коливання ціни зараз у звичайних межах.")

    if factors.near_support:
        details.append("Ціна близько до рівня, від якого вона раніше вже відштовхувалась вгору.")
    if factors.near_resistance:
        details.append("Ціна близько до рівня, від якого вона раніше вже відкочувалась вниз.")

    if not snapshot.data_is_reliable:
        details.append("Дані зараз ненадійні — система тимчасово обережніша.")

    return PlainSituation(
        asset=snapshot.asset,
        price=round(snapshot.price, 4),
        headline_uk=headline,
        details_uk=details,
    )

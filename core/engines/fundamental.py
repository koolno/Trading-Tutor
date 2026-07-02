"""
Fundamental Analysis Module (§13).

Для криптовалют: ліквідність, обсяг, волатильність, регуляторні ризики.
Для акцій/ETF: P/E, зростання прибутку, борг, маржа тощо.

Через відсутність інтернету у середовищі розробки провайдер за замовчуванням
працює на переданих або мок-даних. На твоєму комп'ютері підставляється
реальне джерело (напр. дані біржі для крипти, Yahoo/AlphaVantage для акцій).

Результат — FundamentalContext зі score (-1..+1), який Signal Engine може
використати як додатковий фактор або фільтр.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FundamentalContext:
    asset: str
    asset_class: str = "crypto"       # crypto | stock
    score: float = 0.0               # -1 (слабко) .. +1 (сильно)
    notes: list[str] = field(default_factory=list)
    summary_uk: str = ""

    @property
    def is_weak(self) -> bool:
        return self.score < -0.3

    @property
    def is_strong(self) -> bool:
        return self.score > 0.3

    def as_factors(self) -> list[str]:
        if self.is_strong:
            return ["Сильні фундаментальні показники"]
        if self.is_weak:
            return ["Слабкі фундаментальні показники"]
        return []


class FundamentalAnalysis:
    """Оцінка фундаменталу. Метрики передаються ззовні (з провайдера/біржі)."""

    def analyze_crypto(self, asset: str, *, liquidity_score: float = 0.5,
                       volume_trend: float = 0.0, volatility_atr_pct: float = 3.0,
                       regulatory_risk: float = 0.0) -> FundamentalContext:
        """
        liquidity_score 0..1, volume_trend -1..1 (спад/ріст обсягу),
        volatility_atr_pct — %, regulatory_risk 0..1 (вищий = гірше).
        """
        score = 0.0
        notes = []
        score += (liquidity_score - 0.5) * 0.6
        if liquidity_score < 0.3:
            notes.append("низька ліквідність")
        score += volume_trend * 0.3
        if volume_trend > 0.2:
            notes.append("зростання обсягу")
        elif volume_trend < -0.2:
            notes.append("падіння обсягу")
        if volatility_atr_pct > 10:
            score -= 0.2
            notes.append("надвисока волатильність")
        score -= regulatory_risk * 0.5
        if regulatory_risk > 0.5:
            notes.append("регуляторний ризик")
        score = max(-1.0, min(1.0, score))
        ctx = FundamentalContext(asset, "crypto", round(score, 3), notes)
        ctx.summary_uk = self._summary(ctx)
        return ctx

    def analyze_stock(self, asset: str, *, pe: float | None = None,
                      earnings_growth: float = 0.0, debt_to_equity: float | None = None,
                      profit_margin: float = 0.0) -> FundamentalContext:
        score = 0.0
        notes = []
        if pe is not None:
            if 0 < pe < 20:
                score += 0.25; notes.append("помірний P/E")
            elif pe > 50:
                score -= 0.25; notes.append("високий P/E")
        score += max(-0.3, min(0.3, earnings_growth))
        if earnings_growth > 0.1:
            notes.append("зростання прибутку")
        if debt_to_equity is not None and debt_to_equity > 2:
            score -= 0.2; notes.append("високий борг")
        score += max(-0.2, min(0.2, profit_margin))
        score = max(-1.0, min(1.0, score))
        ctx = FundamentalContext(asset, "stock", round(score, 3), notes)
        ctx.summary_uk = self._summary(ctx)
        return ctx

    @staticmethod
    def _summary(ctx: FundamentalContext) -> str:
        base = ", ".join(ctx.notes) if ctx.notes else "без особливостей"
        if ctx.is_strong:
            return f"Фундаментал сильний ({base})."
        if ctx.is_weak:
            return f"Фундаментал слабкий ({base})."
        return f"Фундаментал нейтральний ({base})."

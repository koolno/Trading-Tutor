"""
Signal Engine (§14).

Не видає просте «купити/продати». Будує структуровану TradeIdea зі стопом,
тейком, логікою, факторами «за» і «проти», та переліком правил, що спрацювали.

Тут навмисно проста, але чесна логіка ухвалення:
  - збираємо технічні фактори;
  - звіряємо з правилами конституції (R-011: ≥2 незалежних підтвердження);
  - якщо переваги немає — повертаємо None і кажемо «краще чекати» (R-020).

Реальні індикатори (RSI, MACD, EMA, ATR) рахуються в TechnicalAnalysis і
передаються сюди як готові фактори. Це тримає Signal Engine незалежним від
джерела даних.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.knowledge.constitution import Rule
from core.models.types import (
    Confidence,
    Direction,
    MarketRegime,
    MarketSnapshot,
    TradeIdea,
)


@dataclass
class TechnicalFactors:
    """Готові технічні фактори по активу (вихід TA-модуля)."""
    trend_up: bool = False
    trend_down: bool = False
    rsi: float = 50.0
    macd_bullish: bool = False
    macd_bearish: bool = False
    near_support: bool = False
    near_resistance: bool = False
    breakout_up: bool = False
    atr_pct: float = 1.0          # ATR як % ціни — для розрахунку стопу


class SignalEngine:
    """
    Пороги нижче (atr_stop_mult, rr_target, rsi_oversold/overbought) —
    параметри стратегії, а не жорсткі константи: "Оптимізована по історії"
    (§ демонстрація overfitting) підбирає інші значення через
    strategy_optimizer.fit_optimized_params(), щоб показати різницю між
    "Класична" (фіксовані підручникові пороги) і підігнаним варіантом.
    """
    def __init__(self, rules: list[Rule], min_confirmations: int = 2,
                 atr_stop_mult: float = 1.5, rr_target: float = 2.0,
                 rsi_oversold: float = 30.0, rsi_overbought: float = 75.0):
        self.rules = {r.id: r for r in rules}
        self.min_confirmations = min_confirmations  # R-011
        self.atr_stop_mult = atr_stop_mult
        self.rr_target = rr_target
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought

    def generate(
        self,
        market: MarketSnapshot,
        tech: TechnicalFactors,
        news=None,                      # NewsContext | None
    ) -> tuple[TradeIdea | None, str]:
        """
        Повертає (ідея, пояснення).
        Якщо ідеї немає — (None, причина чому краще чекати).
        news — необов'язковий NewsContext; сильна суперечлива новина блокує вхід.
        """
        long_factors: list[str] = []
        short_factors: list[str] = []
        rules_fired: list[str] = []

        # --- збір факторів «за лонг» -------------------------------------- #
        if tech.trend_up:
            long_factors.append("Висхідний тренд")
        if tech.macd_bullish:
            long_factors.append("MACD бичачий")
        if tech.near_support:
            long_factors.append("Ціна біля підтримки")
        if tech.breakout_up:
            long_factors.append("Пробій вгору")
        if tech.rsi < self.rsi_oversold:
            long_factors.append(f"RSI низький ({tech.rsi:.0f}) — перепроданість")

        # --- збір факторів «за шорт» -------------------------------------- #
        if tech.trend_down:
            short_factors.append("Низхідний тренд")
        if tech.macd_bearish:
            short_factors.append("MACD ведмежий")
        if tech.near_resistance:
            short_factors.append("Ціна біля опору")
        if tech.rsi > self.rsi_overbought:
            short_factors.append(f"RSI високий ({tech.rsi:.0f}) — перекупленість")

        # --- вибір напряму за перевагою ----------------------------------- #
        if len(long_factors) > len(short_factors):
            direction = Direction.LONG
            supporting, opposing = long_factors, short_factors
        elif len(short_factors) > len(long_factors):
            direction = Direction.SHORT
            supporting, opposing = short_factors, long_factors
        else:
            return None, "Сигнали врівноважені — переваги немає. Краще чекати."

        # --- R-011: мінімум незалежних підтверджень ----------------------- #
        if len(supporting) < self.min_confirmations:
            return None, (
                f"Недостатньо підтверджень ({len(supporting)} < "
                f"{self.min_confirmations}). Сигнал слабкий — краще чекати."
            )
        rules_fired.append("R-011")

        # --- режим ринку має не суперечити напряму ------------------------ #
        if direction == Direction.LONG and market.regime == MarketRegime.TRENDING_DOWN:
            opposing.append("Загальний режим ринку — низхідний")
        if direction == Direction.SHORT and market.regime == MarketRegime.TRENDING_UP:
            opposing.append("Загальний режим ринку — висхідний")

        # --- новинний фон (§11, §14) -------------------------------------- #
        if news is not None:
            if direction == Direction.LONG and news.is_strong_negative:
                return None, ("Сильна негативна новина суперечить лонгу. "
                              "Угода заблокована — краще чекати.")
            if direction == Direction.SHORT and news.is_strong_positive:
                return None, ("Сильна позитивна новина суперечить шорту. "
                              "Угода заблокована — краще чекати.")
            # новини як підтвердний або протилежний фактор
            if direction == Direction.LONG and news.is_strong_positive:
                supporting.append("Позитивний новинний фон")
                rules_fired.append("R-030")
            elif direction == Direction.SHORT and news.is_strong_negative:
                supporting.append("Негативний новинний фон")
                rules_fired.append("R-030")
            elif direction == Direction.LONG and news.score < -0.15:
                opposing.append("Новини радше негативні")
            elif direction == Direction.SHORT and news.score > 0.15:
                opposing.append("Новини радше позитивні")

        # --- розрахунок стопу/тейку від ATR ------------------------------- #
        atr_abs = market.price * (tech.atr_pct / 100.0)
        stop_dist = atr_abs * self.atr_stop_mult
        if direction == Direction.LONG:
            stop = market.price - stop_dist
            take = market.price + stop_dist * self.rr_target
        else:
            stop = market.price + stop_dist
            take = market.price - stop_dist * self.rr_target

        rules_fired.append("R-010")  # маємо явний R:R

        # --- впевненість за к-стю чистих підтверджень --------------------- #
        net = len(supporting) - len(opposing)
        if net >= 3:
            conf = Confidence.STRONG
        elif net == 2:
            conf = Confidence.MEDIUM
        elif net == 1:
            conf = Confidence.WEAK
        else:
            return None, "Фактори «проти» врівноважують «за». Краще чекати."

        idea = TradeIdea(
            asset=market.asset,
            direction=direction,
            time_horizon="swing 1–5 днів",
            entry_price=round(market.price, 8),
            stop_loss=round(stop, 8),
            take_profit=round(take, 8),
            why_now=f"{len(supporting)} підтверджень за {direction.value.upper()}",
            supporting_factors=supporting,
            opposing_factors=opposing,
            invalidation=f"Закриття за стопом {round(stop, 4)} ламає сценарій.",
            confidence=conf,
            rules_fired=rules_fired,
        )
        explanation = (
            f"Ідея {direction.value.upper()} по {market.asset}: "
            f"{', '.join(supporting)}. Впевненість: {conf.value}."
        )
        return idea, explanation

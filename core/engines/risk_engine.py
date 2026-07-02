"""
Risk Engine (§15) — найважливіший модуль системи.

Має право заблокувати БУДЬ-ЯКУ угоду. За замовчуванням — режим
Conservative Growth. Усі пороги налаштовуються, але дефолти консервативні.

Логіка навмисно проста й читабельна: кожна перевірка — окремий блок, який
або додає причину блокування, або пропускає далі. Якщо є хоч одна причина
блокування — угода не дозволена. Розмір позиції рахується від ризику, а не
навпаки (спершу «скільки я готовий втратити», потім «скільки одиниць»).
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from core.models.types import (
    AccountState,
    MarketSnapshot,
    RiskVerdict,
    TradeIdea,
)


class RiskConfig(BaseModel):
    """Налаштування ризику. Дефолти = Conservative Growth (§15)."""
    risk_per_trade_pct: float = 0.5            # ризик на угоду, % від капіталу
    max_daily_risk_pct: float = 1.0
    max_weekly_risk_pct: float = 3.0
    max_drawdown_pct: float = 8.0              # пауза при просадці
    min_risk_reward: float = 1.5
    max_open_positions: int = 5
    max_spread_pct: float = 0.5
    min_liquidity_score: float = 0.4
    max_volatility_atr_pct: float = 15.0       # фільтр екстремальної волатильності
    loss_streak_cooldown: int = 3              # к-сть збитків до cooldown
    allow_leverage: bool = False               # за замовч. без плеча


class RiskEngine:
    def __init__(self, config: RiskConfig | None = None):
        self.config = config or RiskConfig()

    # --------------------------------------------------------------------- #
    #  Головний метод: оцінити ідею.
    # --------------------------------------------------------------------- #
    def evaluate(
        self,
        idea: TradeIdea,
        market: MarketSnapshot,
        account: AccountState,
    ) -> RiskVerdict:
        reasons: list[str] = []
        warnings: list[str] = []
        c = self.config

        # --- 1. Структурна цілісність ідеї -------------------------------- #
        if idea.stop_loss == idea.entry_price:
            reasons.append("Немає стоп-лосу (стоп дорівнює входу).")
        elif not idea.stop_is_on_correct_side():
            reasons.append("Стоп-лос або тейк на неправильному боці від входу.")

        # --- 2. Якість даних (Core Safety CS-004) ------------------------- #
        if not market.data_is_reliable:
            issues = ", ".join(market.data_issues) or "невідома причина"
            reasons.append(f"Ненадійні ринкові дані: {issues}.")

        # --- 3. Ліквідність і спред (R-013) ------------------------------- #
        if market.liquidity_score < c.min_liquidity_score:
            reasons.append(
                f"Актив надто неліквідний (ліквідність {market.liquidity_score:.2f} "
                f"< {c.min_liquidity_score})."
            )
        if market.spread_pct > c.max_spread_pct:
            reasons.append(
                f"Завеликий спред ({market.spread_pct:.2f}% > {c.max_spread_pct}%)."
            )

        # --- 4. Волатильність --------------------------------------------- #
        if market.volatility_atr_pct > c.max_volatility_atr_pct:
            reasons.append(
                f"Екстремальна волатильність ({market.volatility_atr_pct:.1f}% ATR)."
            )

        # --- 5. Risk/Reward (R-010) --------------------------------------- #
        rr = idea.risk_reward
        if rr < c.min_risk_reward:
            reasons.append(
                f"Співвідношення ризик/прибуток замале ({rr:.2f} < {c.min_risk_reward})."
            )

        # --- 6. Лімітні стіни рахунку ------------------------------------- #
        if account.drawdown_pct >= c.max_drawdown_pct:
            reasons.append(
                f"Досягнуто ліміт просадки ({account.drawdown_pct:.1f}% "
                f"≥ {c.max_drawdown_pct}%) — нові угоди зупинено."
            )
        if account.daily_risk_used_pct >= c.max_daily_risk_pct:
            reasons.append("Досягнуто денний ліміт ризику.")
        if account.weekly_risk_used_pct >= c.max_weekly_risk_pct:
            reasons.append("Досягнуто тижневий ліміт ризику.")
        if account.open_positions >= c.max_open_positions:
            reasons.append(
                f"Забагато відкритих позицій ({account.open_positions} "
                f"≥ {c.max_open_positions})."
            )

        # --- 7. Дисципліна: cooldown після серії збитків (R-012, CS-003) -- #
        if account.in_cooldown:
            reasons.append("Активний cooldown після серії збитків.")
        elif account.consecutive_losses >= c.loss_streak_cooldown:
            reasons.append(
                f"Серія збитків ({account.consecutive_losses}) — потрібен cooldown."
            )

        # --- 8. Чи лишається місце в денному risk budget ------------------ #
        remaining_daily = c.max_daily_risk_pct - account.daily_risk_used_pct
        intended_risk_pct = min(c.risk_per_trade_pct, max(0.0, remaining_daily))
        if intended_risk_pct < c.risk_per_trade_pct and not reasons:
            warnings.append(
                "Розмір позиції зменшено, бо лишилось мало денного ризику."
            )

        # --- Якщо є причини — блокуємо без розрахунку розміру -------------- #
        if reasons:
            return RiskVerdict(
                approved=False,
                blocking_reasons=reasons,
                warnings=warnings,
                explanation_uk=self._explain_block(reasons),
            )

        # --- Розрахунок розміру позиції від ризику ------------------------- #
        risk_usd = account.equity * (intended_risk_pct / 100.0)
        risk_per_unit = idea.risk_per_unit
        units = risk_usd / risk_per_unit if risk_per_unit > 0 else 0.0
        position_value = units * idea.entry_price

        # без плеча позиція не може перевищувати капітал
        if not c.allow_leverage and position_value > account.equity:
            units = account.equity / idea.entry_price
            position_value = units * idea.entry_price
            risk_usd = units * risk_per_unit
            warnings.append("Позицію обмежено капіталом (плече вимкнено).")

        return RiskVerdict(
            approved=True,
            position_size_units=round(units, 8),
            position_value_usd=round(position_value, 2),
            risk_amount_usd=round(risk_usd, 2),
            risk_pct_of_equity=round(risk_usd / account.equity * 100.0, 3),
            warnings=warnings,
            explanation_uk=self._explain_approve(idea, risk_usd, units),
        )

    # --------------------------------------------------------------------- #
    #  Emergency stop (§15) — перевіряється окремо, поза оцінкою ідеї.
    # --------------------------------------------------------------------- #
    def emergency_stop_triggered(self, account: AccountState) -> tuple[bool, str]:
        c = self.config
        if account.drawdown_pct >= c.max_drawdown_pct:
            return True, f"Просадка {account.drawdown_pct:.1f}% досягла ліміту."
        if account.daily_risk_used_pct >= c.max_daily_risk_pct:
            return True, "Денний ліміт втрат вичерпано."
        if account.weekly_risk_used_pct >= c.max_weekly_risk_pct:
            return True, "Тижневий ліміт втрат вичерпано."
        return False, ""

    # --------------------------------------------------------------------- #
    #  Пояснення українською (§33)
    # --------------------------------------------------------------------- #
    @staticmethod
    def _explain_block(reasons: list[str]) -> str:
        head = "Угода заблокована Risk Engine. Причини:\n"
        return head + "\n".join(f"  • {r}" for r in reasons)

    @staticmethod
    def _explain_approve(idea: TradeIdea, risk_usd: float, units: float) -> str:
        return (
            f"Угода дозволена. {idea.direction.value.upper()} {idea.asset}.\n"
            f"  • Ризикуємо {risk_usd:.2f} USD ({units:.6f} од.)\n"
            f"  • Стоп {idea.stop_loss}, тейк {idea.take_profit}\n"
            f"  • Ризик/прибуток {idea.risk_reward:.2f}\n"
            f"  • Якщо ціна дійде до стопу — вийдемо з обмеженим збитком."
        )

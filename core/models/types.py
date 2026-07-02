"""
Доменні моделі ядра.

Усе, чим обмінюються модулі (Signal Engine, Risk Engine, Portfolio,
Journal), описано тут як суворі типи. Жоден модуль не передає сирі dict —
тільки ці структури. Це робить систему передбачуваною і легкою для тестів.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
#  Перелічення (enums)
# --------------------------------------------------------------------------- #
class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"


class Mode(str, Enum):
    """Режими роботи системи (§5 специфікації)."""
    ANALYSIS = "analysis"
    PAPER = "paper"
    LIVE = "live"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class MarketRegime(str, Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"
    UNKNOWN = "unknown"


class Confidence(str, Enum):
    """Якісна впевненість — навмисно не «гарантія» (§34)."""
    STRONG = "strong"          # Сильний сигнал
    MEDIUM = "medium"          # Середня впевненість
    WEAK = "weak"              # Слабкий сигнал
    INSUFFICIENT = "insufficient"  # Недостатньо підтверджень


# --------------------------------------------------------------------------- #
#  Ринкові дані
# --------------------------------------------------------------------------- #
class MarketSnapshot(BaseModel):
    """Стан ринку по одному активу в момент рішення."""
    asset: str
    price: float
    spread_pct: float = Field(0.0, description="Спред у відсотках від ціни")
    liquidity_score: float = Field(
        1.0, ge=0.0, le=1.0, description="0 = неліквідний, 1 = дуже ліквідний"
    )
    volatility_atr_pct: float = Field(
        0.0, description="ATR як % від ціни — міра волатильності"
    )
    regime: MarketRegime = MarketRegime.UNKNOWN
    data_is_reliable: bool = True
    data_issues: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=_utcnow)


# --------------------------------------------------------------------------- #
#  Рахунок і портфель
# --------------------------------------------------------------------------- #
class AccountState(BaseModel):
    """Поточний стан рахунку для розрахунку ризику."""
    equity: float = Field(..., description="Поточний капітал (USD)")
    peak_equity: float = Field(..., description="Історичний максимум капіталу")
    daily_risk_used_pct: float = Field(0.0, description="Скільки денного ризику вже використано")
    weekly_risk_used_pct: float = Field(0.0, description="Скільки тижневого ризику вже використано")
    open_positions: int = 0
    consecutive_losses: int = 0
    in_cooldown: bool = False

    @property
    def drawdown_pct(self) -> float:
        """Просадка — падіння від історичного максимуму, у %."""
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - self.equity) / self.peak_equity * 100.0)


# --------------------------------------------------------------------------- #
#  Торгова ідея (вихід Signal Engine, §14)
# --------------------------------------------------------------------------- #
class TradeIdea(BaseModel):
    """
    Структурована торгова ідея. Це НЕ просте «купити/продати».
    Кожне поле має бути заповнене, інакше Risk Engine відхилить ідею.
    """
    asset: str
    direction: Direction
    time_horizon: str                       # напр. "swing 1-5 днів"
    entry_price: float
    stop_loss: float
    take_profit: float
    why_now: str                            # коротка теза українською
    supporting_factors: list[str] = Field(default_factory=list)
    opposing_factors: list[str] = Field(default_factory=list)
    invalidation: str = ""                  # що ламає сценарій
    confidence: Confidence = Confidence.WEAK
    rules_fired: list[str] = Field(default_factory=list)  # id правил, що спрацювали

    # --- похідні метрики ---
    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry_price - self.stop_loss)

    @property
    def reward_per_unit(self) -> float:
        return abs(self.take_profit - self.entry_price)

    @property
    def risk_reward(self) -> float:
        """Співвідношення потенційного прибутку до ризику."""
        r = self.risk_per_unit
        return self.reward_per_unit / r if r > 0 else 0.0

    def stop_is_on_correct_side(self) -> bool:
        """Стоп має бути нижче входу для LONG і вище для SHORT."""
        if self.direction == Direction.LONG:
            return self.stop_loss < self.entry_price < self.take_profit
        return self.take_profit < self.entry_price < self.stop_loss


# --------------------------------------------------------------------------- #
#  Вердикт Risk Engine
# --------------------------------------------------------------------------- #
class RiskVerdict(BaseModel):
    """Результат перевірки ідеї Risk Engine."""
    approved: bool
    position_size_units: float = 0.0
    position_value_usd: float = 0.0
    risk_amount_usd: float = 0.0
    risk_pct_of_equity: float = 0.0
    blocking_reasons: list[str] = Field(default_factory=list)  # чому заблоковано
    warnings: list[str] = Field(default_factory=list)
    explanation_uk: str = ""               # просте пояснення українською

    @property
    def is_blocked(self) -> bool:
        return not self.approved

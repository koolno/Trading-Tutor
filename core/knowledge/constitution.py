"""
Trading Knowledge Constitution (§8, §9).

База знань — основа мислення системи ДО того, як вона накопичить власний
досвід. Тут НЕ копіюються тексти книг. Зберігаються лише узагальнені
практичні принципи у строгій структурі.

Кожне правило має статус. Core Safety Rules не можна вимикати автоматично.
"""
from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field


class RuleCategory(str, Enum):
    RISK_MANAGEMENT = "risk_management"
    POSITION_SIZING = "position_sizing"
    TECHNICAL = "technical_analysis"
    TREND_FOLLOWING = "trend_following"
    MEAN_REVERSION = "mean_reversion"
    BREAKOUT = "breakout_trading"
    VALUE = "value_investing"
    MOMENTUM = "momentum"
    SENTIMENT = "sentiment"
    MACRO = "macro"
    NEWS = "news_reaction"
    BEHAVIORAL = "behavioral_discipline"
    PORTFOLIO = "portfolio_management"
    LIQUIDITY = "liquidity"
    VOLATILITY = "volatility"
    DRAWDOWN = "drawdown_control"
    EXIT = "exit_rules"
    ENTRY = "entry_rules"
    AVOIDANCE = "trade_avoidance_rules"


class RuleStatus(str, Enum):
    """§9 — статуси правил за результатами власної статистики."""
    CORE_SAFETY = "core_safety"            # не можна вимкнути автоматично
    STRONG = "strong"
    USEFUL = "useful"
    CONTEXT_DEPENDENT = "context_dependent"
    UNCERTAIN = "uncertain"
    WEAK = "weak"
    DEPRECATED = "deprecated"
    DANGEROUS = "dangerous"


class RuleEffectiveness(BaseModel):
    """Статистика спрацювань правила (§9). Оновлюється Learning Engine."""
    times_activated: int = 0
    times_helped: int = 0
    times_blocked_profit: int = 0          # завадило прибутковій угоді
    times_prevented_loss: int = 0
    sample_too_small: bool = True

    @property
    def net_score(self) -> float:
        if self.times_activated == 0:
            return 0.0
        return (self.times_prevented_loss - self.times_blocked_profit) / self.times_activated


class Rule(BaseModel):
    """Повна структура правила за §8."""
    id: str
    name: str                              # назва українською
    category: RuleCategory
    knowledge_source: str                  # тип джерела (не цитата книги)
    description: str                       # короткий опис
    applies_when: str
    not_applies_when: str = ""
    confirmations_needed: list[str] = Field(default_factory=list)
    risks: str = ""
    prevents_mistake: str = ""
    example: str = ""
    failure_conditions: str = ""
    confidence_score: float = Field(0.7, ge=0.0, le=1.0)
    historical_effectiveness: RuleEffectiveness = Field(default_factory=RuleEffectiveness)
    live_effectiveness: RuleEffectiveness = Field(default_factory=RuleEffectiveness)
    status: RuleStatus = RuleStatus.USEFUL
    last_reviewed: date = Field(default_factory=date.today)

    @property
    def is_core_safety(self) -> bool:
        return self.status == RuleStatus.CORE_SAFETY


# --------------------------------------------------------------------------- #
#  Seed rules — стартова конституція.
#  Узагальнені принципи risk management і дисципліни, без копіювання текстів.
# --------------------------------------------------------------------------- #
def build_seed_constitution() -> list[Rule]:
    return [
        Rule(
            id="CS-001",
            name="Ніколи не торгувати без стоп-лосу",
            category=RuleCategory.RISK_MANAGEMENT,
            knowledge_source="загальна практика risk management",
            description="Кожна угода мусить мати наперед визначений рівень виходу зі збитком.",
            applies_when="завжди, перед відкриттям будь-якої позиції",
            confirmations_needed=["визначений stop_loss", "stop на правильному боці входу"],
            risks="без стопу один поганий рух може знищити депозит",
            prevents_mistake="необмежений збиток",
            example="LONG по 100, стоп 97 — максимальний збиток обмежений 3%.",
            failure_conditions="гепи через стоп при низькій ліквідності зменшують захист",
            confidence_score=0.99,
            status=RuleStatus.CORE_SAFETY,
        ),
        Rule(
            id="CS-002",
            name="Не ризикувати великою частиною капіталу в одній угоді",
            category=RuleCategory.POSITION_SIZING,
            knowledge_source="position sizing принципи",
            description="Ризик на одну угоду тримати дуже малим (за замовч. 0.25–0.5%).",
            applies_when="при розрахунку розміру позиції",
            risks="кілька збитків поспіль при великому ризику = глибока просадка",
            prevents_mistake="катастрофічна втрата від серії невдач",
            confidence_score=0.98,
            status=RuleStatus.CORE_SAFETY,
        ),
        Rule(
            id="CS-003",
            name="Заборона martingale та подвоєння після збитку",
            category=RuleCategory.BEHAVIORAL,
            knowledge_source="risk management / behavioral finance",
            description="Ніколи не збільшувати ставку, щоб «відігратися».",
            applies_when="після збиткової угоди або серії збитків",
            risks="експоненційне зростання ризику до повної втрати рахунку",
            prevents_mistake="злив депозиту в спробі відігратися",
            confidence_score=0.99,
            status=RuleStatus.CORE_SAFETY,
        ),
        Rule(
            id="CS-004",
            name="Не торгувати при ненадійних даних або помилках API",
            category=RuleCategory.RISK_MANAGEMENT,
            knowledge_source="trading systems engineering",
            description="Якщо дані сумнівні чи біржа віддає помилки — нові угоди заблоковані.",
            applies_when="при кожній перевірці перед входом",
            risks="рішення на основі хибних цін",
            prevents_mistake="вхід за фантомною ціною",
            confidence_score=0.97,
            status=RuleStatus.CORE_SAFETY,
        ),
        Rule(
            id="R-010",
            name="Вимагати мінімальне співвідношення ризик/прибуток",
            category=RuleCategory.ENTRY,
            knowledge_source="systematic trading",
            description="Не входити, якщо потенційний прибуток не виправдовує ризик (R:R ≥ поріг).",
            applies_when="при оцінці кожної ідеї",
            not_applies_when="спеціальні стратегії з високим win-rate і малим R:R (окремий режим)",
            confirmations_needed=["обчислений risk_reward", "R:R не нижче ліміту"],
            prevents_mistake="набір дрібних прибутків, які стирає один збиток",
            confidence_score=0.85,
            status=RuleStatus.STRONG,
        ),
        Rule(
            id="R-011",
            name="Кілька незалежних підтверджень для входу",
            category=RuleCategory.ENTRY,
            knowledge_source="technical + systematic trading",
            description="Один індикатор недостатній; потрібно ≥2 незалежних фактори.",
            applies_when="перед live-угодою",
            prevents_mistake="хибна впевненість від одного сигналу",
            confidence_score=0.8,
            status=RuleStatus.STRONG,
        ),
        Rule(
            id="R-012",
            name="Cooldown після серії збитків",
            category=RuleCategory.BEHAVIORAL,
            knowledge_source="trading psychology / discipline",
            description="Після N збитків поспіль — пауза, без нових угод певний час.",
            applies_when="після досягнення ліміту збитків поспіль",
            prevents_mistake="емоційна торгівля «на тильті»",
            confidence_score=0.82,
            status=RuleStatus.STRONG,
        ),
        Rule(
            id="R-013",
            name="Перевіряти ліквідність і спред",
            category=RuleCategory.LIQUIDITY,
            knowledge_source="market microstructure",
            description="Уникати неліквідних активів і завеликих спредів.",
            applies_when="при відборі активу",
            prevents_mistake="висока вартість входу/виходу, неможливість вийти за ціною",
            confidence_score=0.83,
            status=RuleStatus.STRONG,
        ),
        Rule(
            id="R-030",
            name="Враховувати новинний фон при вході",
            category=RuleCategory.NEWS,
            knowledge_source="news reaction / macro",
            description="Сильна новина у бік угоди підсилює сигнал; проти — блокує вхід.",
            applies_when="коли є свіжий новинний фон з надійних джерел",
            not_applies_when="джерела ненадійні або новини — шум",
            confirmations_needed=["надійне джерело", "достатня сила впливу"],
            prevents_mistake="вхід проти очевидної фундаментальної новини",
            confidence_score=0.75,
            status=RuleStatus.USEFUL,
        ),
        Rule(
            id="R-020",
            name="Більшість часу краще чекати, ніж торгувати",
            category=RuleCategory.AVOIDANCE,
            knowledge_source="trader interviews / discipline",
            description="Відсутність угоди — теж рішення. Без переваги — не торгувати.",
            applies_when="коли сигнали слабкі або суперечливі",
            prevents_mistake="угоди заради активності",
            confidence_score=0.8,
            status=RuleStatus.STRONG,
        ),
        Rule(
            id="R-021",
            name="Не входити лише тому, що ціна росте",
            category=RuleCategory.BEHAVIORAL,
            knowledge_source="behavioral finance",
            description="FOMO — погана причина для входу без власної логіки.",
            applies_when="під час сильних рухів і хайпу",
            prevents_mistake="купівля на вершині",
            confidence_score=0.8,
            status=RuleStatus.USEFUL,
        ),
    ]

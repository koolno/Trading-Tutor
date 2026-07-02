"""
Сховище даних (§37).

За замовчуванням SQLite (файл, без установки) — запускається одразу.
Для PostgreSQL достатньо задати змінну оточення DATABASE_URL, напр.:
    postgresql+psycopg://user:pass@localhost/broker
Решта коду не змінюється — використовується той самий інтерфейс.

Зберігаємо: угоди журналу, снапшоти статистики, стан правил Constitution,
спостереження Investment Memory, підсумки навчальних циклів.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, DateTime, Float, Integer, String, Text, create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session as OrmSession
from sqlalchemy.orm import mapped_column, sessionmaker


def _database_url() -> str:
    return os.getenv("DATABASE_URL", "sqlite:///broker.db")


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
#  Таблиці
# --------------------------------------------------------------------------- #
class TradeRecord(Base):
    """Кожна угода або відмова — рядок журналу (§28)."""
    __tablename__ = "trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_now)
    asset: Mapped[str] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(16))
    direction: Mapped[str] = mapped_column(String(8))
    decision: Mapped[str] = mapped_column(String(16))         # opened|closed|rejected
    reason: Mapped[str] = mapped_column(Text, default="")
    rules_fired: Mapped[list] = mapped_column(JSON, default=list)
    supporting: Mapped[list] = mapped_column(JSON, default=list)
    opposing: Mapped[list] = mapped_column(JSON, default=list)
    news_context: Mapped[list] = mapped_column(JSON, default=list)
    entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_reward: Mapped[float | None] = mapped_column(Float, nullable=True)
    position_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    result: Mapped[str | None] = mapped_column(String(12), nullable=True)
    lesson: Mapped[str] = mapped_column(Text, default="")


class RuleState(Base):
    """Стан правила Constitution зі статистикою (§9)."""
    __tablename__ = "rule_states"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(48))
    status: Mapped[str] = mapped_column(String(24))
    confidence_score: Mapped[float] = mapped_column(Float, default=0.7)
    times_activated: Mapped[int] = mapped_column(Integer, default=0)
    times_helped: Mapped[int] = mapped_column(Integer, default=0)
    times_blocked_profit: Mapped[int] = mapped_column(Integer, default=0)
    times_prevented_loss: Mapped[int] = mapped_column(Integer, default=0)
    last_reviewed: Mapped[datetime] = mapped_column(DateTime, default=_now)


class MemoryObservation(Base):
    """Спостереження Investment Memory (§19)."""
    __tablename__ = "memory"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_now)
    description: Mapped[str] = mapped_column(Text)
    asset_class: Mapped[str] = mapped_column(String(32), default="")
    market_regime: Mapped[str] = mapped_column(String(32), default="")
    confirmations: Mapped[int] = mapped_column(Integer, default=1)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    usefulness: Mapped[str] = mapped_column(String(32), default="uncertain")
    status: Mapped[str] = mapped_column(String(24), default="active")
    sources: Mapped[list] = mapped_column(JSON, default=list)


class CycleSummary(Base):
    """Підсумок навчального циклу після Stop (§21, §27)."""
    __tablename__ = "cycles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    ended_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    starting_equity: Mapped[float] = mapped_column(Float)
    ending_equity: Mapped[float] = mapped_column(Float)
    trades: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    profit_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    report_text: Mapped[str] = mapped_column(Text, default="")
    stop_loss_saves: Mapped[int] = mapped_column(Integer, default=0)
    rejected: Mapped[int] = mapped_column(Integer, default=0)


# --------------------------------------------------------------------------- #
#  Ініціалізація та доступ
# --------------------------------------------------------------------------- #
_engine = None
_SessionLocal = None


def init_db(url: str | None = None):
    """
    Створює engine і таблиці. Ідемпотентно, якщо url не задано явно: повторний
    виклик (напр. від FastAPI startup-події) не затирає вже ініціалізований
    engine — інакше тест чи виклик коду, що свідомо підключив окрему БД
    (наприклад, sqlite:///:memory:), втратив би своє підключення при
    повторному старті застосунку. Явний url завжди перепідключає.
    """
    global _engine, _SessionLocal
    if _engine is not None and url is None:
        return _engine
    _engine = create_engine(url or _database_url(), future=True)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, class_=OrmSession)
    Base.metadata.create_all(_engine)
    return _engine


def get_session() -> OrmSession:
    if _SessionLocal is None:
        init_db()
    return _SessionLocal()


def reset_db():
    """Тільки для тестів: чистить усі таблиці."""
    global _engine
    if _engine is None:
        init_db("sqlite:///:memory:")
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)

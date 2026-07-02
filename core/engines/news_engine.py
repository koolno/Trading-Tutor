"""
News Engine (§11).

Збирає й аналізує новини, оцінює важливість, настрій (позитив/негатив/шум),
силу впливу і довіру до джерела. Результат — NewsContext — впливає на
рішення Signal Engine: сильна негативна новина блокує лонг, і навпаки.

Джерела замінні (як і market data). Передбачено:
  • CryptoPanicProvider — реальні крипто-новини через API (потрібен ключ);
  • MockNewsProvider    — детерміновані новини для офлайн-тестів і демо.

Через відсутність інтернету в середовищі розробки реальний провайдер
перевіряється на моках; на твоєму комп'ютері він працюватиме з ключем.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class Sentiment(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    NOISE = "noise"


# рівень довіри до типу джерела (§11)
SOURCE_TRUST = {
    "regulator": 1.0, "central_bank": 1.0, "official": 0.95,
    "financial_report": 0.9, "major_media": 0.75, "exchange": 0.8,
    "social": 0.35, "rumor": 0.15, "unknown": 0.3,
}


@dataclass
class NewsItem:
    title: str
    source_type: str = "unknown"
    sentiment: Sentiment = Sentiment.NEUTRAL
    impact: float = 0.5            # 0..1 сила впливу
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def trust(self) -> float:
        return SOURCE_TRUST.get(self.source_type, 0.3)

    @property
    def weight(self) -> float:
        """Вага новини = довіра × сила впливу."""
        return self.trust * self.impact


@dataclass
class NewsContext:
    """Агрегований новинний фон по активу для Signal Engine."""
    asset: str
    score: float = 0.0            # -1 (дуже негативно) .. +1 (дуже позитивно)
    strength: float = 0.0        # 0..1 наскільки сильний сигнал загалом
    items: list[NewsItem] = field(default_factory=list)
    summary_uk: str = ""

    @property
    def is_strong_negative(self) -> bool:
        return self.score < -0.4 and self.strength > 0.5

    @property
    def is_strong_positive(self) -> bool:
        return self.score > 0.4 and self.strength > 0.5

    def as_factors(self) -> list[str]:
        """Перетворює фон на людські фактори для журналу/пояснень."""
        out = []
        if self.is_strong_positive:
            out.append("Позитивний новинний фон")
        elif self.is_strong_negative:
            out.append("Негативний новинний фон")
        elif abs(self.score) < 0.15:
            out.append("Новини нейтральні")
        return out


# --------------------------------------------------------------------------- #
#  Провайдери новин
# --------------------------------------------------------------------------- #
class NewsProvider(ABC):
    @abstractmethod
    def fetch(self, asset: str) -> list[NewsItem]:
        ...


class MockNewsProvider(NewsProvider):
    """Детерміновані новини для тестів/демо. Настрій залежить від seed активу."""
    def __init__(self, bias: dict[str, Sentiment] | None = None):
        self.bias = bias or {}

    def fetch(self, asset: str) -> list[NewsItem]:
        s = self.bias.get(asset, Sentiment.NEUTRAL)
        return [
            NewsItem(f"{asset}: офіційне оновлення мережі", "official", s, 0.6),
            NewsItem(f"{asset}: огляд великого медіа", "major_media", s, 0.4),
            NewsItem(f"{asset}: обговорення у соцмережах", "social", Sentiment.NOISE, 0.2),
        ]


class CryptoPanicProvider(NewsProvider):
    """
    Реальні крипто-новини через CryptoPanic API. Потрібен CRYPTOPANIC_TOKEN.
    Тут лише каркас запиту; парсинг настрою — спрощений за міткою джерела.
    На твоєму комп'ютері з ключем і мережею працюватиме; у середовищі без
    інтернету кине виняток, який обробляється рівнем вище (новини = нейтральні).
    """
    def __init__(self):
        self.token = os.getenv("CRYPTOPANIC_TOKEN", "")

    def fetch(self, asset: str) -> list[NewsItem]:
        if not self.token:
            raise RuntimeError("Немає CRYPTOPANIC_TOKEN — новини недоступні.")
        import urllib.request, json
        symbol = asset.split("/")[0]
        url = (f"https://cryptopanic.com/api/v1/posts/?auth_token={self.token}"
               f"&currencies={symbol}&public=true")
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read().decode())
        items: list[NewsItem] = []
        for post in data.get("results", [])[:15]:
            votes = post.get("votes", {})
            pos, neg = votes.get("positive", 0), votes.get("negative", 0)
            if pos > neg:
                sent = Sentiment.POSITIVE
            elif neg > pos:
                sent = Sentiment.NEGATIVE
            else:
                sent = Sentiment.NEUTRAL
            items.append(NewsItem(post.get("title", ""), "major_media", sent, 0.5))
        return items


# --------------------------------------------------------------------------- #
#  Двигун
# --------------------------------------------------------------------------- #
class NewsEngine:
    def __init__(self, provider: NewsProvider | None = None):
        self.provider = provider or MockNewsProvider()

    def analyze(self, asset: str) -> NewsContext:
        try:
            items = self.provider.fetch(asset)
        except Exception:
            # немає доступу до новин — повертаємо нейтральний фон, не блокуємо систему
            return NewsContext(asset=asset, score=0.0, strength=0.0,
                               summary_uk="Новини недоступні — фон нейтральний.")

        if not items:
            return NewsContext(asset=asset, summary_uk="Свіжих новин немає.")

        # зважена оцінка настрою
        total_w = 0.0
        signed = 0.0
        for it in items:
            if it.sentiment == Sentiment.NOISE:
                continue
            sign = {Sentiment.POSITIVE: 1, Sentiment.NEGATIVE: -1,
                    Sentiment.NEUTRAL: 0}[it.sentiment]
            signed += sign * it.weight
            total_w += it.weight
        score = (signed / total_w) if total_w > 0 else 0.0
        strength = min(1.0, total_w / len(items))

        ctx = NewsContext(asset=asset, score=round(score, 3),
                          strength=round(strength, 3), items=items)
        if ctx.is_strong_positive:
            ctx.summary_uk = "Сильний позитивний новинний фон."
        elif ctx.is_strong_negative:
            ctx.summary_uk = "Сильний негативний новинний фон — обережно з лонгами."
        else:
            ctx.summary_uk = "Новинний фон помірний/нейтральний."
        return ctx

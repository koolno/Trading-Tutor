"""
Market Data Module (§10).

Джерела даних замінні. Усі вони повертають однаковий формат — список свічок
OHLCV — тому решта системи не залежить від конкретної біржі.

Передбачено три провайдери:
  • CcxtProvider     — реальні біржі (Binance/Kraken/Bybit) через ccxt;
  • CsvProvider      — локальні CSV для тестів і офлайн-роботи;
  • SyntheticProvider — згенеровані дані для демо без інтернету.

Жоден провайдер не ухвалює торгових рішень. Він лише віддає свічки.
Перевіркою якості займається окремий DataQualityEngine.
"""
from __future__ import annotations

import csv
import math
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass
class Candle:
    """Одна свічка OHLCV."""
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class MarketDataProvider(ABC):
    """Спільний інтерфейс усіх джерел даних."""
    name: str = "base"

    @abstractmethod
    def fetch_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 200) -> list[Candle]:
        ...


# --------------------------------------------------------------------------- #
#  Реальна біржа через ccxt
# --------------------------------------------------------------------------- #
class CcxtProvider(MarketDataProvider):
    """
    Працює з будь-якою біржею, яку підтримує ccxt. Лише читання — ключі не
    потрібні для публічних свічок. Якщо мережа недоступна, кине виняток, який
    обробляється рівнем вище (тоді система не торгує — CS-004).
    """
    def __init__(self, exchange_id: str = "binance"):
        import ccxt  # локальний імпорт, щоб офлайн-тести не вимагали мережі
        self.name = exchange_id
        self.exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 200,
                     since_ms: int | None = None) -> list[Candle]:
        raw = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit, since=since_ms)
        out: list[Candle] = []
        for ts, o, h, l, c, v in raw:
            out.append(Candle(
                ts=datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                open=float(o), high=float(h), low=float(l),
                close=float(c), volume=float(v),
            ))
        return out


# --------------------------------------------------------------------------- #
#  Реальна історія за календарний рік (Paper-режим "Історія") — з CSV-кешем
# --------------------------------------------------------------------------- #
class HistoricalProvider(MarketDataProvider):
    """
    Справжні свічки Binance за обраний календарний рік — на відміну від
    SyntheticProvider (завжди-зростаючий синтетичний ряд), тут відтворюється
    реальна історія цін, лише прискорено. Перше завантаження року йде через
    ccxt і може тривати (Binance віддає щонайбільше 1000 свічок за запит,
    тому рік '1h'-свічок — це ~9 запитів поспіль з дотриманням rate-limit);
    результат кешується у CSV (data/), тому наступні запуски того самого
    року — миттєві, як CsvProvider.
    """
    def __init__(self, year: int, exchange_id: str = "binance",
                 cache_dir: str | Path | None = None):
        self.name = f"historical-{year}"
        self.year = year
        self.exchange_id = exchange_id
        self.cache_dir = Path(cache_dir) if cache_dir else (
            Path(__file__).resolve().parent.parent.parent / "data")

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100_000) -> list[Candle]:
        path = self._cache_path(symbol, timeframe)
        candles = self._read_csv(path) if path.exists() else self._fetch_year(symbol, timeframe)
        if not path.exists():
            self._write_csv(path, candles)
        return candles[-limit:]

    def _cache_path(self, symbol: str, timeframe: str) -> Path:
        safe = symbol.replace("/", "_")
        return self.cache_dir / f"{safe}_{timeframe}_{self.year}.csv"

    def _fetch_year(self, symbol: str, timeframe: str) -> list[Candle]:
        provider = CcxtProvider(self.exchange_id)
        start = datetime(self.year, 1, 1, tzinfo=timezone.utc)
        end = min(datetime(self.year + 1, 1, 1, tzinfo=timezone.utc), datetime.now(timezone.utc))
        since_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        out: list[Candle] = []
        while since_ms < end_ms:
            batch = provider.fetch_ohlcv(symbol, timeframe=timeframe, limit=1000, since_ms=since_ms)
            if not batch:
                break
            for c in batch:
                if c.ts.timestamp() * 1000 >= end_ms:
                    break
                out.append(c)
            last_ts_ms = int(batch[-1].ts.timestamp() * 1000)
            if last_ts_ms < since_ms:
                break  # захист від зациклення, якщо біржа не просувається далі
            since_ms = last_ts_ms + 1
        return out

    @staticmethod
    def _read_csv(path: Path) -> list[Candle]:
        out: list[Candle] = []
        with path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                out.append(Candle(
                    ts=datetime.fromisoformat(row["ts"]), open=float(row["open"]),
                    high=float(row["high"]), low=float(row["low"]),
                    close=float(row["close"]), volume=float(row["volume"]),
                ))
        return out

    @staticmethod
    def _write_csv(path: Path, candles: list[Candle]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["ts", "open", "high", "low", "close", "volume"])
            for c in candles:
                writer.writerow([c.ts.isoformat(), c.open, c.high, c.low, c.close, c.volume])


# --------------------------------------------------------------------------- #
#  CSV-провайдер
# --------------------------------------------------------------------------- #
class CsvProvider(MarketDataProvider):
    """Читає свічки з CSV: колонки ts,open,high,low,close,volume."""
    def __init__(self, directory: str):
        self.name = "csv"
        self.directory = Path(directory)

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 200) -> list[Candle]:
        safe = symbol.replace("/", "_")
        path = self.directory / f"{safe}_{timeframe}.csv"
        if not path.exists():
            raise FileNotFoundError(f"Немає файлу даних: {path}")
        out: list[Candle] = []
        with path.open() as f:
            for row in csv.DictReader(f):
                out.append(Candle(
                    ts=datetime.fromisoformat(row["ts"]),
                    open=float(row["open"]), high=float(row["high"]),
                    low=float(row["low"]), close=float(row["close"]),
                    volume=float(row["volume"]),
                ))
        return out[-limit:]


# --------------------------------------------------------------------------- #
#  Синтетичний провайдер (для демо без інтернету)
# --------------------------------------------------------------------------- #
class SyntheticProvider(MarketDataProvider):
    """
    Генерує реалістичні свічки геометричним блуканням з трендом і шумом.
    Детермінований за seed — зручно для відтворюваних тестів.
    """
    def __init__(self, seed: int = 42, start_price: float = 100.0,
                 drift: float = 0.0005, vol: float = 0.02):
        self.name = "synthetic"
        self.seed = seed
        self.start_price = start_price
        self.drift = drift
        self.vol = vol

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 200) -> list[Candle]:
        rng = random.Random(f"{self.seed}-{symbol}")
        price = self.start_price
        now = datetime.now(timezone.utc)
        out: list[Candle] = []
        for i in range(limit):
            ret = self.drift + rng.gauss(0, self.vol)
            new_price = max(0.01, price * math.exp(ret))
            o = price
            c = new_price
            hi = max(o, c) * (1 + abs(rng.gauss(0, self.vol / 2)))
            lo = min(o, c) * (1 - abs(rng.gauss(0, self.vol / 2)))
            vol = abs(rng.gauss(1000, 200))
            out.append(Candle(
                ts=now - timedelta(hours=(limit - i)),
                open=round(o, 4), high=round(hi, 4), low=round(lo, 4),
                close=round(c, 4), volume=round(vol, 2),
            ))
            price = new_price
        return out

"""
Завантаження реальних історичних свічок Binance у CSV (§10, PLAN A1).

Використовує CcxtProvider (core/data/providers.py) — лише читання публічних
даних, ключі не потрібні. Результат зберігається офлайн у data/, у форматі,
який розуміє CsvProvider (ts,open,high,low,close,volume), щоб решта системи
могла працювати на цих даних без мережі.

Торгову логіку цей скрипт не чіпає — лише завантажує й зберігає дані.

Приклад:
    python scripts/fetch_history.py --symbol BTC/USDT --timeframe 1h --limit 500
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from core.data.providers import Candle, MarketDataProvider

DEFAULT_OUTDIR = Path(__file__).resolve().parent.parent / "data"


def candles_to_csv(candles: list[Candle], path: Path) -> Path:
    """Записує свічки у CSV у форматі, сумісному з CsvProvider."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["ts", "open", "high", "low", "close", "volume"])
        for c in candles:
            writer.writerow([c.ts.isoformat(), c.open, c.high, c.low, c.close, c.volume])
    return path


def fetch_and_save(
    symbol: str,
    timeframe: str = "1h",
    limit: int = 200,
    outdir: Path | str = DEFAULT_OUTDIR,
    provider: MarketDataProvider | None = None,
) -> Path:
    """Тягне свічки через провайдер (за замовчуванням Binance через ccxt) і зберігає у CSV."""
    if provider is None:
        from core.data.providers import CcxtProvider
        provider = CcxtProvider("binance")
    candles = provider.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    safe = symbol.replace("/", "_")
    path = Path(outdir) / f"{safe}_{timeframe}.csv"
    return candles_to_csv(candles, path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Завантажити реальні історичні свічки у CSV")
    parser.add_argument("--symbol", default="BTC/USDT", help="Пара, напр. BTC/USDT")
    parser.add_argument("--timeframe", default="1h", help="Таймфрейм, напр. 1h, 4h, 1d")
    parser.add_argument("--limit", type=int, default=500, help="Кількість свічок")
    parser.add_argument("--exchange", default="binance", help="ID біржі для ccxt")
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR), help="Куди зберігати CSV")
    args = parser.parse_args()

    from core.data.providers import CcxtProvider
    provider = CcxtProvider(args.exchange)
    path = fetch_and_save(args.symbol, args.timeframe, args.limit, args.outdir, provider=provider)
    print(f"Збережено {args.limit} свічок {args.symbol} ({args.timeframe}) у {path}")


if __name__ == "__main__":
    main()

"""
Побудова чесного кейса на реальній історії Binance (§PLAN D1).

Тягне реальні свічки (CcxtProvider, як у scripts/fetch_history.py), проганяє
CaseBuilder (core/engines/case_builder.py, ту саму торгову логіку, що й
решта системи) і зберігає результат у data/cases/<symbol>.json — звідки
його читає екран кейсів (§D1) через /api/cases.

ВАЖЛИВО (чесність, як і в case_builder.py): період НЕ підбирається під
прибуток. За замовчуванням береться просто "останні N свічок, доступні
зараз" — нейтральний, невибірковий проміжок. Скрипт зберігає результат
навіть якщо він нульовий чи від'ємний.

Приклад:
    python scripts/build_case.py --symbol BTC/USDT --limit 2000
"""
from __future__ import annotations

import argparse
from pathlib import Path

from core.engines.case_builder import Case, CaseBuilder
from core.engines.risk_engine import RiskEngine
from core.engines.signal_engine import SignalEngine
from core.knowledge.constitution import build_seed_constitution

DEFAULT_CASES_DIR = Path(__file__).resolve().parent.parent / "data" / "cases"


def build_and_save(
    symbol: str,
    timeframe: str = "1h",
    limit: int = 2000,
    starting_equity: float = 500.0,
    outdir: Path | str = DEFAULT_CASES_DIR,
    provider=None,
) -> tuple[Case, Path]:
    """Тягне реальну історію, будує кейс і зберігає його як JSON."""
    if provider is None:
        from core.data.providers import CcxtProvider
        provider = CcxtProvider("binance")
    candles = provider.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

    builder = CaseBuilder(
        signal=SignalEngine(build_seed_constitution(), min_confirmations=2),
        risk=RiskEngine(),
    )
    case = builder.build(symbol, candles, starting_equity=starting_equity)

    safe = symbol.replace("/", "_")
    path = Path(outdir) / f"{safe}.json"
    case.save_json(path)
    return case, path


def main() -> None:
    parser = argparse.ArgumentParser(description="Побудувати чесний кейс на реальній історії")
    parser.add_argument("--symbol", default="BTC/USDT", help="Пара, напр. BTC/USDT")
    parser.add_argument("--timeframe", default="1h", help="Таймфрейм, напр. 1h, 4h, 1d")
    parser.add_argument("--limit", type=int, default=2000, help="Кількість свічок історії")
    parser.add_argument("--equity", type=float, default=500.0, help="Стартовий капітал USD")
    parser.add_argument("--exchange", default="binance", help="ID біржі для ccxt")
    parser.add_argument("--outdir", default=str(DEFAULT_CASES_DIR), help="Куди зберігати кейс")
    args = parser.parse_args()

    from core.data.providers import CcxtProvider
    provider = CcxtProvider(args.exchange)
    case, path = build_and_save(
        args.symbol, args.timeframe, args.limit, args.equity, args.outdir, provider=provider)
    print(case.summary_uk())
    print(f"\nЗбережено у {path}")


if __name__ == "__main__":
    main()

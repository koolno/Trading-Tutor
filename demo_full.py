"""
Повний цикл на синтетичних даних (офлайн).
Запуск: python -m demo_full
"""
from datetime import datetime, timezone

from core.data.providers import SyntheticProvider
from core.data.quality import DataQualityEngine
from core.engines.journal import Journal, JournalEntry
from core.engines.learning import build_stop_report, compute_stats
from core.engines.paper_trading import PaperBroker, PaperTradingEngine
from core.engines.risk_engine import RiskConfig, RiskEngine
from core.engines.signal_engine import SignalEngine
from core.engines.technical import TechnicalAnalysis
from core.knowledge.constitution import build_seed_constitution


def _journal_close(journal, pos, pnl, result, exit_price):
    journal.add(JournalEntry(
        ts=datetime.now(timezone.utc).isoformat(),
        asset=pos.asset, mode="paper", direction=pos.direction.value,
        decision="closed", reason="стоп/тейк",
        rules_fired=pos.rules_fired, supporting=pos.supporting,
        entry=pos.entry, stop_loss=pos.stop_loss, take_profit=pos.take_profit,
        exit=exit_price, position_size=pos.size, pnl_usd=pnl, result=result,
        lesson="перемога — сетап спрацював" if result == "win"
               else "збиток — переглянути фактори входу",
    ))


def main() -> None:
    rules = build_seed_constitution()
    signal = SignalEngine(rules, min_confirmations=2)
    risk = RiskEngine(RiskConfig(min_risk_reward=1.5))
    broker = PaperBroker(starting_equity=500.0)
    journal = Journal()
    engine = PaperTradingEngine(signal, risk, broker, journal)
    ta = TechnicalAnalysis()
    dq = DataQualityEngine()

    assets = {
        "BTC/USDT": SyntheticProvider(seed=1, start_price=60000, drift=0.004, vol=0.012),
        "ETH/USDT": SyntheticProvider(seed=7, start_price=3000, drift=0.003, vol=0.015),
        "SOL/USDT": SyntheticProvider(seed=3, start_price=150, drift=-0.004, vol=0.018),
    }
    starting = broker.equity
    print(f"Старт: {starting} USD | активів: {len(assets)}\n")
    opened = 0

    for symbol, provider in assets.items():
        candles = provider.fetch_ohlcv(symbol, "1h", limit=200)
        for end in range(60, len(candles)):
            window = candles[:end]
            current = window[-1]
            for pos, pnl, result in broker.update_candle(symbol, current.high, current.low):
                _journal_close(journal, pos, pnl, result, current.close)
            report = dq.check(window, "1h", check_staleness=False)
            factors, snapshot = ta.analyze(symbol, window, report.reliable, report.issues)
            msg = engine.step(snapshot, factors, update_positions=False)
            if msg.startswith("✅"):
                opened += 1

    # закрити залишки за останньою ціною
    for symbol, provider in assets.items():
        last = provider.fetch_ohlcv(symbol, "1h", 200)[-1].close
        for pos, pnl, result in broker.update(symbol, last):
            _journal_close(journal, pos, pnl, result, last)

    closed = journal.closed_trades()
    rejected = len([e for e in journal.entries if e.decision == "rejected"])
    print(f"Відкрито угод: {opened} | закрито: {len(closed)} | відмов: {rejected}\n")
    print(build_stop_report(closed, starting, broker.equity))


if __name__ == "__main__":
    main()

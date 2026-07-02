"""
Backtesting Engine (§22) + Live-гейт.

Проганяє стратегію на історичних свічках і рахує метрики:
total return, win rate, max drawdown, Sharpe, Sortino, profit factor,
expectancy, кількість угод. Уникаємо overfitting: жодного підбору під
історію — та сама логіка, що й у live.

Live-гейт: перед реальними грошима стратегія має пройти мінімальні пороги
(додатний expectancy, profit factor > 1, достатня кількість угод, помірна
просадка). Це реалізує вимогу §22 «перед live використанням — backtest».
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from core.data.providers import Candle
from core.engines.paper_trading import PaperBroker, PaperTradingEngine
from core.engines.risk_engine import RiskEngine
from core.engines.signal_engine import SignalEngine
from core.engines.technical import TechnicalAnalysis
from core.data.quality import DataQualityEngine
from core.engines.journal import Journal
from core.engines.learning import compute_stats


@dataclass
class BacktestResult:
    trades: int = 0
    win_rate: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    profit_factor: float | None = None
    expectancy: float = 0.0
    passed_gate: bool = False
    gate_reasons: list[str] = field(default_factory=list)

    def summary_uk(self) -> str:
        pf = "∞" if self.profit_factor is None else f"{self.profit_factor:.2f}"
        verdict = "✅ ПРОЙДЕНО" if self.passed_gate else "❌ НЕ ПРОЙДЕНО"
        lines = [
            "── Backtest ──",
            f"Угод: {self.trades} | Win rate: {self.win_rate:.1f}%",
            f"Дохідність: {self.total_return_pct:+.2f}% | Просадка: {self.max_drawdown_pct:.1f}%",
            f"Sharpe: {self.sharpe:.2f} | Sortino: {self.sortino:.2f} | Profit factor: {pf}",
            f"Expectancy: {self.expectancy:.3f} USD/угоду",
            f"Гейт для Live: {verdict}",
        ]
        if not self.passed_gate:
            lines += [f"  • {r}" for r in self.gate_reasons]
        return "\n".join(lines)


class Backtester:
    def __init__(self, signal: SignalEngine, risk: RiskEngine):
        self.signal = signal
        self.risk = risk
        self.ta = TechnicalAnalysis()
        self.dq = DataQualityEngine()

    def run(self, series_by_asset: dict[str, list[Candle]],
            starting_equity: float = 500.0,
            min_trades: int = 20) -> BacktestResult:
        broker = PaperBroker(starting_equity=starting_equity)
        journal = Journal()
        engine = PaperTradingEngine(self.signal, self.risk, broker, journal)

        equity_curve = [starting_equity]
        length = min(len(s) for s in series_by_asset.values())
        for end in range(60, length):
            for asset, series in series_by_asset.items():
                window = series[: end + 1]
                current = window[-1]
                for pos, pnl, result in broker.update_candle(
                        asset, current.high, current.low):
                    engine.journal.add_close(pos, pnl, result, current.close,
                                             mode="backtest")
                report = self.dq.check(window, "1h", check_staleness=False)
                factors, snapshot = self.ta.analyze(
                    asset, window, report.reliable, report.issues)
                engine.step(snapshot, factors, update_positions=False)
            equity_curve.append(broker.equity)

        # закрити залишки
        for asset, series in series_by_asset.items():
            last = series[min(length - 1, len(series) - 1)].close
            for pos, pnl, result in broker.update(asset, last):
                engine.journal.add_close(pos, pnl, result, last, mode="backtest")
        equity_curve.append(broker.equity)

        return self._metrics(journal, equity_curve, starting_equity, min_trades)

    def _metrics(self, journal, equity_curve, starting, min_trades) -> BacktestResult:
        closed = journal.closed_trades()
        stats = compute_stats(closed)
        res = BacktestResult(
            trades=stats.trades,
            win_rate=round(stats.win_rate, 1),
            total_return_pct=round((equity_curve[-1] / starting - 1) * 100, 2),
            expectancy=round(stats.expectancy, 3),
            profit_factor=None if stats.profit_factor == float("inf")
            else round(stats.profit_factor, 2),
        )
        # просадка з кривої капіталу
        peak = equity_curve[0]
        max_dd = 0.0
        for v in equity_curve:
            peak = max(peak, v)
            max_dd = max(max_dd, (peak - v) / peak * 100 if peak > 0 else 0)
        res.max_drawdown_pct = round(max_dd, 2)
        # Sharpe/Sortino з поперіодних дохідностей
        rets = [(equity_curve[i] / equity_curve[i - 1] - 1)
                for i in range(1, len(equity_curve)) if equity_curve[i - 1] > 0]
        if len(rets) > 1:
            mean = sum(rets) / len(rets)
            std = math.sqrt(sum((r - mean) ** 2 for r in rets) / len(rets))
            downside = [r for r in rets if r < 0]
            dstd = math.sqrt(sum(r ** 2 for r in downside) / len(downside)) if downside else 0.0
            ann = math.sqrt(24 * 365)  # годинні свічки → річна нормалізація
            res.sharpe = round((mean / std * ann) if std > 0 else 0.0, 2)
            res.sortino = round((mean / dstd * ann) if dstd > 0 else 0.0, 2)

        # --- Live-гейт ---
        reasons = []
        if stats.trades < min_trades:
            reasons.append(f"замало угод ({stats.trades} < {min_trades})")
        if stats.expectancy <= 0:
            reasons.append("expectancy не додатний")
        if res.profit_factor is not None and res.profit_factor < 1.0:
            reasons.append("profit factor < 1")
        if res.max_drawdown_pct > 25:
            reasons.append(f"завелика просадка ({res.max_drawdown_pct}%)")
        res.gate_reasons = reasons
        res.passed_gate = len(reasons) == 0
        return res

"""
Strategy Optimizer — параметри для "Оптимізована по історії" (навмисна,
чесна демонстрація overfitting, §PLAN).

Підбирає параметри SignalEngine (пороги RSI, множник ATR-стопу, ціль R:R,
кількість підтверджень), які максимізують ПРИБУТОК на реальній історії
2021-2025 — а не пороги з підручника, як у "Класична" стратегія. Це
справжній, реальний grid search: результат дійсно найкращий із перевірених
комбінацій на цьому вікні, і саме тому він, найімовірніше, програє на нових
даних, яких підбір не бачив (суть overfitting — не підробка для ефекту).

Продуктивність: TechnicalAnalysis рахує індикатори за O(розмір вікна), а
Backtester.run() передає ЩОРАЗУ ввесь ряд від початку — на 5 роках 1h-свічок
(~43800) один прогін уже займає хвилини, а grid із десятків комбінацій —
години. Тут навмисно: (1) дані стиснуті до денних свічок (§_aggregate_daily,
~1826 замість ~43800), (2) вікно TA-аналізу обмежене (_LOOKBACK_CANDLES) —
разом це відрізняє підбір від "чесного" Backtester.run(), але для мети
"підібрати пороги стратегії" цього достатньо, а рахується він секунди,
не години.
"""
from __future__ import annotations

import itertools
import json
from dataclasses import asdict, dataclass
from datetime import datetime, time as dtime, timezone
from pathlib import Path

from core.data.providers import Candle, HistoricalProvider
from core.data.quality import DataQualityEngine
from core.engines.journal import Journal
from core.engines.learning import compute_stats
from core.engines.paper_trading import PaperBroker, PaperTradingEngine
from core.engines.risk_engine import RiskConfig, RiskEngine
from core.engines.signal_engine import SignalEngine
from core.engines.technical import TechnicalAnalysis
from core.knowledge.constitution import build_seed_constitution

FIT_YEARS: tuple[int, ...] = (2021, 2022, 2023, 2024, 2025)
FIT_ASSET = "BTC/USDT"

_CACHE_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "strategy_cache"
    / "optimized_params.json"
)

# Невеликий, але СПРАВЖНІЙ grid — обмежений навмисно, щоб підбір займав
# секунди, а не години (див. пояснення продуктивності вище).
_GRID_MIN_CONFIRMATIONS = (1, 2)
_GRID_ATR_STOP_MULT = (1.0, 1.5, 2.0)
_GRID_RR_TARGET = (1.5, 2.0, 2.5, 3.0)
_GRID_RSI_OVERSOLD = (20.0, 25.0, 30.0)
_GRID_RSI_OVERBOUGHT = (70.0, 75.0, 80.0)
_MIN_TRADES_TO_QUALIFY = 5  # менше — не показова "оптимізація", а випадковість

_LOOKBACK_CANDLES = 120  # обмежене ковзне вікно для TA (див. docstring вище)


@dataclass
class OptimizedParams:
    """Підібрані параметри + чесна статистика ЦЬОГО Ж підбору на 2021-2025."""
    min_confirmations: int
    atr_stop_mult: float
    rr_target: float
    rsi_oversold: float
    rsi_overbought: float
    fit_total_return_pct: float
    fit_trades: int
    fit_win_rate: float
    fit_years: str

    def to_signal_engine(self) -> SignalEngine:
        return SignalEngine(
            build_seed_constitution(), self.min_confirmations,
            atr_stop_mult=self.atr_stop_mult, rr_target=self.rr_target,
            rsi_oversold=self.rsi_oversold, rsi_overbought=self.rsi_overbought,
        )


def _aggregate_daily(candles: list[Candle]) -> list[Candle]:
    """Стискає годинні свічки в денні (§ пояснення продуктивності вгорі)."""
    days: dict = {}
    order: list = []
    for c in candles:
        d = c.ts.date()
        if d not in days:
            days[d] = []
            order.append(d)
        days[d].append(c)
    out = []
    for d in order:
        g = days[d]
        out.append(Candle(
            ts=datetime.combine(d, dtime.min, tzinfo=timezone.utc),
            open=g[0].open, high=max(x.high for x in g),
            low=min(x.low for x in g), close=g[-1].close,
            volume=sum(x.volume for x in g),
        ))
    return out


def _fetch_fit_series(years: tuple[int, ...] = FIT_YEARS,
                       asset: str = FIT_ASSET) -> list[Candle]:
    all_candles: list[Candle] = []
    for y in years:
        all_candles += HistoricalProvider(year=y).fetch_ohlcv(asset, "1h", limit=100_000)
    return _aggregate_daily(all_candles)


def _quick_backtest(signal: SignalEngine, risk: RiskEngine, series: list[Candle],
                     asset: str = FIT_ASSET, starting_equity: float = 500.0,
                     warmup: int = 60) -> tuple[float, int, float]:
    """Полегшений прогін для grid search: обмежене ковзне вікно замість
    повного ряду (§ пояснення продуктивності на початку файлу)."""
    broker = PaperBroker(starting_equity=starting_equity)
    journal = Journal()
    engine = PaperTradingEngine(signal, risk, broker, journal)
    ta = TechnicalAnalysis()
    dq = DataQualityEngine()

    for end in range(warmup, len(series)):
        window = series[max(0, end - _LOOKBACK_CANDLES):end + 1]
        current = window[-1]
        for pos, pnl, result in broker.update_candle(asset, current.high, current.low):
            journal.add_close(pos, pnl, result, current.close, mode="fit", ts=current.ts)
        report = dq.check(window, "1d", check_staleness=False)
        factors, snapshot = ta.analyze(asset, window, report.reliable, report.issues)
        engine.step(snapshot, factors, update_positions=False, as_of=current.ts)

    last_candle = series[-1]
    for pos, pnl, result in broker.update(asset, last_candle.close):
        journal.add_close(pos, pnl, result, last_candle.close, mode="fit", ts=last_candle.ts)

    stats = compute_stats(journal.closed_trades())
    total_return_pct = (broker.equity / starting_equity - 1) * 100
    return total_return_pct, stats.trades, stats.win_rate


def _permissive_risk() -> RiskEngine:
    """Дуже м'який RiskConfig для підбору — перевіряємо ЛИШЕ якість сигналу
    (пороги RSI/ATR/R:R), а не намагаємось обійти захист рахунку."""
    return RiskEngine(RiskConfig(
        min_risk_reward=1.0, max_drawdown_pct=100.0, max_daily_risk_pct=100.0,
        max_weekly_risk_pct=100.0, loss_streak_cooldown=10_000,
    ))


def _grid_search(series: list[Candle]) -> OptimizedParams:
    best = None
    for minc, atrm, rr, rsl, rsh in itertools.product(
            _GRID_MIN_CONFIRMATIONS, _GRID_ATR_STOP_MULT, _GRID_RR_TARGET,
            _GRID_RSI_OVERSOLD, _GRID_RSI_OVERBOUGHT):
        signal = SignalEngine(build_seed_constitution(), minc, atr_stop_mult=atrm,
                              rr_target=rr, rsi_oversold=rsl, rsi_overbought=rsh)
        ret, trades, win_rate = _quick_backtest(signal, _permissive_risk(), series)
        if trades < _MIN_TRADES_TO_QUALIFY:
            continue
        if best is None or ret > best[0]:
            best = (ret, trades, win_rate, (minc, atrm, rr, rsl, rsh))

    if best is None:
        # запасний варіант: найм'якші пороги з grid, навіть якщо мало угод
        params = (1, 1.0, 1.5, 30.0, 70.0)
        signal = SignalEngine(build_seed_constitution(), params[0], atr_stop_mult=params[1],
                              rr_target=params[2], rsi_oversold=params[3], rsi_overbought=params[4])
        ret, trades, win_rate = _quick_backtest(signal, _permissive_risk(), series)
        best = (ret, trades, win_rate, params)

    ret, trades, win_rate, (minc, atrm, rr, rsl, rsh) = best
    return OptimizedParams(
        min_confirmations=minc, atr_stop_mult=atrm, rr_target=rr,
        rsi_oversold=rsl, rsi_overbought=rsh,
        fit_total_return_pct=round(ret, 1), fit_trades=trades,
        fit_win_rate=round(win_rate, 1),
        fit_years=f"{FIT_YEARS[0]}-{FIT_YEARS[-1]}",
    )


def fit_optimized_params(force: bool = False) -> OptimizedParams:
    """Повертає підібрані параметри, кешовані у JSON — підбір рахується один
    раз (перший запуск стратегії «Оптимізована»), далі — миттєво з кешу."""
    if not force and _CACHE_PATH.exists():
        return OptimizedParams(**json.loads(_CACHE_PATH.read_text(encoding="utf-8")))

    series = _fetch_fit_series()
    params = _grid_search(series)

    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(
        json.dumps(asdict(params), ensure_ascii=False, indent=2), encoding="utf-8")
    return params

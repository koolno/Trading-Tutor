"""
Session Manager — стан запущеної стратегії (Start/Stop flow, §4).

Тримає поточну сесію: режим, рахунок, відкриті позиції, журнал, статистику.
Один «тік» = обробка нової порції даних по watchlist. Для демо/MVP дані
беруться з SyntheticProvider (офлайн), але провайдер замінний на CcxtProvider.

Це шар оркестрації — він не містить торгової логіки, лише викликає двигуни.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.data.providers import MarketDataProvider, SyntheticProvider
from core.data.quality import DataQualityEngine
from core.engines.journal import Journal, JournalEntry
from core.engines.learning import build_stop_report, compute_stats
from core.engines.paper_trading import PaperBroker, PaperTradingEngine
from core.engines.risk_engine import RiskConfig, RiskEngine
from core.engines.signal_engine import SignalEngine
from core.engines.technical import TechnicalAnalysis
from core.engines.news_engine import NewsEngine, MockNewsProvider, NewsProvider
from core.engines.investment_memory import InvestmentMemory, Observation
from core.engines.live_adapter import LiveTradingAdapter
from core.engines.narration import narrate_emergency_stop_uk, narrate_entry_uk, narrate_wait_uk
from core.engines.understanding import build_understanding_summary
from core.knowledge.constitution import build_seed_constitution
from core.models.types import Mode


@dataclass
class SessionConfig:
    amount_usd: float = 500.0
    risk_level: str = "conservative"      # demo | conservative | moderate
    mode: Mode = Mode.PAPER
    assets: list[str] = field(default_factory=lambda: ["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    cycle_months: int = 2
    live_enabled: bool = False            # реальні гроші (за замовч. вимкнено)
    live_confirmed: bool = False          # користувач явно підтвердив live

    def to_risk_config(self) -> RiskConfig:
        if self.risk_level == "demo":
            # М'якші правила ЛИШЕ для демонстрації на синтетичних даних,
            # щоб одразу було видно угоди й графіки. НЕ для реальних грошей.
            return RiskConfig(risk_per_trade_pct=1.0, max_daily_risk_pct=3.0,
                              max_weekly_risk_pct=8.0, max_drawdown_pct=20.0,
                              min_risk_reward=1.2, loss_streak_cooldown=6)
        if self.risk_level == "moderate":
            return RiskConfig(risk_per_trade_pct=0.75, max_daily_risk_pct=1.5,
                              max_drawdown_pct=10.0, min_risk_reward=1.5)
        return RiskConfig()  # conservative defaults

    @property
    def is_demo(self) -> bool:
        return self.risk_level == "demo"


class Session:
    def __init__(self, config: SessionConfig, provider: MarketDataProvider | None = None):
        self.config = config
        self.rules = build_seed_constitution()
        # у демо-режимі достатньо 1 підтвердження і трендові дані — щоб одразу
        # було видно роботу; у звичайних режимах — суворіше (2 підтвердження)
        min_conf = 1 if config.is_demo else 2
        self.signal = SignalEngine(self.rules, min_confirmations=min_conf)
        self.risk = RiskEngine(config.to_risk_config())
        self.broker = PaperBroker(starting_equity=config.amount_usd)
        self.journal = Journal()
        # новини, пам'ять, live-адаптер
        self.news = NewsEngine(MockNewsProvider())   # замінюється на реальний у live
        self.memory = InvestmentMemory()
        self.live = LiveTradingAdapter(
            enabled=config.live_enabled and config.live_confirmed,
            dry_run=not (config.live_enabled and config.live_confirmed),
        )
        self._news_cache: dict = {}
        self.engine = PaperTradingEngine(self.signal, self.risk, self.broker, self.journal)
        self.ta = TechnicalAnalysis()
        self.dq = DataQualityEngine()
        self.starting_equity = config.amount_usd

        # Готуємо дані наперед. Для офлайн-демо генеруємо довгу синтетичну
        # історію на кожен актив один раз; курсор іде вперед по ній.
        # У live-режимі сюди підставляється CcxtProvider (свічки з біржі).
        self._series: dict[str, list] = {}
        self.providers: dict[str, MarketDataProvider] = {}
        # у демо — виразніші тренди й волатильність, щоб швидко з'явилися угоди
        if config.is_demo:
            seeds = {"BTC/USDT": (1, 60000, 0.004), "ETH/USDT": (7, 3000, 0.0035),
                     "SOL/USDT": (3, 150, 0.005)}
            demo_vol = 0.013
        else:
            seeds = {"BTC/USDT": (1, 60000, 0.0015), "ETH/USDT": (7, 3000, 0.0012),
                     "SOL/USDT": (3, 150, 0.001)}
            demo_vol = 0.015
        for a in config.assets:
            default_drift = 0.003 if config.is_demo else 0.001
            seed, price, drift = seeds.get(a, (abs(hash(a)) % 1000, 100, default_drift))
            p = provider or SyntheticProvider(
                seed=seed, start_price=price, drift=drift, vol=demo_vol)
            self.providers[a] = p
            # 1000 свічок наперед — вистачає на тривалий безперервний цикл
            self._series[a] = p.fetch_ohlcv(a, "1h", limit=1000)

        self.running = False
        self.paused = False
        import uuid
        self.session_id = uuid.uuid4().hex[:16]
        self.last_action = "Сесія створена. Натисніть «Почати стратегію»."
        self.equity_curve: list[float] = [config.amount_usd]
        self._cursor = 60  # позиція у підготовленій історії
        self._max_curve = 300  # обмеження довжини кривої для дашборду
        self._lock = threading.Lock()

    # --------------------------------------------------------------------- #
    #  Один тік стратегії
    # --------------------------------------------------------------------- #
    def tick(self) -> None:
        if not self.running or self.paused:
            return
        with self._lock:
            for asset in self.providers:
                series = self._series[asset]
                if self._cursor >= len(series):
                    continue
                window = series[: self._cursor + 1]
                if len(window) < 60:
                    continue
                current = window[-1]
                # закрити позиції за діапазоном поточної свічки
                close_narrations = []
                for pos, pnl, result in self.broker.update_candle(
                        asset, current.high, current.low):
                    self._journal_close(pos, pnl, result, current.close)
                    close_narrations.append(narrate_entry_uk(self.journal.entries[-1]))
                report = self.dq.check(window, "1h", check_staleness=False)
                factors, snapshot = self.ta.analyze(
                    asset, window, report.reliable, report.issues)
                # новини оновлюємо періодично (кожні 24 тіки ≈ добу), кешуємо
                if asset not in self._news_cache or self._cursor % 24 == 0:
                    self._news_cache[asset] = self.news.analyze(asset)
                news_ctx = self._news_cache[asset]
                before = len(self.journal.entries)
                msg = self.engine.step(snapshot, factors,
                                       update_positions=False, news=news_ctx)
                if len(self.journal.entries) > before:
                    step_narration = narrate_entry_uk(self.journal.entries[-1])
                elif msg.startswith("⏳"):
                    step_narration = narrate_wait_uk(asset, msg)
                elif msg.startswith("⛔"):
                    step_narration = narrate_emergency_stop_uk(
                        asset, msg.split(":", 1)[-1])
                else:
                    step_narration = msg.splitlines()[0]
                # подія закриття важливіша за цей тік — показуємо саме її, якщо була
                self.last_action = close_narrations[-1] if close_narrations else step_narration
            self.equity_curve.append(round(self.broker.equity, 2))
            if len(self.equity_curve) > self._max_curve:
                self.equity_curve = self.equity_curve[-self._max_curve:]
            self._cursor += 1
            # коли історія вичерпана — завершуємо цикл і пропонуємо звіт
            if self._cursor >= len(next(iter(self._series.values()))):
                self.running = False
                self.last_action = (
                    "Навчальний цикл завершено. Натисніть «Зупинити і "
                    "проаналізувати» для звіту.")

    def _journal_close(self, pos, pnl, result, exit_price):
        self.journal.add(JournalEntry(
            ts=datetime.now(timezone.utc).isoformat(),
            asset=pos.asset, mode=self.config.mode.value, direction=pos.direction.value,
            decision="closed", reason="стоп/тейк", rules_fired=pos.rules_fired,
            supporting=pos.supporting, entry=pos.entry, stop_loss=pos.stop_loss,
            take_profit=pos.take_profit, exit=exit_price, position_size=pos.size,
            pnl_usd=pnl, result=result,
            lesson="перемога — сетап спрацював" if result == "win"
                   else "збиток — переглянути фактори входу",
        ))

    # --------------------------------------------------------------------- #
    #  Команди керування
    # --------------------------------------------------------------------- #
    def start(self):
        self.running = True
        self.paused = False
        self.last_action = "Стратегію запущено. Система сканує ринок."

    def pause(self):
        self.paused = True
        self.last_action = "Паузу активовано. Нові угоди призупинені."

    def resume(self):
        self.paused = False
        self.last_action = "Роботу відновлено."

    def close_all(self):
        for asset in self.providers:
            series = self._series[asset]
            idx = min(self._cursor, len(series) - 1)
            last = series[idx].close
            for pos, pnl, result in self.broker.update(asset, last):
                self._journal_close(pos, pnl, result, last)
        self.last_action = "Усі позиції закрито."

    def stop_and_review(self) -> str:
        self.running = False
        self.close_all()
        self.last_action = "Стратегію зупинено. Звіт сформовано."
        report = build_stop_report(
            self.journal.closed_trades(), self.starting_equity, self.broker.equity)
        self._persist_cycle(report)
        return report

    def understanding_summary(self) -> list[str]:
        """Прості підсумки розуміння (§PLAN C3) — не бали, не рівні."""
        return build_understanding_summary(self.journal.entries).insights_uk

    def _persist_cycle(self, report: str):
        """Зберігає підсумок циклу і угоди в БД (§37)."""
        try:
            from core.storage.db import get_session as db_session, CycleSummary, TradeRecord
            stats = compute_stats(self.journal.closed_trades())
            pf = None if stats.profit_factor == float("inf") else stats.profit_factor
            rejected = len([e for e in self.journal.entries if e.decision == "rejected"])
            s = db_session()
            try:
                s.add(CycleSummary(
                    session_id=self.session_id, starting_equity=self.starting_equity,
                    ending_equity=self.broker.equity, trades=stats.trades,
                    win_rate=stats.win_rate, profit_factor=pf, report_text=report,
                    stop_loss_saves=stats.losses, rejected=rejected,
                ))
                for e in self.journal.entries:
                    s.add(TradeRecord(
                        session_id=self.session_id, asset=e.asset, mode=e.mode,
                        direction=e.direction, decision=e.decision, reason=e.reason,
                        rules_fired=e.rules_fired or [], supporting=e.supporting or [],
                        opposing=e.opposing or [], entry=e.entry, stop_loss=e.stop_loss,
                        take_profit=e.take_profit, exit=e.exit, risk_reward=e.risk_reward,
                        position_size=e.position_size, pnl_usd=e.pnl_usd,
                        result=e.result, lesson=e.lesson,
                    ))
                s.commit()
            finally:
                s.close()
        except Exception:
            pass  # БД не критична для роботи; не валимо сесію через збій запису

    # --------------------------------------------------------------------- #
    #  Дані для дашборду (§26)
    # --------------------------------------------------------------------- #
    def dashboard(self) -> dict:
        acc = self.broker.account_state()
        stats = compute_stats(self.journal.closed_trades())
        pf = None if stats.profit_factor == float("inf") else round(stats.profit_factor, 2)
        return {
            "running": self.running,
            "paused": self.paused,
            "mode": self.config.mode.value,
            "risk_level": self.config.risk_level,
            "is_demo": self.config.is_demo,
            "balance": round(self.broker.equity, 2),
            "starting": self.starting_equity,
            "pnl": round(self.broker.equity - self.starting_equity, 2),
            "pnl_pct": round((self.broker.equity / self.starting_equity - 1) * 100, 2),
            "drawdown_pct": round(acc.drawdown_pct, 2),
            "open_positions": [
                {"asset": p.asset, "direction": p.direction.value,
                 "entry": round(p.entry, 4), "stop": round(p.stop_loss, 4),
                 "take": round(p.take_profit, 4), "size": round(p.size, 6)}
                for p in self.broker.positions
            ],
            "stats": {
                "trades": stats.trades, "wins": stats.wins, "losses": stats.losses,
                "win_rate": round(stats.win_rate, 1),
                "avg_win": round(stats.avg_win, 2), "avg_loss": round(stats.avg_loss, 2),
                "profit_factor": pf, "expectancy": round(stats.expectancy, 3),
                "sample_sufficient": stats.sample_sufficient,
            },
            "equity_curve": self.equity_curve[-200:],
            "last_action": self.last_action,
            "rejected": len([e for e in self.journal.entries if e.decision == "rejected"]),
        }

    def recent_journal(self, limit: int = 20) -> list[dict]:
        out = []
        for e in reversed(self.journal.entries[-limit:]):
            out.append({
                "ts": e.ts, "asset": e.asset, "decision": e.decision,
                "direction": e.direction, "reason": e.reason,
                "pnl": e.pnl_usd, "result": e.result,
                "supporting": e.supporting, "lesson": e.lesson,
            })
        return out

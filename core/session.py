"""
Session Manager — стан запущеної стратегії (Start/Stop flow, §4).

Тримає поточну сесію: режим, рахунок, відкриті позиції, журнал, статистику.
Один «тік» = обробка нової порції даних по watchlist. Для демо/MVP дані
беруться з SyntheticProvider (офлайн), але провайдер замінний на CcxtProvider.

Це шар оркестрації — він не містить торгової логіки, лише викликає двигуни.
"""
from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from core.data.providers import MarketDataProvider, SyntheticProvider
from core.data.quality import DataQualityEngine
from core.engines.journal import FORCED_EXIT_REASON, TRIGGERED_EXIT_REASON, Journal, JournalEntry
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

# TechnicalAnalysis рахує EMA/MACD/RSI/ATR за O(розмір вікна) на кожен тік;
# без обмеження вікно росло б необмежено (series[:cursor+1]), і повний рік
# 1h-свічок (§ historical mode) означав би O(n^2) — цикл, який мав би йти
# "прискорено, хвилини", насправді тягнувся б довше реального часу. 300
# свічок — з великим запасом більше за найдовший індикатор (EMA(50)), тож
# останнє значення не змінюється, лише перестає повторно перераховувати
# всю історію з нуля щотік.
_TA_WINDOW_CANDLES = 300


@dataclass
class SessionConfig:
    amount_usd: float = 500.0
    risk_level: str = "conservative"      # demo | conservative | moderate
    mode: Mode = Mode.PAPER
    assets: list[str] = field(default_factory=lambda: ["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    cycle_months: int = 2
    live_enabled: bool = False            # реальні гроші (за замовч. вимкнено)
    live_confirmed: bool = False          # користувач явно підтвердив live
    # "fast_sim" — прискорена симуляція на СИНТЕТИЧНИХ даних (лише для
    # тренажера/демо, ніколи не пропонується як основний Paper-вибір);
    # "historical" — реальна історія Binance за обраний рік (historical_year),
    # відтворена прискорено, як fast_sim, але на СПРАВЖНІХ цінах;
    # "live_realtime" — реальні ціни з біржі в реальному темпі. У Paper-режимі
    # (mode="paper") гроші завжди паперові (PaperBroker) незалежно від
    # market_mode. Лише коли mode="live" І live_enabled/live_confirmed —
    # Session вмикає LiveBroker (реальні ордери через LiveTradingAdapter), і
    # ТІЛЬКИ якщо market_mode="live_realtime" — реальні гроші ніколи не
    # торгують на історичних чи синтетичних даних (перевіряється нижче).
    market_mode: str = "fast_sim"
    historical_year: int | None = None    # рік для market_mode="historical"
    live_interval_sec: int = 60           # як часто тягнути нову ціну в live_realtime
    # "classic" — типові правила з підручника (тренд, RSI, MACD), пороги
    #   фіксовані, не підганяються під жодні дані;
    # "optimized" — параметри ПІДІБРАНІ під історію 2021-2025, щоб
    #   максимізувати минулий прибуток (§ навмисна демонстрація overfitting,
    #   чесно позначена попередженням в UI);
    # "dca" — без вибору моменту входу: фіксований план регулярних внесків
    #   (усереднення), без Signal/Risk Engine.
    strategy: str = "classic"

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
        # Реальні гроші (§24, §35): та сама умова, що визначає self.live вище
        # (enabled=live_enabled and live_confirmed) вирішує, чи ця сесія
        # торгуватиме РЕАЛЬНИМИ ордерами через LiveBroker, а не симуляцією.
        # Обидва мають лишатись синхронізованими — якщо колись розійдуться,
        # або гроші стануть "реальними, але симульованими", або навпаки.
        self.is_real_live = (
            config.mode == Mode.LIVE and config.live_enabled and config.live_confirmed)
        if self.is_real_live:
            # /api/start уже відхиляє цю комбінацію (422) — тут захист про
            # запас, якщо Session колись створять напряму (скрипт, тест),
            # оминаючи API-шар: реальні гроші допустимі лише на реальних
            # цінах у реальному часі, з неоптимізованою (не "перепідігнаною")
            # стратегією.
            if config.market_mode != "live_realtime" or config.strategy != "classic":
                raise ValueError(
                    "Live (реальні гроші) дозволено лише з "
                    "market_mode='live_realtime' і strategy='classic'."
                )
            from core.engines.live_broker import LiveBroker
            self.broker = LiveBroker(self.live, starting_equity_hint=config.amount_usd)
        self._news_cache: dict = {}
        self.optimized_params = None   # заповнюється нижче лише для strategy="optimized"
        self.ta = TechnicalAnalysis()
        self.dq = DataQualityEngine()
        self.starting_equity = config.amount_usd

        self._series: dict[str, list] = {}
        self.providers: dict[str, MarketDataProvider] = {}
        self._last_live_fetch: datetime | None = None

        if config.market_mode == "live_realtime":
            # Реальні ціни з біржі, реальний темп: одна свічка = одна реальна
            # хвилина, оновлення раз на live_interval_sec (типово 60с). Гроші
            # й тут паперові (PaperBroker) — окремо від live_enabled/LiveTradingAdapter.
            live_provider = provider or self._default_live_provider()
            for a in config.assets:
                self.providers[a] = live_provider
                self._series[a] = live_provider.fetch_ohlcv(a, "1m", limit=200)
        elif config.market_mode == "historical":
            # Реальна історія Binance за обраний рік, відтворена прискорено —
            # той самий cursor-based playback, що й fast_sim (_tick_fast),
            # але на СПРАВЖНІХ цінах замість синтетичного завжди-зростаючого ряду.
            year = config.historical_year or (datetime.now(timezone.utc).year - 1)
            hist_provider = provider or self._default_historical_provider(year)
            for a in config.assets:
                self.providers[a] = hist_provider
                self._series[a] = hist_provider.fetch_ohlcv(a, "1h", limit=100_000)
        else:
            # Готуємо дані наперед. Для офлайн-демо генеруємо довгу синтетичну
            # історію на кожен актив один раз; курсор іде вперед по ній.
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

        # --- Стратегія: яка логіка ухвалення рішень водить цю сесію -------- #
        # Будується ПІСЛЯ підготовки даних вище, бо "dca" рахує план внесків
        # від довжини серії (total_ticks).
        if config.strategy == "optimized":
            # "Оптимізована по історії" — параметри ПІДІБРАНІ під 2021-2025,
            # щоб максимізувати минулий прибуток, а не пороги з підручника.
            # Саме тому вона, найімовірніше, програє наперед (§ overfitting,
            # чесно позначено попередженням в UI, api/main.py STRATEGIES).
            from core.engines.strategy_optimizer import fit_optimized_params
            self.optimized_params = fit_optimized_params()
            self.signal = self.optimized_params.to_signal_engine()
            self.engine = PaperTradingEngine(self.signal, self.risk, self.broker, self.journal,
                                            mode=config.mode.value)
        elif config.strategy == "dca":
            # "Надійна (усереднення)" — без вибору моменту входу: фіксований
            # план регулярних внесків, без Signal/Risk Engine.
            from core.engines.dca_engine import DCAEngine
            if config.market_mode == "live_realtime":
                engine_kwargs = {"interval_ticks": 24}
            else:
                first_series = next(iter(self._series.values()), [])
                engine_kwargs = {"total_ticks": max(1, len(first_series) - 60)}
            self.engine = DCAEngine(
                self.broker, self.journal, config.assets, config.amount_usd, **engine_kwargs)
        else:
            # "Класична (з літератури)" — типові правила (тренд, RSI, MACD),
            # фіксовані пороги, нічого не підганяється під жодні дані.
            self.engine = PaperTradingEngine(self.signal, self.risk, self.broker, self.journal,
                                            mode=config.mode.value)

        self.running = False
        self.paused = False
        import uuid
        self.session_id = uuid.uuid4().hex[:16]
        self.last_action = "Сесія створена. Натисніть «Почати стратегію»."
        self.equity_curve: list[float] = [config.amount_usd]
        self._cursor = 60  # позиція у підготовленій історії (fast_sim/historical)
        self._max_curve = 300  # обмеження довжини кривої для дашборду
        self._lock = threading.Lock()

    @staticmethod
    def _default_live_provider() -> MarketDataProvider:
        from core.data.providers import CcxtProvider
        return CcxtProvider("binance")

    @staticmethod
    def _default_historical_provider(year: int) -> MarketDataProvider:
        from core.data.providers import HistoricalProvider
        return HistoricalProvider(year=year)

    # --------------------------------------------------------------------- #
    #  Один тік стратегії
    # --------------------------------------------------------------------- #
    def tick(self) -> None:
        if not self.running or self.paused:
            return
        with self._lock:
            if self.config.market_mode == "live_realtime":
                self._tick_live()
            else:
                self._tick_fast()

    def _tick_fast(self) -> None:
        for asset in self.providers:
            series = self._series[asset]
            if self._cursor >= len(series):
                continue
            window = series[max(0, self._cursor + 1 - _TA_WINDOW_CANDLES):self._cursor + 1]
            if len(window) < 60:
                continue
            if asset not in self._news_cache or self._cursor % 24 == 0:
                self._news_cache[asset] = self.news.analyze(asset)
            self._process_window(asset, window, timeframe="1h", check_staleness=False,
                                 news_ctx=self._news_cache[asset])
        self.equity_curve.append(round(self.broker.equity, 2))
        if len(self.equity_curve) > self._max_curve:
            self.equity_curve = self.equity_curve[-self._max_curve:]
        self._cursor += 1
        # забагато повторних просадок цього циклу — чесно зупиняємось, а не
        # мовчки продовжуємо накопичувати збитки до кінця історії
        if self.broker.hard_stopped and self.running:
            self.running = False
            return
        # коли історія вичерпана — завершуємо цикл і пропонуємо звіт
        if self._cursor >= len(next(iter(self._series.values()))):
            self.running = False
            self.last_action = (
                "Навчальний цикл завершено. Натисніть «Зупинити і "
                "проаналізувати» для звіту.")

    def _tick_live(self) -> None:
        now = datetime.now(timezone.utc)
        interval = self.config.live_interval_sec
        if self._last_live_fetch is not None:
            elapsed = (now - self._last_live_fetch).total_seconds()
            if elapsed < interval:
                remaining = int(interval - elapsed)
                self.last_action = f"Live: наступне оновлення ціни через {remaining} с."
                return
        self._last_live_fetch = now
        processed = False
        for asset in self.providers:
            try:
                candles = self.providers[asset].fetch_ohlcv(asset, "1m", limit=200)
            except Exception as e:
                self.last_action = f"{asset}: не вдалося отримати дані з біржі ({e})."
                continue
            if len(candles) < 60:
                continue
            self._series[asset] = candles
            if asset not in self._news_cache:
                self._news_cache[asset] = self.news.analyze(asset)
            self._process_window(asset, candles, timeframe="1m", check_staleness=True,
                                 news_ctx=self._news_cache[asset])
            processed = True
        if processed:
            self.equity_curve.append(round(self.broker.equity, 2))
            if len(self.equity_curve) > self._max_curve:
                self.equity_curve = self.equity_curve[-self._max_curve:]
        # забагато повторних просадок цього циклу — чесно зупиняємось (§_tick_fast)
        if self.broker.hard_stopped:
            self.running = False

    def _process_window(self, asset, window, timeframe: str, check_staleness: bool, news_ctx):
        """Спільна логіка для fast_sim і live_realtime — сама торгова логіка
        не змінюється, лише джерело й темп надходження свічок (§F)."""
        current = window[-1]
        # закрити позиції за діапазоном поточної свічки
        close_narrations = []
        for pos, pnl, result in self.broker.update_candle(
                asset, current.high, current.low):
            self._journal_close(pos, pnl, result, current.close, current.ts)
            close_narrations.append(narrate_entry_uk(self.journal.entries[-1]))
        report = self.dq.check(window, timeframe, check_staleness=check_staleness)
        factors, snapshot = self.ta.analyze(
            asset, window, report.reliable, report.issues)
        before = len(self.journal.entries)
        # as_of=current.ts — час свічки, а не datetime.now(): у fast_sim/
        # historical це симульований момент з минулого, журнал має показувати
        # ЙОГО, а не реальний поточний час (§ issue: журнал завжди показував
        # "зараз" навіть для угод 2022/2025 років)
        msg = self.engine.step(snapshot, factors,
                               update_positions=False, news=news_ctx, as_of=current.ts)
        if len(self.journal.entries) > before:
            step_narration = narrate_entry_uk(self.journal.entries[-1])
        elif msg.startswith("⏳"):
            step_narration = narrate_wait_uk(asset, msg)
        elif msg.startswith("⛔"):
            step_narration = narrate_emergency_stop_uk(
                asset, msg.split(":", 1)[-1])
        else:
            step_narration = msg.splitlines()[0]
        # цикл щойно зупинено чесно (§hard_stopped) — найважливіша подія
        # цього тіку, показуємо її, навіть якщо в цей самий тік закрилась
        # ще й позиція (інакше причину зупинки могло б не побачити ніхто)
        if msg.startswith("🛑"):
            self.last_action = msg[2:].strip()
            return
        # подія закриття важливіша за цей тік — показуємо саме її, якщо була
        self.last_action = close_narrations[-1] if close_narrations else step_narration

    def _journal_close(self, pos, pnl, result, exit_price, as_of: datetime,
                       reason: str = TRIGGERED_EXIT_REASON):
        self.journal.add(JournalEntry(
            ts=as_of.isoformat(),
            asset=pos.asset, mode=self.config.mode.value, direction=pos.direction.value,
            decision="closed", reason=reason, rules_fired=pos.rules_fired,
            supporting=pos.supporting, entry=pos.entry, stop_loss=pos.stop_loss,
            take_profit=pos.take_profit, exit=exit_price, position_size=pos.size,
            pnl_usd=pnl, result=result,
            lesson=self._closing_lesson(result, reason),
        ))

    @staticmethod
    def _closing_lesson(result: str, reason: str) -> str:
        if reason != TRIGGERED_EXIT_REASON:
            # примусове закриття — стоп/тейк тут ні до чого, не приписуємо
            # результат "сетапу", який нічого не вирішував
            return ("прибуток на момент примусового закриття" if result == "win"
                    else "збиток на момент примусового закриття")
        return ("перемога — сетап спрацював" if result == "win"
                else "збиток — переглянути фактори входу")

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
            candle = series[idx]
            # примусово, а не update() (stop/take-перевірка) — "закрити ВСІ
            # угоди" має закривати справді все, а не лише те, що вже й так
            # вийшло б за стопом/тейком (і DCA-позиції інакше не закрились
            # би НІКОЛИ — у них стоп/тейк навмисно ніколи не спрацьовує)
            for pos, pnl, result in self.broker.close_all_positions(asset, candle.close):
                self._journal_close(pos, pnl, result, candle.close, candle.ts,
                                    reason=FORCED_EXIT_REASON)
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
            # лише СПРАВЖНІ спрацювання стопу — не всі збитки: примусове
            # закриття (кінець циклу, "Закрити всі угоди", DCA) теж може дати
            # збиток, але це не заслуга стоп-лосу, і приписувати йому "захист"
            # було б нечесно (§ critical review)
            stop_loss_saves = len([
                e for e in self.journal.entries
                if e.decision == "closed" and e.result == "loss" and e.reason == TRIGGERED_EXIT_REASON
            ])
            s = db_session()
            try:
                s.add(CycleSummary(
                    session_id=self.session_id, starting_equity=self.starting_equity,
                    ending_equity=self.broker.equity, trades=stats.trades,
                    win_rate=stats.win_rate, profit_factor=pf, report_text=report,
                    stop_loss_saves=stop_loss_saves, rejected=rejected,
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
            "is_real_live": self.is_real_live,
            "risk_level": self.config.risk_level,
            "is_demo": self.config.is_demo,
            "market_mode": self.config.market_mode,
            "historical_year": self.config.historical_year,
            "strategy": self.config.strategy,
            "optimized_fit": (asdict(self.optimized_params)
                             if self.optimized_params is not None else None),
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

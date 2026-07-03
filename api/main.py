"""
FastAPI backend — Start/Stop flow і дані для україномовного дашборду.

Запуск:  uvicorn api.main:app --reload

Модель виконання навмисно проста: немає фонових потоків. Кожен запит
/api/dashboard просуває симуляцію на кілька кроків («тіків»). Це робить
сервер передбачуваним, легким для тестів і безпечним для запуску.
Один процес тримає одну активну сесію (для MVP цього достатньо).
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.engines.live_adapter import LiveTradingAdapter
from core.models.types import Mode
from core.session import Session, SessionConfig

app = FastAPI(title="Smart Trading Assistant", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

_FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "index.html"


@app.on_event("startup")
def _init_storage():
    from core.storage.db import init_db
    init_db()  # SQLite за замовч., або DATABASE_URL для PostgreSQL


@app.get("/")
def index():
    """Віддає україномовний інтерфейс із того ж сервера (один URL)."""
    return FileResponse(str(_FRONTEND))

# --- глобальний стан (одна сесія на процес для MVP) ----------------------- #
_session: Session | None = None

# скільки кроків симуляції просувати за кожне опитування дашборду
TICKS_PER_POLL = 3


# --------------------------------------------------------------------------- #
#  Моделі запитів
# --------------------------------------------------------------------------- #
class StartRequest(BaseModel):
    amount_usd: float = 500.0
    risk_level: str = "conservative"
    mode: str = "paper"
    assets: list[str] = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    cycle_months: int = 2
    live_confirmed: bool = False        # користувач явно підтвердив реальні гроші
    # "historical" (реальна історія за historical_year, прискорено),
    # "live_realtime" чи "fast_sim" (синтетика, лише тренажер) — див.
    # SessionConfig.market_mode. Не плутати з mode="live" (реальні гроші) —
    # тут гроші завжди паперові.
    market_mode: str = "fast_sim"
    historical_year: int | None = None  # рік для market_mode="historical"
    live_interval_sec: int = 60


# запам'ятовуємо, чи стратегія пройшла backtest-гейт (потрібно для live)
_backtest_passed: bool = False


# --------------------------------------------------------------------------- #
#  Ендпойнти
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health():
    return {"status": "ok", "backtest_passed": _backtest_passed}


@app.post("/api/backtest")
def backtest():
    """Проганяє backtest на історії. Обов'язковий крок перед Live (§22)."""
    global _backtest_passed
    from core.engines.backtester import Backtester
    from core.engines.signal_engine import SignalEngine
    from core.engines.risk_engine import RiskEngine, RiskConfig
    from core.data.providers import SyntheticProvider
    from core.knowledge.constitution import build_seed_constitution

    bt = Backtester(SignalEngine(build_seed_constitution(), 2),
                    RiskEngine(RiskConfig(min_risk_reward=1.5)))
    seeds = {"BTC/USDT": (1, 60000, 0.004), "ETH/USDT": (7, 3000, 0.003)}
    series = {a: SyntheticProvider(seed=s, start_price=p, drift=d, vol=0.013)
              .fetch_ohlcv(a, "1h", 400) for a, (s, p, d) in seeds.items()}
    res = bt.run(series, starting_equity=500, min_trades=20)
    _backtest_passed = res.passed_gate
    return {
        "passed": res.passed_gate, "summary": res.summary_uk(),
        "metrics": {"trades": res.trades, "win_rate": res.win_rate,
                    "return_pct": res.total_return_pct, "max_dd": res.max_drawdown_pct,
                    "sharpe": res.sharpe, "sortino": res.sortino,
                    "profit_factor": res.profit_factor, "expectancy": res.expectancy},
        "gate_reasons": res.gate_reasons,
    }


@app.post("/api/start")
def start(req: StartRequest):
    global _session
    live_requested = req.mode == "live"

    if live_requested:
        # Live дозволено ЛИШЕ якщо: пройдено backtest + явне підтвердження + є ключі
        problems = []
        if not _backtest_passed:
            problems.append("Спершу запустіть backtest і пройдіть гейт.")
        if not req.live_confirmed:
            problems.append("Немає явного підтвердження на реальні гроші.")
        adapter = LiveTradingAdapter(enabled=True, dry_run=False)
        ready, checks = adapter.preflight()
        problems += [c for c in checks if "dry-run" not in c and "вимкнено" not in c]
        if problems:
            raise HTTPException(
                status_code=403,
                detail={"message": "Live поки недоступний. Виконайте умови:",
                        "checks": problems})

    if req.market_mode not in ("fast_sim", "live_realtime", "historical"):
        raise HTTPException(status_code=422, detail="Невідомий market_mode")
    if req.market_mode == "historical" and not req.historical_year:
        raise HTTPException(status_code=422, detail="Вкажіть рік для історичного режиму")

    cfg = SessionConfig(
        amount_usd=req.amount_usd, risk_level=req.risk_level,
        mode=Mode(req.mode), assets=req.assets, cycle_months=req.cycle_months,
        live_enabled=live_requested, live_confirmed=req.live_confirmed,
        market_mode=req.market_mode, historical_year=req.historical_year,
        live_interval_sec=req.live_interval_sec,
    )
    _session = Session(cfg)
    _session.start()
    return {"message": "Стратегію запущено", "config": req.model_dump()}


@app.post("/api/pause")
def pause():
    _require_session().pause()
    return {"message": "Паузу активовано"}


@app.post("/api/resume")
def resume():
    _require_session().resume()
    return {"message": "Роботу відновлено"}


@app.post("/api/close-all")
def close_all():
    _require_session().close_all()
    return {"message": "Усі позиції закрито"}


@app.post("/api/stop")
def stop():
    session = _require_session()
    report = session.stop_and_review()
    return {"message": "Стратегію зупинено", "report": report,
            "understanding": session.understanding_summary()}


@app.get("/api/dashboard")
def dashboard():
    if _session is None:
        return {"running": False, "message": "Сесія ще не створена"}
    # просуваємо симуляцію на кілька кроків при кожному опитуванні
    if _session.running and not _session.paused:
        for _ in range(TICKS_PER_POLL):
            _session.tick()
    return _session.dashboard()


@app.get("/api/journal")
def journal(limit: int = 20):
    return {"entries": _require_session().recent_journal(limit)}


@app.get("/api/progress")
def progress():
    """Збережений прогрес за весь час (§E1) — не бали, не рівні (§C3)."""
    from core.engines.progress import build_progress_summary
    from core.storage.db import CycleSummary
    from core.storage.db import get_session as db_session

    s = db_session()
    try:
        rows = s.query(CycleSummary).order_by(CycleSummary.ended_at.desc()).all()
        cycles = len(rows)
        total_trades = sum(r.trades for r in rows)
        total_stop_loss_saves = sum(r.stop_loss_saves for r in rows)
        total_rejected = sum(r.rejected for r in rows)
        history = [
            {"session_id": r.session_id, "ended_at": r.ended_at.isoformat(),
             "starting_equity": r.starting_equity, "ending_equity": r.ending_equity,
             "trades": r.trades, "win_rate": r.win_rate,
             "stop_loss_saves": r.stop_loss_saves, "rejected": r.rejected}
            for r in rows
        ]
    finally:
        s.close()

    summary = build_progress_summary(cycles, total_trades, total_stop_loss_saves, total_rejected)
    return {**summary.to_dict(), "history": history}


# --------------------------------------------------------------------------- #
#  Реальні свічки для екранів "Що бачить" / "Що радить" (§B1/§B2)
# --------------------------------------------------------------------------- #
# Раніше тут був набір synthetic-seeds — саме це й спричиняло розбіжність між
# екранами: обидва просили різну кількість свічок (200 і 400) з ОДНОГО
# детермінованого блукання SyntheticProvider, потрапляючи в різні точки
# одного ряду і показуючи різну "поточну ціну" в один момент часу. Тепер
# обидва екрани читають реальні свічки Binance — для одного активу вони
# завжди закінчуються на одній і тій самій останній закритій свічці,
# незалежно від limit, і чесно показують падіння чи бокове рухання ринку.
_SITUATION_CANDLES = 200
_ADVICE_CANDLES = 400
_CANDLE_CACHE_TTL_SEC = 300.0  # 5 хв: досить довго, щоб користувач встиг
                                # прочитати екран "Що бачить" і перейти до
                                # "Порада" — обидва мають показати ОДНУ й ту
                                # саму ціну, а не розійтись через те, що ринок
                                # ворухнувся, поки він читав (§ раніше TTL 20с
                                # був закороткий для реального темпу читання)
_candle_cache: dict[str, tuple[float, list]] = {}   # asset -> (fetched_at, candles)
_candle_cache_lock = threading.Lock()
_live_provider = None            # ліниво створюваний CcxtProvider, спільний для всіх активів
_live_provider_lock = threading.Lock()


def _get_live_provider():
    """Один спільний CcxtProvider на процес — щоб не губити внутрішній
    rate-limit ccxt, який тримає стан на рівні інстансу біржі."""
    global _live_provider
    with _live_provider_lock:
        if _live_provider is None:
            from core.data.providers import CcxtProvider
            _live_provider = CcxtProvider("binance")
        return _live_provider


def _live_candles(asset: str, limit: int):
    """Реальні 1h-свічки з Binance. Кидає виняток, якщо мережа/біржа
    недоступні — обробляється у /api/situation і /api/advice."""
    now = time.monotonic()
    with _candle_cache_lock:
        cached = _candle_cache.get(asset)
        if cached is not None and now - cached[0] < _CANDLE_CACHE_TTL_SEC:
            return cached[1][-limit:]
    # мережевий запит навмисно поза локом — не серіалізуємо запити для різних
    # активів одне за одним, поки триває I/O
    fetch_n = max(limit, _ADVICE_CANDLES)
    candles = _get_live_provider().fetch_ohlcv(asset, "1h", limit=fetch_n)
    with _candle_cache_lock:
        _candle_cache[asset] = (now, candles)
    return candles[-limit:]


@app.get("/api/situation")
def situation(asset: str = "BTC/USDT"):
    """Екран №1 (§B1) «Що система бачить»: проста мова, без жаргону.
    Реальні дані Binance — ті самі, що й на екрані «Порада» (§B2)."""
    from core.engines.plain_language import describe_situation_uk
    from core.engines.technical import TechnicalAnalysis

    try:
        candles = _live_candles(asset, _SITUATION_CANDLES)
        factors, snapshot = TechnicalAnalysis().analyze(asset, candles)
        return describe_situation_uk(factors, snapshot).to_dict()
    except Exception:
        return {"error": True,
                "message": "Не вдалося отримати дані з біржі. Спробуйте ще раз за хвилину."}


@app.get("/api/advice")
def advice(asset: str = "BTC/USDT"):
    """Екран №2 (§B2) «Що радить і чому»: ймовірність + фактори за/проти. Не
    порада купувати. Ті самі реальні свічки Binance, що й /api/situation."""
    from core.engines.advice import AdviceEngine

    try:
        candles = _live_candles(asset, _ADVICE_CANDLES)
        return AdviceEngine().explain(asset, candles).to_dict()
    except Exception:
        return {"error": True,
                "message": "Не вдалося отримати дані з біржі. Спробуйте ще раз за хвилину."}


# --------------------------------------------------------------------------- #
#  Кейси на реальній історії (§D1) + власні кейси користувача (§E2) —
#  «чесна вітрина» за моделлю фотостоку: люди приносять свій контент
# --------------------------------------------------------------------------- #
_CASES_DIR = Path(__file__).resolve().parent.parent / "data" / "cases"
_CASE_ID_RE = re.compile(r"^[A-Za-z0-9_]+$")


@app.get("/api/cases")
def list_cases():
    """Список чесних кейсів — реальна історія (§D1) і власні кейси користувачів (§E2)."""
    cases = []
    if _CASES_DIR.exists():
        for f in sorted(_CASES_DIR.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            cases.append({
                "id": f.stem,
                "asset": data.get("asset"),
                "period_start": data.get("period_start"),
                "period_end": data.get("period_end"),
                "total_return_pct": data.get("total_return_pct"),
                "trades": len(data.get("trades", [])),
                "stop_loss_saves": data.get("stop_loss_saves", 0),
                "rejected_by_risk": data.get("rejected_by_risk", 0),
                "source": data.get("source", "real_history"),
            })
    return {"cases": cases}


@app.post("/api/cases/share")
def share_case():
    """Поділитися своїм кейсом із поточної/останньої сесії (§E2, модель фотостоку)."""
    from core.engines.case_builder import case_from_journal

    session = _require_session()
    try:
        case = case_from_journal(
            session.journal.entries, session.starting_equity, session.broker.equity)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    case_id = f"user_{session.session_id}"
    case.save_json(_CASES_DIR / f"{case_id}.json")
    return {"message": "Кейс збережено", "id": case_id}


@app.get("/api/cases/{case_id}")
def get_case(case_id: str):
    """Повний кейс: усі угоди й моменти, де спрацював стоп-лос (§D1)."""
    if not _CASE_ID_RE.match(case_id):
        raise HTTPException(status_code=404, detail="Кейс не знайдено")
    path = _CASES_DIR / f"{case_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Кейс не знайдено")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/constitution")
def constitution():
    s = _require_session() if _session else None
    rules = s.rules if s else __import__(
        "core.knowledge.constitution", fromlist=["build_seed_constitution"]
    ).build_seed_constitution()
    return {"rules": [
        {"id": r.id, "name": r.name, "category": r.category.value,
         "status": r.status.value, "description": r.description}
        for r in rules
    ]}


def _require_session() -> Session:
    if _session is None:
        raise HTTPException(status_code=400, detail="Сесія не запущена")
    return _session

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

    cfg = SessionConfig(
        amount_usd=req.amount_usd, risk_level=req.risk_level,
        mode=Mode(req.mode), assets=req.assets, cycle_months=req.cycle_months,
        live_enabled=live_requested, live_confirmed=req.live_confirmed,
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


# набір активів і параметрів синтетичних даних, узгоджений із core/session.py,
# щоб «просте» демо-подання ситуації виглядало так само, як і сама стратегія
_SITUATION_SEEDS = {
    "BTC/USDT": (1, 60000, 0.0015),
    "ETH/USDT": (7, 3000, 0.0012),
    "SOL/USDT": (3, 150, 0.001),
}


@app.get("/api/situation")
def situation(asset: str = "BTC/USDT"):
    """Екран №1 (§B1) «Що система бачить»: проста мова, без жаргону."""
    from core.data.providers import SyntheticProvider
    from core.engines.plain_language import describe_situation_uk
    from core.engines.technical import TechnicalAnalysis

    seed, price, drift = _SITUATION_SEEDS.get(
        asset, (abs(hash(asset)) % 1000, 100, 0.001))
    candles = SyntheticProvider(
        seed=seed, start_price=price, drift=drift, vol=0.015).fetch_ohlcv(asset, "1h", 200)
    factors, snapshot = TechnicalAnalysis().analyze(asset, candles)
    return describe_situation_uk(factors, snapshot).to_dict()


@app.get("/api/advice")
def advice(asset: str = "BTC/USDT"):
    """Екран №2 (§B2) «Що радить і чому»: ймовірність + фактори за/проти. Не порада купувати."""
    from core.data.providers import SyntheticProvider
    from core.engines.advice import AdviceEngine

    seed, price, drift = _SITUATION_SEEDS.get(
        asset, (abs(hash(asset)) % 1000, 100, 0.001))
    candles = SyntheticProvider(
        seed=seed, start_price=price, drift=drift, vol=0.015).fetch_ohlcv(asset, "1h", 400)
    return AdviceEngine().explain(asset, candles).to_dict()


# --------------------------------------------------------------------------- #
#  Кейси на реальній історії (§D1) — «чесна вітрина»
# --------------------------------------------------------------------------- #
_CASES_DIR = Path(__file__).resolve().parent.parent / "data" / "cases"
_CASE_ID_RE = re.compile(r"^[A-Za-z0-9_]+$")


@app.get("/api/cases")
def list_cases():
    """Список чесних кейсів на реальній історії (§D1). Без прикрашання."""
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
            })
    return {"cases": cases}


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

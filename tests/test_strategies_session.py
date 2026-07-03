"""Тести інтеграції трьох стратегій (§ "3 окремі режими") із Session/API:
classic (типові правила), optimized (підібрано під історію — overfitting
demo), dca (усереднення). "optimized" підмінюється фейковим підбором, щоб
тест не чекав реальний grid search і не ходив у мережу."""
from fastapi.testclient import TestClient

import api.main as main
import core.engines.strategy_optimizer as opt
from core.data.providers import SyntheticProvider
from core.engines.dca_engine import DCAEngine
from core.engines.paper_trading import PaperTradingEngine
from core.session import Session, SessionConfig


def _fake_params():
    return opt.OptimizedParams(
        min_confirmations=1, atr_stop_mult=2.0, rr_target=2.5,
        rsi_oversold=20.0, rsi_overbought=80.0,
        fit_total_return_pct=37.6, fit_trades=252, fit_win_rate=37.7,
        fit_years="2021-2025",
    )


def test_session_config_defaults_to_classic_strategy():
    assert SessionConfig().strategy == "classic"


def test_classic_strategy_uses_textbook_signal_thresholds():
    provider = SyntheticProvider(seed=1, start_price=100, drift=0.001)
    session = Session(SessionConfig(strategy="classic", assets=["BTC/USDT"]), provider=provider)
    assert isinstance(session.engine, PaperTradingEngine)
    assert session.signal.rsi_oversold == 30.0
    assert session.signal.rr_target == 2.0
    assert session.optimized_params is None


def test_optimized_strategy_uses_fitted_signal_thresholds(monkeypatch):
    monkeypatch.setattr(opt, "fit_optimized_params", lambda *a, **kw: _fake_params())
    provider = SyntheticProvider(seed=1, start_price=100, drift=0.001)
    session = Session(SessionConfig(strategy="optimized", assets=["BTC/USDT"]), provider=provider)
    assert isinstance(session.engine, PaperTradingEngine)
    assert session.signal.rsi_oversold == 20.0
    assert session.signal.rr_target == 2.5
    assert session.optimized_params.fit_total_return_pct == 37.6


def test_dca_strategy_builds_dca_engine_sized_to_series():
    provider = SyntheticProvider(seed=1, start_price=100, drift=0.001)
    session = Session(
        SessionConfig(strategy="dca", assets=["BTC/USDT"], amount_usd=1000), provider=provider)
    assert isinstance(session.engine, DCAEngine)
    # provider дає 1000 свічок (fast_sim default), warmup=60 -> total_ticks=940
    assert session.engine.interval == 940 // 30
    assert session.engine.tranche_usd == 1000 / 1 / 30


def test_dca_and_classic_sessions_both_realize_pnl_on_stop():
    for strategy in ("classic", "dca"):
        provider = SyntheticProvider(seed=2, start_price=20000, drift=0.001)
        session = Session(
            SessionConfig(strategy=strategy, assets=["BTC/USDT"], amount_usd=500),
            provider=provider)
        session.start()
        for _ in range(200):
            session.tick()
        session.stop_and_review()
        d = session.dashboard()
        assert d["open_positions"] == []
        assert d["strategy"] == strategy


def test_api_start_rejects_unknown_strategy():
    with TestClient(main.app) as client:
        r = client.post("/api/start", json={"mode": "paper", "strategy": "made_up"})
    assert r.status_code == 422


def test_api_start_dca_strategy_and_dashboard_exposes_it():
    with TestClient(main.app) as client:
        r = client.post("/api/start", json={
            "mode": "paper", "strategy": "dca", "assets": ["BTC/USDT"],
        })
        assert r.status_code == 200
        d = client.get("/api/dashboard").json()
        assert d["strategy"] == "dca"
        client.post("/api/stop")


def test_api_strategies_endpoint_reports_cache_state(monkeypatch, tmp_path):
    cache_path = tmp_path / "optimized_params.json"
    monkeypatch.setattr(opt, "_CACHE_PATH", cache_path)
    with TestClient(main.app) as client:
        r = client.get("/api/strategies").json()
        assert r["optimized_cached"] is None

        import json
        cache_path.write_text(json.dumps({
            "min_confirmations": 1, "atr_stop_mult": 2.0, "rr_target": 2.5,
            "rsi_oversold": 20.0, "rsi_overbought": 80.0,
            "fit_total_return_pct": 37.6, "fit_trades": 252, "fit_win_rate": 37.7,
            "fit_years": "2021-2025",
        }), encoding="utf-8")
        r2 = client.get("/api/strategies").json()
        assert r2["optimized_cached"]["fit_total_return_pct"] == 37.6

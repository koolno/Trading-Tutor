"""Тест ендпойнтів кейсів (PLAN D1): «чесна вітрина» на реальній історії."""
import json

from fastapi.testclient import TestClient

import api.main as main


def _write_case(dir_, case_id, **overrides):
    data = {
        "asset": "BTC/USDT",
        "period_start": "2026-05-01T00:00:00+00:00",
        "period_end": "2026-06-01T00:00:00+00:00",
        "starting_equity": 500.0,
        "ending_equity": 488.25,
        "total_return_pct": -2.35,
        "rejected_by_risk": 10,
        "stop_loss_saves": 2,
        "trades": [
            {"asset": "BTC/USDT", "direction": "long", "opened_at": "2026-05-02T00:00:00+00:00",
             "closed_at": "2026-05-02T02:00:00+00:00", "entry": 100.0, "stop_loss": 97.0,
             "take_profit": 106.0, "exit": 97.0, "pnl_usd": -3.0, "result": "loss",
             "protected_from_loss": True, "supporting": ["Висхідний тренд"]},
            {"asset": "BTC/USDT", "direction": "long", "opened_at": "2026-05-03T00:00:00+00:00",
             "closed_at": "2026-05-03T04:00:00+00:00", "entry": 100.0, "stop_loss": 97.0,
             "take_profit": 106.0, "exit": 106.0, "pnl_usd": 6.0, "result": "win",
             "protected_from_loss": False, "supporting": ["MACD бичачий"]},
        ],
    }
    data.update(overrides)
    (dir_ / f"{case_id}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def test_list_cases_reads_saved_case_files(tmp_path, monkeypatch):
    _write_case(tmp_path, "BTC_USDT")
    monkeypatch.setattr(main, "_CASES_DIR", tmp_path)
    with TestClient(main.app) as client:
        r = client.get("/api/cases")
    assert r.status_code == 200
    cases = r.json()["cases"]
    assert len(cases) == 1
    assert cases[0]["id"] == "BTC_USDT"
    assert cases[0]["asset"] == "BTC/USDT"
    assert cases[0]["trades"] == 2
    assert cases[0]["stop_loss_saves"] == 2


def test_list_cases_empty_when_no_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_CASES_DIR", tmp_path / "does_not_exist")
    with TestClient(main.app) as client:
        r = client.get("/api/cases")
    assert r.status_code == 200
    assert r.json()["cases"] == []


def test_get_case_returns_full_trades_with_protection_flags(tmp_path, monkeypatch):
    _write_case(tmp_path, "ETH_USDT")
    monkeypatch.setattr(main, "_CASES_DIR", tmp_path)
    with TestClient(main.app) as client:
        r = client.get("/api/cases/ETH_USDT")
    assert r.status_code == 200
    data = r.json()
    assert len(data["trades"]) == 2
    assert data["trades"][0]["protected_from_loss"] is True
    assert data["trades"][1]["protected_from_loss"] is False


def test_get_case_404_for_unknown_id(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_CASES_DIR", tmp_path)
    with TestClient(main.app) as client:
        r = client.get("/api/cases/NOPE")
    assert r.status_code == 404


def test_get_case_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_CASES_DIR", tmp_path)
    with TestClient(main.app) as client:
        r = client.get("/api/cases/..%2F..%2Fsecrets")
    assert r.status_code in (404, 422)


# --- Поділитися своїм кейсом (§E2, модель фотостоку) ---------------------- #
def test_share_case_requires_active_session(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_CASES_DIR", tmp_path)
    monkeypatch.setattr(main, "_session", None)
    with TestClient(main.app) as client:
        r = client.post("/api/cases/share")
    assert r.status_code == 400


def test_share_case_requires_closed_trades(tmp_path, monkeypatch):
    from core.session import Session, SessionConfig
    session = Session(SessionConfig(risk_level="demo", assets=["BTC/USDT"]))
    monkeypatch.setattr(main, "_CASES_DIR", tmp_path)
    monkeypatch.setattr(main, "_session", session)
    with TestClient(main.app) as client:
        r = client.post("/api/cases/share")
    assert r.status_code == 400


def test_share_case_saves_and_appears_in_list(tmp_path, monkeypatch):
    from core.session import Session, SessionConfig
    session = Session(SessionConfig(risk_level="demo", assets=["BTC/USDT"], amount_usd=500))
    session.start()
    for _ in range(150):
        session.tick()
        if session.journal.closed_trades():
            break
    assert session.journal.closed_trades(), "тест потребує хоча б однієї закритої угоди"

    monkeypatch.setattr(main, "_CASES_DIR", tmp_path)
    monkeypatch.setattr(main, "_session", session)
    with TestClient(main.app) as client:
        r = client.post("/api/cases/share")
        assert r.status_code == 200
        case_id = r.json()["id"]
        assert case_id.startswith("user_")

        listing = client.get("/api/cases").json()["cases"]
        shared = next(c for c in listing if c["id"] == case_id)
        assert shared["source"] == "trainer_synthetic"

        detail = client.get(f"/api/cases/{case_id}").json()
        assert detail["source"] == "trainer_synthetic"
        assert len(detail["trades"]) == len(session.journal.closed_trades())

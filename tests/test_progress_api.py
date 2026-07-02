"""Тест ендпойнта /api/progress і збереження прогресу в БД (PLAN E1)."""
import pytest

from core.session import Session, SessionConfig
from core.storage.db import CycleSummary, get_session as db_session, init_db, reset_db
from fastapi.testclient import TestClient

import api.main as main


@pytest.fixture(autouse=True)
def _mem_db(tmp_path):
    # файлова SQLite, не :memory: — TestClient виконує ендпойнти у робочому
    # потоці, а SQLite :memory: не ділиться з'єднанням між потоками
    init_db(f"sqlite:///{tmp_path}/test.db")
    reset_db()
    yield


def test_progress_empty_before_any_cycle():
    with TestClient(main.app) as client:
        r = client.get("/api/progress")
    assert r.status_code == 200
    data = r.json()
    assert data["cycles"] == 0
    assert "перший прогрес" in data["insights"][0].lower()
    assert data["history"] == []


def test_stop_and_review_persists_stop_loss_saves_and_rejected():
    session = Session(SessionConfig(risk_level="demo", assets=["BTC/USDT"], amount_usd=500))
    session.start()
    for _ in range(150):
        session.tick()
        if session.journal.closed_trades():
            break
    session.stop_and_review()

    s = db_session()
    try:
        rows = s.query(CycleSummary).filter_by(session_id=session.session_id).all()
    finally:
        s.close()
    assert len(rows) == 1
    row = rows[0]
    assert row.stop_loss_saves >= 0
    assert row.rejected >= 0
    # узгодженість: losses у журналі === stop_loss_saves у збереженому циклі
    closed = session.journal.closed_trades()
    losses = len([e for e in closed if e.result == "loss"])
    assert row.stop_loss_saves == losses


def test_progress_endpoint_aggregates_across_multiple_cycles():
    s = db_session()
    try:
        s.add(CycleSummary(session_id="a", starting_equity=500, ending_equity=490,
                           trades=3, win_rate=0, stop_loss_saves=2, rejected=10))
        s.add(CycleSummary(session_id="b", starting_equity=500, ending_equity=520,
                           trades=5, win_rate=60, stop_loss_saves=1, rejected=7))
        s.commit()
    finally:
        s.close()

    with TestClient(main.app) as client:
        r = client.get("/api/progress")
    assert r.status_code == 200
    data = r.json()
    assert data["cycles"] == 2
    assert data["total_trades"] == 8
    assert data["total_stop_loss_saves"] == 3
    assert data["total_rejected"] == 17
    assert len(data["history"]) == 2

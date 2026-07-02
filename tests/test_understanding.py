"""Тест «розуміння, а не очки» (PLAN C3): прості підсумки без балів і рівнів."""
from core.engines.journal import JournalEntry
from core.engines.understanding import build_understanding_summary
from core.session import Session, SessionConfig

BAN_WORDS = ["бал", "бали", "очк", "рівень", "рівн", "score", "level", "xp"]


def _entry(**kw):
    base = dict(ts="t", asset="BTC/USDT", mode="paper", direction="long", reason="r")
    base.update(kw)
    return JournalEntry(**base)


def test_no_trades_gives_honest_message_not_fake_summary():
    s = build_understanding_summary([])
    assert len(s.insights_uk) == 1
    assert "ще не встигла" in s.insights_uk[0].lower()


def test_losses_produce_stop_loss_understanding_with_correct_count():
    entries = [_entry(decision="closed", result="loss") for _ in range(3)]
    s = build_understanding_summary(entries)
    text = " ".join(s.insights_uk)
    assert "стоп-лос" in text.lower()
    assert "3 рази" in text


def test_singular_plural_ukrainian_grammar():
    one_loss = build_understanding_summary([_entry(decision="closed", result="loss")])
    assert "1 раз " in one_loss.insights_uk[0] or "1 раз і" in one_loss.insights_uk[0]

    five_losses = build_understanding_summary(
        [_entry(decision="closed", result="loss") for _ in range(5)])
    assert "5 разів" in five_losses.insights_uk[0]


def test_wins_and_rejections_produce_separate_insights():
    entries = (
        [_entry(decision="closed", result="win") for _ in range(2)]
        + [_entry(decision="rejected") for _ in range(4)]
        + [_entry(decision="opened")]
    )
    s = build_understanding_summary(entries)
    text = " ".join(s.insights_uk).lower()
    assert "прибуток" in text
    assert "пропустила 4" in " ".join(s.insights_uk)
    assert "підтвердж" in text


def test_no_gamification_language_anywhere():
    entries = (
        [_entry(decision="closed", result="loss")]
        + [_entry(decision="closed", result="win")]
        + [_entry(decision="rejected")]
        + [_entry(decision="opened")]
    )
    s = build_understanding_summary(entries)
    text = " ".join(s.insights_uk).lower()
    for word in BAN_WORDS:
        assert word not in text, f"ігрове слово '{word}' не мало б з'являтися в підсумку розуміння"


def test_session_understanding_summary_reflects_journal():
    session = Session(SessionConfig(risk_level="demo", assets=["BTC/USDT"], amount_usd=500))
    session.start()
    for _ in range(150):
        session.tick()
        if session.journal.entries:
            break
    insights = session.understanding_summary()
    assert isinstance(insights, list)
    assert len(insights) >= 1

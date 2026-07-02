"""Тест збереженого прогресу користувача (PLAN E1): не бали, не рівні."""
from core.engines.progress import build_progress_summary

BAN_WORDS = ["бали", "бал ", "очко", "очки", "рівень", "level", "score"]


def test_no_cycles_gives_honest_starting_message():
    s = build_progress_summary(0, 0, 0, 0)
    assert s.cycles == 0
    assert "перший прогрес" in s.insights_uk[0].lower()


def test_progress_accumulates_across_cycles_with_correct_grammar():
    s = build_progress_summary(cycles=3, total_trades=17, total_stop_loss_saves=5, total_rejected=40)
    text = " ".join(s.insights_uk)
    assert "3 цикли" in text
    assert "17 угод" in text
    assert "5 разів" in text
    assert "40 разів" in text


def test_single_cycle_grammar():
    s = build_progress_summary(cycles=1, total_trades=2, total_stop_loss_saves=1, total_rejected=0)
    text = " ".join(s.insights_uk)
    assert "1 цикл " in text or "1 цикл і" in text
    assert "1 раз " in text or "1 раз " in text


def test_no_saves_or_rejections_still_reports_cycles_honestly():
    s = build_progress_summary(cycles=2, total_trades=0, total_stop_loss_saves=0, total_rejected=0)
    assert len(s.insights_uk) == 1
    assert "2 цикли" in s.insights_uk[0]


def test_no_gamification_language_anywhere():
    s = build_progress_summary(cycles=5, total_trades=30, total_stop_loss_saves=8, total_rejected=100)
    text = " ".join(s.insights_uk).lower()
    for word in BAN_WORDS:
        assert word not in text, f"ігрове слово '{word}' не мало б з'являтися в прогресі"


def test_to_dict_shape():
    s = build_progress_summary(cycles=2, total_trades=5, total_stop_loss_saves=1, total_rejected=3)
    d = s.to_dict()
    assert d["cycles"] == 2
    assert d["total_trades"] == 5
    assert d["total_stop_loss_saves"] == 1
    assert d["total_rejected"] == 3
    assert d["insights"] == s.insights_uk

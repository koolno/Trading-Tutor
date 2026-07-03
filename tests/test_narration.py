"""Тест пояснень кроків простою мовою (PLAN C2): «система почекала, бо…» тощо."""
from core.engines.journal import TRIGGERED_EXIT_REASON, JournalEntry
from core.engines.narration import (
    narrate_emergency_stop_uk,
    narrate_entry_uk,
    narrate_wait_uk,
)
from core.session import Session, SessionConfig


def _entry(**kw):
    base = dict(ts="t", asset="BTC/USDT", mode="paper", direction="long", reason="r")
    if kw.get("decision") == "closed":
        base["reason"] = TRIGGERED_EXIT_REASON  # справжнє спрацювання стопу/тейку
    base.update(kw)
    return JournalEntry(**base)


def test_narrate_wait_strips_jargon_tail_and_emoji():
    text = narrate_wait_uk("BTC/USDT", "⏳ Сигнали врівноважені — переваги немає. Краще чекати.")
    assert text == "Система почекала з BTC/USDT, бо сигнали врівноважені — переваги немає."
    assert "Краще чекати" not in text
    assert "⏳" not in text


def test_narrate_wait_handles_empty_reason_gracefully():
    text = narrate_wait_uk("BTC/USDT", "⏳ Краще чекати.")
    assert text == "Система почекала з BTC/USDT, бо ще не побачила достатньо підстав для входу."


def test_narrate_entry_opened():
    text = narrate_entry_uk(_entry(decision="opened", direction="long"))
    assert "відкрила угоду (купівлю)" in text
    assert "BTC/USDT" in text


def test_narrate_entry_opened_short():
    text = narrate_entry_uk(_entry(decision="opened", direction="short"))
    assert "продаж" in text


def test_narrate_entry_rejected():
    text = narrate_entry_uk(_entry(decision="rejected", reason="Немає стоп-лосу (стоп дорівнює входу)."))
    assert "відмовилась від угоди" in text
    assert "немає стоп-лосу" in text.lower()


def test_narrate_entry_closed_win_mentions_target_not_jargon():
    text = narrate_entry_uk(_entry(decision="closed", result="win"))
    assert "вийшла з прибутком" in text
    assert "ціна дійшла до цілі" in text


def test_narrate_entry_closed_loss_mentions_stop_loss_protection():
    text = narrate_entry_uk(_entry(decision="closed", result="loss"))
    assert "вийшла зі збитком" in text
    assert "спрацював захист" in text
    assert "стоп-лос" in text


def test_narrate_entry_closed_breakeven():
    text = narrate_entry_uk(_entry(decision="closed", result="breakeven"))
    assert "без прибутку і без збитку" in text


def test_narrate_entry_forced_close_does_not_claim_stop_or_target():
    """Примусове закриття (кінець циклу, DCA, "Закрити всі угоди") не має
    приписувати результат стопу/тейку, які тут ні до чого (§ critical review)."""
    win = narrate_entry_uk(_entry(decision="closed", result="win", reason="Закрито примусово"))
    loss = narrate_entry_uk(_entry(decision="closed", result="loss", reason="Закрито примусово"))
    assert "примусово" in win
    assert "тейк" not in win and "ціль" not in win
    assert "примусово" in loss
    assert "стоп-лос" not in loss and "захист" not in loss


def test_narrate_emergency_stop():
    text = narrate_emergency_stop_uk("BTC/USDT", "Просадка 9.0% досягла ліміту.")
    assert text.startswith("Система зупинилась на BTC/USDT, бо ризик зашкалив:")
    assert "просадка" in text.lower()


# --- Інтеграція: last_action під час тіку тренажера справді пояснює крок --- #
def test_session_last_action_is_plain_language_during_demo_run():
    session = Session(SessionConfig(risk_level="demo", assets=["BTC/USDT"], amount_usd=500))
    session.start()
    seen_wait = seen_trade_event = False
    for _ in range(150):
        session.tick()
        action = session.last_action
        if action.startswith("Система почекала"):
            seen_wait = True
        if ("відкрила угоду" in action or "вийшла з прибутком" in action
                or "вийшла зі збитком" in action or "відмовилась від угоди" in action):
            seen_trade_event = True
        if seen_wait and seen_trade_event:
            break
    assert seen_wait or seen_trade_event, "тренажер має пояснювати кожен крок простою мовою"

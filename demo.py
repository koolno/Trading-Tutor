"""
Демо ядра: Signal Engine + Risk Engine + Constitution разом.

Запуск:  python -m demo
Показує три сценарії: хороша угода, угода без переваги (чекаємо),
угода заблокована Risk Engine. Усе пояснення — українською.
"""
from core.engines.risk_engine import RiskConfig, RiskEngine
from core.engines.signal_engine import SignalEngine, TechnicalFactors
from core.knowledge.constitution import build_seed_constitution
from core.models.types import AccountState, MarketRegime, MarketSnapshot


def line(title: str) -> None:
    print("\n" + "═" * 64)
    print(f"  {title}")
    print("═" * 64)


def main() -> None:
    rules = build_seed_constitution()
    signal = SignalEngine(rules, min_confirmations=2)
    risk = RiskEngine(RiskConfig())  # Conservative Growth дефолти

    account = AccountState(equity=500.0, peak_equity=500.0)
    print(f"Капітал: {account.equity} USD | правил у конституції: {len(rules)}")

    # --- Сценарій 1: чистий бичачий сетап ----------------------------- #
    line("Сценарій 1 — сильний сетап на LONG")
    market = MarketSnapshot(
        asset="BTC/USDT", price=60000.0, spread_pct=0.02,
        liquidity_score=0.95, volatility_atr_pct=2.0,
        regime=MarketRegime.TRENDING_UP,
    )
    tech = TechnicalFactors(
        trend_up=True, macd_bullish=True, near_support=True, rsi=42, atr_pct=2.0,
    )
    idea, why = signal.generate(market, tech)
    print("Signal Engine:", why)
    if idea:
        verdict = risk.evaluate(idea, market, account)
        print(verdict.explanation_uk)

    # --- Сценарій 2: немає переваги -> чекаємо ------------------------ #
    line("Сценарій 2 — змішані сигнали (краще чекати)")
    tech2 = TechnicalFactors(trend_up=True, rsi=50, atr_pct=2.0)  # лише 1 фактор
    idea2, why2 = signal.generate(market, tech2)
    print("Signal Engine:", why2)
    print("Угода НЕ створена — система чекає (правило R-020).")

    # --- Сценарій 3: хороша ідея, але поганий ризик-контекст ---------- #
    line("Сценарій 3 — гарний сигнал, але Risk Engine блокує")
    bad_market = MarketSnapshot(
        asset="SOMECOIN/USDT", price=1.0, spread_pct=1.5,  # завеликий спред
        liquidity_score=0.2, volatility_atr_pct=2.0,       # неліквідний
        regime=MarketRegime.TRENDING_UP,
    )
    bad_account = AccountState(
        equity=460.0, peak_equity=500.0,  # просадка 8%
        consecutive_losses=3,             # серія збитків
    )
    idea3, why3 = signal.generate(bad_market, tech)
    print("Signal Engine:", why3)
    if idea3:
        verdict3 = risk.evaluate(idea3, bad_market, bad_account)
        print(verdict3.explanation_uk)

    # --- Emergency stop ---------------------------------------------- #
    line("Перевірка Emergency Stop")
    triggered, reason = risk.emergency_stop_triggered(bad_account)
    print(f"Emergency stop: {'СПРАЦЮВАВ' if triggered else 'ні'} — {reason}")


if __name__ == "__main__":
    main()

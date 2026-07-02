"""
Statistics, Learning Engine (§18), 80/20 Pareto (§20), Reports (§27).

Аналізує журнал закритих угод і будує:
  • статистику ефективності (win rate, profit factor, expectancy, drawdown);
  • Pareto-аналіз (20% факторів, що дали 80% результату);
  • звіт українською простою мовою.

Принцип §18: не робити радикальних висновків з малої вибірки.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from core.engines.journal import JournalEntry


@dataclass
class Stats:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    total_pnl: float = 0.0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    sample_sufficient: bool = False


def compute_stats(closed: list[JournalEntry]) -> Stats:
    s = Stats(trades=len(closed))
    if not closed:
        return s
    wins = [e for e in closed if e.result == "win"]
    losses = [e for e in closed if e.result == "loss"]
    s.wins = len(wins)
    s.losses = len(losses)
    s.gross_profit = sum(e.pnl_usd or 0 for e in wins)
    s.gross_loss = abs(sum(e.pnl_usd or 0 for e in losses))
    s.total_pnl = sum(e.pnl_usd or 0 for e in closed)
    s.win_rate = s.wins / s.trades * 100
    s.avg_win = s.gross_profit / s.wins if s.wins else 0.0
    s.avg_loss = s.gross_loss / s.losses if s.losses else 0.0
    s.profit_factor = (s.gross_profit / s.gross_loss) if s.gross_loss > 0 else float("inf")
    s.expectancy = s.total_pnl / s.trades
    s.sample_sufficient = s.trades >= 30   # §18: поріг достатньої вибірки
    return s


class ParetoAnalyzer:
    """80/20 Performance Engine (§20)."""
    def by_factor(self, closed: list[JournalEntry]) -> dict[str, float]:
        """Сумарний P/L за кожним фактором підтримки."""
        pnl_by_factor: dict[str, float] = defaultdict(float)
        for e in closed:
            for f in e.supporting:
                pnl_by_factor[f] += e.pnl_usd or 0
        return dict(sorted(pnl_by_factor.items(), key=lambda x: x[1], reverse=True))

    def top_contributors(self, closed: list[JournalEntry]) -> tuple[list, list]:
        """Повертає (найкорисніші фактори, найшкідливіші фактори)."""
        ranked = list(self.by_factor(closed).items())
        helpful = [f for f in ranked if f[1] > 0]
        harmful = [f for f in ranked if f[1] < 0]
        return helpful, harmful


class LearningEngine:
    """§18 — формулює висновки, обережно з малою вибіркою."""
    def insights(self, closed: list[JournalEntry]) -> list[str]:
        out: list[str] = []
        stats = compute_stats(closed)
        if stats.trades == 0:
            return ["Ще немає закритих угод для висновків."]
        if not stats.sample_sufficient:
            out.append(
                f"⚠️ Вибірка мала ({stats.trades} угод). Висновки попередні — "
                "потрібно більше даних для надійних рішень."
            )
        helpful, harmful = ParetoAnalyzer().top_contributors(closed)
        if helpful:
            top = helpful[0]
            out.append(f"Найкорисніший фактор: «{top[0]}» (+{top[1]:.2f} USD).")
        if harmful:
            worst = harmful[-1]
            out.append(f"Найшкідливіший фактор: «{worst[0]}» ({worst[1]:.2f} USD).")
        if stats.profit_factor != float("inf") and stats.profit_factor < 1:
            out.append("Profit factor < 1 — стратегія поки збиткова, не збільшувати ризик.")
        return out


def build_stop_report(closed: list[JournalEntry], starting_equity: float,
                      current_equity: float) -> str:
    """Звіт після Stop (§27), українською."""
    stats = compute_stats(closed)
    pareto = ParetoAnalyzer()
    helpful, harmful = pareto.top_contributors(closed)
    learning = LearningEngine().insights(closed)

    pf = "∞" if stats.profit_factor == float("inf") else f"{stats.profit_factor:.2f}"
    lines = [
        "═" * 56,
        "  ЗВІТ ПІСЛЯ ЗУПИНКИ ЦИКЛУ",
        "═" * 56,
        f"Початковий капітал: {starting_equity:.2f} USD",
        f"Поточний капітал:   {current_equity:.2f} USD",
        f"Результат циклу:    {current_equity - starting_equity:+.2f} USD "
        f"({(current_equity / starting_equity - 1) * 100:+.2f}%)",
        "",
        "── Статистика ──",
        f"Угод закрито:       {stats.trades}",
        f"Прибуткові / збиткові: {stats.wins} / {stats.losses}",
        f"Win rate:           {stats.win_rate:.1f}%",
        f"Середній виграш:    {stats.avg_win:.2f} USD",
        f"Середній програш:   {stats.avg_loss:.2f} USD",
        f"Profit factor:      {pf}",
        f"Expectancy:         {stats.expectancy:.3f} USD/угоду",
        "",
        "── 80/20 аналіз ──",
    ]
    if helpful:
        lines.append("Що приносило прибуток:")
        for f, v in helpful[:3]:
            lines.append(f"  • {f}: +{v:.2f} USD")
    if harmful:
        lines.append("Що створювало збитки:")
        for f, v in harmful[:3]:
            lines.append(f"  • {f}: {v:.2f} USD")
    if not helpful and not harmful:
        lines.append("  Недостатньо даних для Pareto-аналізу.")

    lines += ["", "── Висновки системи ──"]
    lines += [f"  • {i}" for i in learning]
    lines += ["", "── План наступного циклу ──"]
    if not stats.sample_sufficient:
        lines.append("  • Зібрати більше угод перед зміною правил.")
    lines.append("  • Зберегти фактори з позитивним внеском, переглянути збиткові.")
    lines.append("  • Не збільшувати ризик без статистичного підтвердження.")
    lines.append("═" * 56)
    return "\n".join(lines)

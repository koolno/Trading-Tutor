"""
Technical Analysis Module (§12).

Рахує індикатори з реальних свічок і перетворює їх на TechnicalFactors
(вхід Signal Engine) та оцінку ринку для MarketSnapshot. Реалізації
індикаторів — чистий Python без зовнішніх TA-бібліотек, щоб не залежати
від важких пакетів і легко тестувати.

Технічний аналіз НЕ може бути єдиною підставою для угоди (§12) — він лише
постачає фактори, які далі фільтрують Signal Engine і Risk Engine.
"""
from __future__ import annotations

from core.data.providers import Candle
from core.engines.signal_engine import TechnicalFactors
from core.models.types import MarketRegime, MarketSnapshot


# --------------------------------------------------------------------------- #
#  Базові індикатори
# --------------------------------------------------------------------------- #
def ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def sma(values: list[float], period: int) -> float:
    if len(values) < period:
        return sum(values) / len(values) if values else 0.0
    return sum(values[-period:]) / period


def rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = closes[-i] - closes[-i - 1]
        (gains if diff >= 0 else losses).append(abs(diff))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(closes: list[float]) -> tuple[float, float]:
    """Повертає (macd_line, signal_line) на останній свічці."""
    if len(closes) < 35:
        return 0.0, 0.0
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    macd_line = [a - b for a, b in zip(ema12, ema26)]
    signal_line = ema(macd_line, 9)
    return macd_line[-1], signal_line[-1]


def atr_pct(candles: list[Candle], period: int = 14) -> float:
    """Average True Range як % від останньої ціни."""
    if len(candles) < period + 1:
        return 1.0
    trs = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        tr = max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
        trs.append(tr)
    atr = sum(trs[-period:]) / period
    last = candles[-1].close
    return (atr / last * 100) if last > 0 else 1.0


# --------------------------------------------------------------------------- #
#  Аналіз: свічки -> фактори + знімок ринку
# --------------------------------------------------------------------------- #
class TechnicalAnalysis:
    def analyze(self, symbol: str, candles: list[Candle],
                data_reliable: bool = True,
                data_issues: list[str] | None = None) -> tuple[TechnicalFactors, MarketSnapshot]:
        closes = [c.close for c in candles]
        last = closes[-1]

        ema_fast = ema(closes, 20)[-1]
        ema_slow = ema(closes, 50)[-1]
        macd_line, macd_sig = macd(closes)
        rsi_val = rsi(closes)
        atr_p = atr_pct(candles)

        # тренд за взаємним розташуванням EMA
        trend_up = ema_fast > ema_slow and last > ema_fast
        trend_down = ema_fast < ema_slow and last < ema_fast

        # support/resistance за нещодавніми екстремумами
        window = closes[-30:] if len(closes) >= 30 else closes
        recent_low = min(window)
        recent_high = max(window)
        near_support = (last - recent_low) / last < 0.02 if last else False
        near_resistance = (recent_high - last) / last < 0.02 if last else False

        # пробій вгору
        prev_high = max(closes[-30:-1]) if len(closes) > 31 else recent_high
        breakout_up = last > prev_high

        # режим ринку
        if atr_p > 8:
            regime = MarketRegime.HIGH_VOLATILITY
        elif trend_up:
            regime = MarketRegime.TRENDING_UP
        elif trend_down:
            regime = MarketRegime.TRENDING_DOWN
        else:
            regime = MarketRegime.RANGING

        # оцінка ліквідності за обсягом (груба нормалізація)
        avg_vol = sum(c.volume for c in candles[-30:]) / min(30, len(candles))
        liquidity = max(0.0, min(1.0, avg_vol / (avg_vol + 100)))

        # спред як частка ATR (за відсутності order book)
        spread_pct = min(0.5, atr_p / 50)

        factors = TechnicalFactors(
            trend_up=trend_up,
            trend_down=trend_down,
            rsi=round(rsi_val, 1),
            macd_bullish=macd_line > macd_sig,
            macd_bearish=macd_line < macd_sig,
            near_support=near_support,
            near_resistance=near_resistance,
            breakout_up=breakout_up,
            atr_pct=round(atr_p, 3),
        )
        snapshot = MarketSnapshot(
            asset=symbol,
            price=round(last, 8),
            spread_pct=round(spread_pct, 4),
            liquidity_score=round(liquidity, 3),
            volatility_atr_pct=round(atr_p, 3),
            regime=regime,
            data_is_reliable=data_reliable,
            data_issues=data_issues or [],
        )
        return factors, snapshot

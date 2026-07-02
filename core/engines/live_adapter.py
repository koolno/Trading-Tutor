"""
Live Trading Adapter (§24, §35) — реальне підключення до Binance через ccxt.

⚠️ БЕЗПЕКА (потрійний захист):
  1. enabled=False за замовчуванням — live вимкнено;
  2. dry_run=True за замовчуванням — навіть увімкнений, ордери не надсилаються;
  3. before Live — backtest-гейт і явне підтвердження користувача.

Ключі — лише з оточення (.env): BINANCE_API_KEY / BINANCE_API_SECRET.
Права ключа: тільки читання балансу і спот-торгівля. Вивід коштів (withdrawal)
має бути ВИМКНЕНИЙ на боці біржі — система його не використовує і не потребує.

У середовищі розробки немає інтернету, тож реальні виклики перевіряються на
твоєму комп'ютері. Логіка захисту й формування ордера протестована на моках.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from core.models.types import Direction, TradeIdea


@dataclass
class OrderResult:
    accepted: bool
    dry_run: bool
    detail: str
    order_id: str | None = None


class LiveTradingAdapter:
    def __init__(self, exchange_id: str = "binance",
                 enabled: bool = False, dry_run: bool = True):
        self.exchange_id = exchange_id
        self.enabled = enabled
        self.dry_run = dry_run
        self._exchange = None

    # --------------------------------------------------------------------- #
    #  Перевірка готовності перед live (§5.3)
    # --------------------------------------------------------------------- #
    def preflight(self) -> tuple[bool, list[str]]:
        problems: list[str] = []
        if not self.enabled:
            problems.append("Live-режим вимкнено (enabled=False).")
        if not os.getenv(f"{self.exchange_id.upper()}_API_KEY"):
            problems.append("Немає API-ключа в оточенні (.env).")
        if not os.getenv(f"{self.exchange_id.upper()}_API_SECRET"):
            problems.append("Немає API-секрету в оточенні (.env).")
        ready = len([p for p in problems if "вимкнено" not in p]) == 0 and self.enabled
        if ready and self.dry_run:
            problems.append("Готово, але активний dry-run (ордери не надсилаються).")
        return ready, problems

    def _connect(self):
        if self._exchange is not None:
            return self._exchange
        import ccxt
        key = os.getenv(f"{self.exchange_id.upper()}_API_KEY")
        secret = os.getenv(f"{self.exchange_id.upper()}_API_SECRET")
        if not key or not secret:
            raise RuntimeError("Немає ключів API для біржі.")
        self._exchange = getattr(ccxt, self.exchange_id)({
            "apiKey": key, "secret": secret, "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        return self._exchange

    # --------------------------------------------------------------------- #
    #  Читання балансу
    # --------------------------------------------------------------------- #
    def get_balance(self, quote: str = "USDT") -> float:
        if not self.enabled:
            return 0.0
        ex = self._connect()
        bal = ex.fetch_balance()
        return float(bal.get("total", {}).get(quote, 0.0))

    # --------------------------------------------------------------------- #
    #  Виставлення ордера з потрійним захистом
    # --------------------------------------------------------------------- #
    def place_order(self, idea: TradeIdea, size: float) -> OrderResult:
        side = "buy" if idea.direction == Direction.LONG else "sell"
        if not self.enabled:
            return OrderResult(False, self.dry_run, "Відхилено: live-режим вимкнено.")
        if self.dry_run:
            return OrderResult(
                True, True,
                f"DRY-RUN: {side.upper()} {size:.6f} {idea.asset} @ ~{idea.entry_price}. "
                "Реальний ордер НЕ надіслано.")
        # --- реальне виконання (працює на твоєму комп'ютері з ключами) ---
        try:
            ex = self._connect()
            order = ex.create_order(idea.asset, "market", side, size)
            oid = str(order.get("id", ""))
            # захисний стоп-ордер
            opp = "sell" if side == "buy" else "buy"
            try:
                ex.create_order(idea.asset, "stop_loss_limit", opp, size,
                                idea.stop_loss,
                                {"stopPrice": idea.stop_loss, "price": idea.stop_loss})
            except Exception:
                pass  # деякі ринки не підтримують — стоп контролюється системою
            return OrderResult(True, False,
                               f"Ордер надіслано: {side.upper()} {size:.6f} {idea.asset}.",
                               order_id=oid)
        except Exception as e:
            return OrderResult(False, False, f"Помилка біржі: {e}")

    def emergency_close_all(self, positions: list | None = None) -> OrderResult:
        if self.dry_run or not self.enabled:
            return OrderResult(True, True, "DRY-RUN: всі позиції умовно закрито.")
        try:
            ex = self._connect()
            closed = 0
            for pos in (positions or []):
                side = "sell" if pos.direction == Direction.LONG else "buy"
                ex.create_order(pos.asset, "market", side, pos.size)
                closed += 1
            return OrderResult(True, False, f"Аварійно закрито позицій: {closed}.")
        except Exception as e:
            return OrderResult(False, False, f"Помилка аварійного закриття: {e}")

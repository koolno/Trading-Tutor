"""
Live Trading Adapter (§24, §35) — реальне підключення до Binance через ccxt.

⚠️ БЕЗПЕКА (потрійний захист):
  1. enabled=False за замовчуванням — live вимкнено;
  2. dry_run=True за замовчуванням — навіть увімкнений, ордери не надсилаються;
  3. before Live — backtest-гейт і явне підтвердження користувача.

Ключі — лише з оточення (.env, завантажується через load_dotenv() у
api/main.py): BINANCE_API_KEY / BINANCE_API_SECRET. Права ключа: тільки
читання балансу і спот-торгівля. Вивід коштів (withdrawal) має бути
ВИМКНЕНИЙ на боці біржі — система його не використовує і не потребує.

Кожна позиція отримує ДВА захисних ордери на біржі — стоп-лос і тейк-профіт
(окремі ордери, не атомарна OCO-пара: простіше й портативніше між версіями
ccxt/біржами; LiveBroker.core/engines/live_broker.py звіряє статус обох на
кожному тіку й скасовує той, що не спрацював).

У середовищі розробки немає інтернету, тож реальні виклики перевіряються на
твоєму комп'ютері. Логіка захисту й формування ордера протестована на моках
(core/engines/live_broker.py + tests/test_live_broker.py).
"""
from __future__ import annotations

from dataclasses import dataclass
import os

from core.models.types import Direction, TradeIdea


@dataclass
class OrderResult:
    accepted: bool
    dry_run: bool
    detail: str
    order_id: str | None = None      # id ГОЛОВНОГО ордера цього виклику (вхід — у place_order)
    fill_price: float | None = None  # реальна ціна виконання, якщо біржа її повернула
    stop_order_id: str | None = None  # id стоп-ордера, виставленого РАЗОМ із входом (place_order)


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

    def round_amount(self, symbol: str, amount: float) -> float:
        """Округлює кількість до кроку лоту біржі — реальна біржа відхилить
        ордер, чия кількість не відповідає amount_to_precision символу."""
        try:
            ex = self._connect()
            return float(ex.amount_to_precision(symbol, amount))
        except Exception:
            return amount  # без підключення (dry-run/тести без ключів) — як є

    # --------------------------------------------------------------------- #
    #  Виставлення ордерів
    # --------------------------------------------------------------------- #
    def _place_protective_order(self, symbol: str, side: str, size: float,
                                 price: float, order_type: str, params: dict
                                ) -> OrderResult:
        """Спільний хелпер для стоп-лос і тейк-профіт ордерів — обидва це
        "ордер у протилежний бік за заданою ціною", різниться лише тип
        ордера на біржі."""
        try:
            ex = self._connect()
            order = ex.create_order(symbol, order_type, side, size, price, params)
            return OrderResult(True, False, f"{order_type} ордер встановлено.",
                               order_id=str(order.get("id", "")))
        except Exception as e:
            return OrderResult(False, False, f"Не вдалося встановити {order_type}: {e}")

    def place_order(self, idea: TradeIdea, size: float) -> OrderResult:
        """Ринковий вхід + захисний стоп-ордер. Тейк-профіт — окремим
        викликом place_take_profit() (LiveBroker робить це одразу після
        успішного входу), щоб обидва мали власний OrderResult і власний
        order_id для звірки на кожному тіку."""
        side = "buy" if idea.direction == Direction.LONG else "sell"
        if not self.enabled:
            return OrderResult(False, self.dry_run, "Відхилено: live-режим вимкнено.")
        if self.dry_run:
            return OrderResult(
                True, True,
                f"DRY-RUN: {side.upper()} {size:.6f} {idea.asset} @ ~{idea.entry_price}. "
                "Реальний ордер НЕ надіслано.", fill_price=idea.entry_price)
        # --- реальне виконання (працює на твоєму комп'ютері з ключами) ---
        try:
            ex = self._connect()
            order = ex.create_order(idea.asset, "market", side, size)
            oid = str(order.get("id", ""))
            fill_price = order.get("average") or order.get("price") or idea.entry_price
        except Exception as e:
            return OrderResult(False, False, f"Помилка біржі: {e}")

        opp = "sell" if side == "buy" else "buy"
        detail = f"Ордер надіслано: {side.upper()} {size:.6f} {idea.asset}."
        stop_result = self._place_protective_order(
            idea.asset, opp, size, idea.stop_loss, "stop_loss_limit",
            {"stopPrice": idea.stop_loss, "price": idea.stop_loss})
        if not stop_result.accepted:
            # чесно повідомляємо, а не ховаємо — раніше ця помилка мовчки
            # проковтувалась (except: pass), і користувач не бачив, що вхід
            # відбувся БЕЗ захисного стопу на біржі
            detail += f" УВАГА: захисний стоп-ордер не встановлено ({stop_result.detail})."

        return OrderResult(True, False, detail, order_id=oid, fill_price=fill_price,
                           stop_order_id=stop_result.order_id if stop_result.accepted else None)

    def place_take_profit(self, idea: TradeIdea, size: float) -> OrderResult:
        """Окремий лімітний тейк-профіт ордер у протилежний бік за
        idea.take_profit — викликається LiveBroker.open() одразу після
        успішного place_order()."""
        if not self.enabled:
            return OrderResult(False, self.dry_run, "Відхилено: live-режим вимкнено.")
        if self.dry_run:
            return OrderResult(True, True, f"DRY-RUN: тейк-профіт {idea.asset} @ {idea.take_profit}.")
        side = "sell" if idea.direction == Direction.LONG else "buy"
        return self._place_protective_order(idea.asset, side, size, idea.take_profit, "limit", {})

    # --------------------------------------------------------------------- #
    #  Звірка стану ордерів (LiveBroker.update()/update_candle())
    # --------------------------------------------------------------------- #
    def fetch_order(self, order_id: str | None, symbol: str) -> dict | None:
        """Повний об'єкт ордера ccxt (статус, реальна ціна виконання) — або
        None, якщо ордера немає чи перевірити не вдалось (мережа/біржа).
        Тимчасова помилка опитування НЕ повинна валити тік."""
        if not self.enabled or self.dry_run or not order_id:
            return None
        try:
            ex = self._connect()
            return ex.fetch_order(order_id, symbol)
        except Exception:
            return None

    def cancel_order(self, order_id: str | None, symbol: str) -> bool:
        """Скасовує ордер, що не знадобився (інший захисний ордер уже
        спрацював). Не критично, якщо не вийде (напр. вже виконаний/
        скасований сам) — тому лише best-effort, без винятку назовні."""
        if not self.enabled or self.dry_run or not order_id:
            return True
        try:
            ex = self._connect()
            ex.cancel_order(order_id, symbol)
            return True
        except Exception:
            return False

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

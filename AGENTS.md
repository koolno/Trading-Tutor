# AGENTS.md — гід для продовження в Codex

Цей файл допомагає ШІ-агенту (Codex) або розробнику швидко зрозуміти проєкт
і безпечно його розвивати далі.

## Що це
Україномовний ШІ-помічник для поступового росту капіталу. Зовні просто,
всередині — аналіз + жорсткий контроль ризику. Спот-торгівля, Binance.

## Як запустити
```bash
pip install -r requirements.txt
uvicorn api.main:app --reload      # http://localhost:8000 (і UI, і API)
python -m pytest -q                # 40 тестів
python -m demo_full                # офлайн-демо повного циклу
```

## Архітектура (модулі незалежні й замінні)
```
api/main.py               FastAPI: Start/Stop, backtest, live-гейт, dashboard
core/session.py           оркестрація одного «тіку» стратегії
core/models/types.py      суворі доменні типи (обмін між модулями)
core/knowledge/           Trading Knowledge Constitution (правила + статуси)
core/data/                провайдери даних + Data Quality Engine
core/storage/db.py        SQLAlchemy (SQLite за замовч., PostgreSQL за env)
core/engines/
  technical.py            RSI/MACD/EMA/ATR, режим ринку
  news_engine.py          новини: настрій, довіра джерел, вплив на сигнал
  fundamental.py          фундаментал крипти/акцій
  signal_engine.py        структуровані ідеї (враховує новини)
  risk_engine.py          ⭐ ВЕТО на будь-яку угоду
  paper_trading.py        симуляція виконання (комісії, slippage)
  backtester.py           метрики + Live-гейт (Sharpe/Sortino/PF/DD)
  live_adapter.py         реальний Binance через ccxt (потрійний захист)
  investment_memory.py    накопичення спостережень (persist у БД)
  learning.py             статистика + 80/20 + звіти українською
  journal.py              журнал рішень
frontend/index.html       україномовний дашборд (React через CDN, без збірки)
```

## Потік даних одного рішення
дані → якість → технічний аналіз + новини + фундаментал → Signal Engine →
Risk Engine (вето) → Paper/Live виконання → журнал → БД → статистика/80-20.

## Безпека Live (НЕ послаблювати)
Live торгівля вмикається ЛИШЕ якщо одночасно:
1. пройдено `/api/backtest` (гейт: PF>1, expectancy>0, ≥20 угод, DD<25%);
2. є `BINANCE_API_KEY` / `BINANCE_API_SECRET` у `.env` (лише торгівля, вивід вимкнено);
3. користувач явно підтвердив (`live_confirmed=true`).
За замовчуванням `enabled=False`, `dry_run=True`. Три незалежні запобіжники.

## Що варто зробити далі (пріоритет)
1. **Реальні дані замість синтетики**: у `core/session.py` замість
   `SyntheticProvider` підставити `CcxtProvider("binance")` для живих свічок,
   а в `NewsEngine` — `CryptoPanicProvider` (потрібен `CRYPTOPANIC_TOKEN`).
2. **Реальний фундаментал**: під'єднати джерело метрик (біржа для крипти,
   Yahoo/AlphaVantage для акцій) у `fundamental.py`.
3. **Rule Evolution (§9)**: оновлювати `RuleState` у БД за статистикою угод.
4. **Withdrawal Planner (§25)** та **Strategy Laboratory (§30)** — окремі модулі.
5. **Тести на реальному провайдері** (мережа доступна в Codex, тут її не було).

## Важливі застереження
- Система НЕ обіцяє прибутку. Синтетичні метрики (Sharpe тощо) нереалістично
  високі — на реальних даних будуть значно нижчі. Це очікувано й чесно.
- Сигнальна логіка проста (тренд+MACD+RSI+новини). Це база, не доведена
  перевага — її треба валідувати на живих даних перед реальними грошима.
- Перед реальними грошима — щонайменше кілька тижнів Paper на живих даних.

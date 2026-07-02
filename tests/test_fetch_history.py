"""Тест завантаження історії у CSV (§10, PLAN A1). Без мережі — фейковий провайдер."""
from core.data.providers import CsvProvider, SyntheticProvider
from scripts.fetch_history import fetch_and_save


def test_fetch_and_save_writes_readable_csv(tmp_path):
    provider = SyntheticProvider(seed=1, start_price=100)
    path = fetch_and_save("BTC/USDT", timeframe="1h", limit=50, outdir=tmp_path, provider=provider)

    assert path.exists()
    assert path.name == "BTC_USDT_1h.csv"

    # CSV має читатись назад тим самим провайдером, що й решта системи
    candles = CsvProvider(str(tmp_path)).fetch_ohlcv("BTC/USDT", "1h", limit=50)
    assert len(candles) == 50
    assert candles[0].close > 0


def test_fetch_and_save_creates_outdir(tmp_path):
    provider = SyntheticProvider(seed=2, start_price=50)
    outdir = tmp_path / "nested" / "data"
    path = fetch_and_save("ETH/USDT", timeframe="4h", limit=10, outdir=outdir, provider=provider)
    assert path.exists()

import pytest

from hft.bin import run


class _DummyConfig:
    """Minimal config object providing ccxt_instance."""

    def __init__(self, instance):
        self.ccxt_instance = instance


class _DummyCCXT:
    def __init__(self, price: float | None, should_raise: bool = False):
        self._price = price
        self._raise = should_raise

    async def fetch_ticker(self, symbol: str):
        if self._raise:
            raise RuntimeError("fetch failed")
        return {"last": self._price}


class _DummyExchange:
    def __init__(self, swap=None, spot=None, default=None):
        self.exchanges = {}
        if swap is not None:
            self.exchanges["swap"] = swap
        if spot is not None:
            self.exchanges["spot"] = spot
        self.config = _DummyConfig(default or spot or swap)


@pytest.mark.asyncio
async def test_fetch_prices_falls_back_to_spot():
    """Spot-only资产应走现货实例估值。"""
    swap = _DummyCCXT(price=None, should_raise=True)  # swap 查价失败
    spot = _DummyCCXT(price=1.23)  # spot 能查到价格
    exchange = _DummyExchange(swap=swap, spot=spot)

    balances = {
        "spot": {
            "ABC": {"total": 2},  # 非稳定币，需查价
        }
    }

    prices = await run._fetch_prices(exchange, balances)

    assert prices["ABC"] == pytest.approx(1.23)


@pytest.mark.asyncio
async def test_exchange_status_missing_config_message(capsys, monkeypatch):
    """缺失配置时应输出实际路径（f-string 生效）。"""

    def _raise_not_found(path: str):
        raise FileNotFoundError

    monkeypatch.setattr(run.BaseExchangeConfig, "load", classmethod(lambda cls, path: _raise_not_found(path)))

    await run.exchange_status_async("missing/path")
    out = capsys.readouterr().out

    assert "Exchange config not found: missing/path" in out
    assert "conf/exchange/missing/path.yaml" in out

import inspect

from hft.exchange.base import BaseExchange


def test_base_exchange_has_fetch_balance():
    assert hasattr(BaseExchange, "fetch_balance")
    assert inspect.iscoroutinefunction(BaseExchange.fetch_balance)


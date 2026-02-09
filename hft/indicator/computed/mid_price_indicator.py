"""
MidPrice 中间价指标

Feature 0006: Indicator 与 DataSource 统一架构

从 OrderBookDataSource 计算中间价。
"""
import time
from typing import Any, Optional

from ..base import BaseIndicator
from ..datasource.orderbook_datasource import OrderBookDataSource


class MidPriceIndicator(BaseIndicator):
    """
    中间价格指标

    从 OrderBookDataSource 计算中间价、最优买卖价和价差。
    通过 scope 获取 OrderBookDataSource 实例。
    """
    supported_scope = None

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self._cache_ttl: float = kwargs.get("cache_ttl", 5.0)
        self._cached_vars: Optional[dict[str, Any]] = None
        self._cache_ts: float = 0.0

    def _get_ob_ds(self) -> Optional[OrderBookDataSource]:
        """获取 OrderBook 数据源"""
        if self.root is None:
            return None
        return self.root.query_indicator(OrderBookDataSource, self.scope)

    def _compute_vars(self) -> Optional[dict[str, Any]]:
        """从 OrderBook 计算中间价变量"""
        ds = self._get_ob_ds()
        if ds is None or not ds.ready:
            return None

        ob = ds.data.get_data()
        if ob is None:
            return None

        best_bid = ob.best_bid
        best_ask = ob.best_ask
        mid = ob.mid_price
        spread = (best_ask - best_bid) if (best_bid and best_ask) else None

        return {
            "orderbook_mid_price": mid,
            "orderbook_best_bid": best_bid,
            "orderbook_best_ask": best_ask,
            "orderbook_spread": spread,
        }

    def _empty_vars(self) -> dict[str, Any]:
        return {
            "orderbook_mid_price": None,
            "orderbook_best_bid": None,
            "orderbook_best_ask": None,
            "orderbook_spread": None,
        }

    def get_vars(self) -> dict[str, Any]:
        """返回中间价变量，带缓存"""
        now = time.time()
        if self._cached_vars is not None and now - self._cache_ts < self._cache_ttl:
            return self._cached_vars

        result = self._compute_vars()
        if result is None:
            return self._empty_vars()

        self._cached_vars = result
        self._cache_ts = now
        return result

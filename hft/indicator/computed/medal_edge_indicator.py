"""
MedalEdge 指标

Feature 0006: Indicator 与 DataSource 统一架构

计算 taker 相对于 maker 的百分比优势。
原名 edge，重命名为 medal_edge 以更直观。
"""
import time
from dataclasses import dataclass
from typing import Any, Optional

from ..base import BaseIndicator
from ..datasource.trades_datasource import TradesDataSource


@dataclass
class MedalEdgeData:
    """MedalEdge 数据点"""
    buy_edge: float
    sell_edge: float


class MedalEdgeIndicator(BaseIndicator):
    """
    Medal Edge 指标

    计算 taker 相对于 maker 的百分比优势。

    公式（量纲无关）：
    - 买入：edge = (p_final - vwap_buy) / p_final - taker_fee
    - 卖出：edge = (vwap_sell - p_final) / p_final - taker_fee

    正值表示 taker 有优势，如 0.001 表示 0.1%
    """
    supported_scope = None

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self._window: float = kwargs.get("window", 60.0)
        self._taker_fee: float = kwargs.get("taker_fee", 0.0005)
        # 缓存
        self._cached: Optional[MedalEdgeData] = None
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 10.0

    def _get_trades_ds(self) -> Optional[TradesDataSource]:
        """获取 TradesDataSource"""
        if self.root is None:
            return None
        return self.root.query_indicator(TradesDataSource, self.scope)

    def _compute(self) -> Optional[MedalEdgeData]:
        """单次遍历计算 edge"""
        ds = self._get_trades_ds()
        if ds is None or not ds.ready:
            return None

        now = time.time()
        cutoff = now - self._window

        buy_qty = 0.0
        buy_notional = 0.0
        sell_qty = 0.0
        sell_notional = 0.0
        last_price = 0.0

        # 单次遍历 data_list: list[tuple[TradeData, float]]
        for trade, ts in ds.data.data_list:
            if ts < cutoff:
                continue
            amt = abs(trade.amount)
            cost = amt * trade.price
            if trade.amount > 0:  # buy
                buy_qty += amt
                buy_notional += cost
            else:  # sell
                sell_qty += amt
                sell_notional += cost
            last_price = trade.price

        if last_price <= 0:
            return None

        # 计算 buy edge
        if buy_qty > 0:
            vwap_buy = buy_notional / buy_qty
            buy_edge = (last_price - vwap_buy) / last_price - self._taker_fee
        else:
            buy_edge = 0.0

        # 计算 sell edge
        if sell_qty > 0:
            vwap_sell = sell_notional / sell_qty
            sell_edge = (vwap_sell - last_price) / last_price - self._taker_fee
        else:
            sell_edge = 0.0

        return MedalEdgeData(buy_edge=buy_edge, sell_edge=sell_edge)

    def get_vars(self) -> dict[str, Any]:
        """返回 medal_edge 变量，带缓存"""
        now = time.time()
        if self._cached is not None and now - self._cache_ts < self._cache_ttl:
            return {
                "medal_edge": self._cached.buy_edge,
                "medal_buy_edge": self._cached.buy_edge,
                "medal_sell_edge": self._cached.sell_edge,
            }

        result = self._compute()
        if result is None:
            return {
                "medal_edge": 0.0,
                "medal_buy_edge": 0.0,
                "medal_sell_edge": 0.0,
            }

        self._cached = result
        self._cache_ts = now
        return {
            "medal_edge": result.buy_edge,
            "medal_buy_edge": result.buy_edge,
            "medal_sell_edge": result.sell_edge,
        }

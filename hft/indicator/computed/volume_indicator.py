"""
Volume 成交量指标

Feature 0006: Indicator 与 DataSource 统一架构

从 TradesDataSource 计算窗口内的成交量统计。
"""
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from ..base import BaseIndicator
from ..datasource.trades_datasource import TradesDataSource

if TYPE_CHECKING:
    pass


@dataclass
class VolumeData:
    """成交量数据点"""
    volume: float
    buy_volume: float
    sell_volume: float
    volume_notional: float
    buy_volume_notional: float
    sell_volume_notional: float


class VolumeIndicator(BaseIndicator):
    """
    成交量指标

    从 TradesDataSource 计算窗口内的成交量。
    通过 scope 获取 TradesDataSource 实例，单次遍历聚合。
    """
    supported_scope = None  # 由 flow 配置决定

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self._window: float = kwargs.get("window", 300.0)
        # 缓存
        self._cached: Optional[VolumeData] = None
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 10.0

    def _get_trades_ds(self) -> Optional[TradesDataSource]:
        """获取 TradesDataSource"""
        if self.root is None:
            return None
        return self.root.query_indicator(TradesDataSource, self.scope)

    def _compute(self) -> Optional[VolumeData]:
        """单次遍历计算成交量"""
        ds = self._get_trades_ds()
        if ds is None or not ds.ready:
            return None

        now = time.time()
        cutoff = now - self._window

        buy_vol = 0.0
        sell_vol = 0.0
        buy_notional = 0.0
        sell_notional = 0.0

        # 单次遍历 data_list: list[tuple[TradeData, float]]
        for trade, ts in ds.data.data_list:
            if ts < cutoff:
                continue
            amt = abs(trade.amount)
            cost = amt * trade.price
            if trade.amount > 0:  # buy
                buy_vol += amt
                buy_notional += cost
            else:  # sell
                sell_vol += amt
                sell_notional += cost

        return VolumeData(
            volume=buy_vol + sell_vol,
            buy_volume=buy_vol,
            sell_volume=sell_vol,
            volume_notional=buy_notional + sell_notional,
            buy_volume_notional=buy_notional,
            sell_volume_notional=sell_notional,
        )

    def _to_vars(self, d: VolumeData) -> dict[str, Any]:
        return {
            "volume": d.volume,
            "buy_volume": d.buy_volume,
            "sell_volume": d.sell_volume,
            "volume_notional": d.volume_notional,
            "buy_volume_notional": d.buy_volume_notional,
            "sell_volume_notional": d.sell_volume_notional,
        }

    def _empty_vars(self) -> dict[str, Any]:
        return {
            "volume": 0.0,
            "buy_volume": 0.0,
            "sell_volume": 0.0,
            "volume_notional": 0.0,
            "buy_volume_notional": 0.0,
            "sell_volume_notional": 0.0,
        }

    def get_vars(self) -> dict[str, Any]:
        """返回成交量变量，带缓存"""
        now = time.time()
        if self._cached is not None and now - self._cache_ts < self._cache_ttl:
            return self._to_vars(self._cached)

        result = self._compute()
        if result is None:
            return self._empty_vars()

        self._cached = result
        self._cache_ts = now
        return self._to_vars(result)

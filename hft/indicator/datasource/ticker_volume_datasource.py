"""
Ticker 交易量数据源

基于 Global/Local 模式：
- GlobalExchangeTickerVolumeIndicator: ExchangeClass 级别，批量获取所有交易对的24h交易量
- TickerVolumeIndicator: TradingPairClass 级别，从全局指标提取本交易对数据
"""
import time
from collections import defaultdict
from functools import cached_property
from typing import Any

from ...core.healthy_data import HealthyDataArray
from ...core.scope.scopes import ExchangeClassScope, TradingPairClassScope
from ..base import BaseExchangeClassDataIndicator, BaseTradingPairClassDataIndicator


class GlobalExchangeTickerVolumeIndicator(BaseExchangeClassDataIndicator[dict[str, float]]):
    """
    全局交易量指标（ExchangeClass 级别）

    每个 exchange_class 调用一次 medal_fetch_ticker_volumes()，
    批量获取所有交易对的24h交易量，避免逐个请求。
    """
    DEFAULT_DISABLE_SECONDS = None  # 永不自动禁用

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.volumes: defaultdict[str, HealthyDataArray[float]] = defaultdict(self.create_healthy_data_array)

    @property
    def interval(self) -> float:
        return 10.0  # exchange 层有 60 秒缓存，这里频繁调用只是读缓存

    async def on_tick(self) -> bool:
        """定时批量获取交易量"""
        if not self.exchange.ready:
            return False
        volumes = await self.exchange.medal_fetch_ticker_volumes()
        await self.data.update(volumes)
        ts = time.time()
        for symbol, volume in volumes.items():
            await self.volumes[symbol].update(volume, ts)

    def get_vars(self) -> dict[str, Any]:
        """返回全部交易量字典"""
        return {"ticker_volumes": self.data.data_list}


class TickerVolumeIndicator(BaseTradingPairClassDataIndicator[float]):
    """
    交易对级交易量指标（TradingPairClass 级别）

    从 GlobalExchangeTickerVolumeIndicator 获取本交易对的24h交易量。

    提供变量：
    - ticker_volume: 当前24h交易量
    - ticker_volume_history: 交易量历史数组
    """
    DEFAULT_IS_ARRAY = None
    disable_tick = True  # 不需要定时器，数据来自全局指标
    __pickle_exclude__ = {*BaseTradingPairClassDataIndicator.__pickle_exclude__, "global_indicator"}
    supported_scope = TradingPairClassScope

    @property
    def ready(self) -> bool:
        return hasattr(self, 'get_data') and self.get_data.is_healthy

    @property
    def interval(self):
        return None

    @cached_property
    def global_indicator(self) -> GlobalExchangeTickerVolumeIndicator:
        app_core = self.root
        global_scope = self.scope.search_prev_scope(ExchangeClassScope)
        return app_core.query_indicator(self.global_id, global_scope)

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self.global_id = kwargs["global_id"]

    async def on_tick(self):
        return True

    @property
    def get_data(self) -> HealthyDataArray[float]:
        return self.global_indicator.volumes[self.symbol]

    def get_vars(self) -> dict[str, Any]:
        """返回本交易对的交易量变量"""
        result = {"ticker_volume_history": self.get_data.data_list}
        data = self.get_data.get_data()
        if data is not None:
            result["ticker_volume"] = data
            return result
        else:
            raise ValueError("Ticker volume is not available")

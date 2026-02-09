"""
资金费率数据源

Feature 0006: Indicator 与 DataSource 统一架构

- GlobalFundingRateIndicator: ExchangeClass 级别，批量获取所有交易对的资金费率
- FundingRateIndicator: TradingPairClass 级别，从全局指标提取本交易对数据
"""
import time
from functools import cached_property
from typing import TYPE_CHECKING, Any, Optional

from ...plugin import pm
from ..base import BaseIndicator
from ..datasource.base import BaseDataSource
from ...core.scope.scopes import ExchangeClassScope, TradingPairClassScope

if TYPE_CHECKING:
    from ...exchange.base import BaseExchange, FundingRate


class GlobalFundingRateIndicator(BaseDataSource):
    """
    全局资金费率指标（ExchangeClass 级别）

    每个 exchange_class 只调用一次 medal_fetch_funding_rates()，
    批量获取所有交易对的资金费率，避免逐个请求。

    数据存储在 self.data (HealthyDataArray) 中，
    FundingRateIndicator 通过查询本实例获取数据。
    """
    supported_scope = ExchangeClassScope
    DEFAULT_IS_ARRAY = False  # 单值：最新的 funding_rates dict
    DEFAULT_MAX_AGE = 60.0

    __pickle_exclude__ = {*BaseDataSource.__pickle_exclude__, "exchange"}

    @property
    def interval(self) -> float:
        return 3.0

    @cached_property
    def exchange(self) -> 'BaseExchange':
        """获取交易所实例（取该 class 下第一个）"""
        exchange_class = self.scope.get_var("exchange_class")
        exchange_group = self.root.exchange_group
        exchange_path = next(iter(exchange_group.exchange_group[exchange_class]))
        return exchange_group.exchange_instances[exchange_path]

    async def on_tick(self) -> bool:
        """定时批量获取资金费率"""
        if not self.exchange.ready:
            return False

        try:
            funding_rates = await self.exchange.medal_fetch_funding_rates()
        except Exception as e:
            self.logger.warning(
                "Failed to fetch funding rates: %s", e
            )
            return False

        if not funding_rates:
            return False

        # 存储到 data
        await self.data.update(funding_rates)

        # 通过 event 分发给 FundingRateIndicator
        self.event.emit("update", time.time(), funding_rates)

        # 插件钩子
        for symbol, fr in funding_rates.items():
            pm.hook.on_funding_rate_update(
                exchange=self.exchange,
                symbol=symbol,
                funding_rate=fr,
            )

        return False

    def get_vars(self) -> dict[str, Any]:
        """返回全部资金费率字典"""
        data = self.data.get_data()
        if data is None:
            return {"funding_rates": {}}
        return {"funding_rates": data}


class FundingRateIndicator(BaseIndicator):
    """
    交易对级资金费率指标（TradingPairClass 级别）

    从 GlobalFundingRateIndicator 获取本交易对的资金费率。
    通过事件订阅实现实时更新，避免每个交易对单独请求 API。
    """
    supported_scope = TradingPairClassScope
    disable_tick = True  # 事件驱动，不需要 tick

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self._latest_fr: Optional['FundingRate'] = None
        self._latest_ts: float = 0.0
        self._subscribed = False

    async def on_start(self) -> None:
        """启动时订阅全局资金费率指标的 update 事件"""
        await super().on_start()
        self._subscribe_global()

    def _subscribe_global(self) -> None:
        """订阅 GlobalFundingRateIndicator"""
        if self._subscribed:
            return
        global_indicator = self.root.query_indicator(
            GlobalFundingRateIndicator, self.scope,
        )
        if global_indicator is None:
            return
        global_indicator.event.on("update", self._on_global_update)
        self._subscribed = True

    def _on_global_update(
        self,
        timestamp: float,
        funding_rates: dict[str, 'FundingRate'],
    ) -> None:
        """处理全局资金费率更新，提取本交易对数据"""
        symbol = self.scope.get_var("symbol")
        fr = funding_rates.get(symbol)
        if fr is None:
            return
        self._latest_fr = fr
        self._latest_ts = timestamp

    def get_vars(self) -> dict[str, Any]:
        """返回本交易对的资金费率变量"""
        fr = self._latest_fr
        if fr is None:
            return {
                "funding_rate": None,
                "daily_funding_rate": 0.0,
                "base_funding_rate": 0.0,
            }
        return {
            "funding_rate": fr,
            "daily_funding_rate": fr.daily_funding_rate,
            "base_funding_rate": fr.base_funding_rate,
            "index_price": fr.index_price,
            "mark_price": fr.mark_price,
        }

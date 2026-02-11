"""
资金费率数据源

Feature 0006: Indicator 与 DataSource 统一架构

- GlobalFundingRateIndicator: ExchangeClass 级别，批量获取所有交易对的资金费率
- FundingRateIndicator: TradingPairClass 级别，从全局指标提取本交易对数据
"""
from dataclasses import dataclass
from functools import cached_property
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Optional
from ...core.healthy_data import HealthyData, HealthyDataArray
from ...core.scope.scopes import ExchangeClassScope, TradingPairClassScope
from ..base import BaseExchangeClassDataIndicator, BaseTradingPairClassDataIndicator, T
if TYPE_CHECKING:
    from ...exchange.base import BaseExchange, FundingRate


@dataclass
class FundingRateMeta:
    """资金费率元数据, 仅使用最近值"""
    exchange: str
    symbol: str
    timestamp: float                    # 数据时间戳
    expiry: Optional[float]             # 到期时间
    base_funding_rate: float            # 基础资金费率
    next_funding_timestamp: float       # 下次结算时间戳
    funding_interval_hours: int         # 结算间隔（小时）
    minimum_funding_rate: float = -0.03     # 最小资金费率
    maximum_funding_rate: float = 0.03      # 最大资金费率


class GlobalExchangeFundingRateIndicator(BaseExchangeClassDataIndicator[dict[str, 'FundingRate']]):
    """
    资金费率指标

    每个 exchange_class 调用一次 medal_fetch_funding_rates()，
    批量获取所有交易对的资金费率，避免逐个请求。

    数据存储在 self.data (HealthyDataArray) 中，
    FundingRateIndicator 通过查询本实例获取数据。
    """
    DEFAULT_DISABLE_SECONDS = None  # 永不自动禁用

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.metas: defaultdict[str, HealthyData[FundingRateMeta]] = defaultdict(self.create_healthy_data)
        self.funding_rates: defaultdict[str, HealthyDataArray[float]] = defaultdict(self.create_healthy_data_array)
        self.index_prices: defaultdict[str, HealthyDataArray[float]] = defaultdict(self.create_healthy_data_array)
        self.mark_prices: defaultdict[str, HealthyDataArray[float]] = defaultdict(self.create_healthy_data_array)

    @property
    def interval(self) -> float:
        return 3.0

    async def on_tick(self) -> bool:
        """定时批量获取资金费率"""
        if not self.exchange.ready:
            return False
        funding_rates = await self.exchange.medal_fetch_funding_rates()
        # 存储到 data
        await self.data.update(funding_rates)
        for symbol, fr in funding_rates.items():
            # 存储 meta
            meta = FundingRateMeta(
                exchange=fr.exchange,
                symbol=fr.symbol,
                timestamp=fr.timestamp,
                expiry=fr.expiry,
                base_funding_rate=fr.base_funding_rate,
                next_funding_timestamp=fr.next_funding_timestamp,
                funding_interval_hours=fr.funding_interval_hours,
                minimum_funding_rate=fr.minimum_funding_rate,
                maximum_funding_rate=fr.maximum_funding_rate,
            )
            await self.metas[symbol].update(meta, fr.timestamp)
            # 存储 funding rate 历史
            await self.funding_rates[symbol].update(fr.next_funding_rate, fr.timestamp)
            # 存储 index price 历史
            await self.index_prices[symbol].update(fr.index_price, fr.index_price_timestamp)
            # 存储 mark price 历史
            await self.mark_prices[symbol].update(fr.mark_price, fr.mark_price_timestamp)
        # 通过 event 分发给 FundingRateIndicator
        # self.event.emit("update", time.time(), funding_rates)

    def get_vars(self) -> dict[str, Any]:
        """返回全部资金费率字典"""
        return {"funding_rates_history": self.data.data_list}


class LocalTradingPairFundingRateIndicator(BaseTradingPairClassDataIndicator[T]):
    """
    交易对级资金费率指标（TradingPairClass 级别）

    从 GlobalFundingRateIndicator 获取本交易对的资金费率。
    通过事件订阅实现实时更新，避免每个交易对单独请求 API。
    """
    disable_tick = True  # 不需要定时器
    __pickle_exclude__ = {*BaseTradingPairClassDataIndicator.__pickle_exclude__, "global_indicator"}
    supported_scope = TradingPairClassScope

    @property
    def interval(self):
        return None

    @cached_property
    def global_indicator(self) -> GlobalExchangeFundingRateIndicator:
        app_core = self.root
        global_scope = self.scope.search_prev_scope(ExchangeClassScope)
        return app_core.query_indicator(self.global_id, global_scope)

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self.global_id = kwargs["global_id"]

    async def on_tick(self):
        return True


class FundingRateMetaIndicator(LocalTradingPairFundingRateIndicator[FundingRateMeta]):
    """
    交易对级资金费率元数据指标

    从 GlobalFundingRateIndicator 获取本交易对的资金费率元数据。
    """
    # TODO: 对现货交易对，应该返回默认值

    @property
    def get_data(self) -> HealthyData[FundingRateMeta]:
        return self.global_indicator.metas[self.symbol]

    def get_vars(self) -> dict[str, Any]:
        """返回本交易对的资金费率元数据变量"""
        result = {}
        data = self.get_data.get_data()
        if data is not None:
            result.update({
                "funding_rate_meta": data,
                "funding_rate_base": data.base_funding_rate,
                "funding_rate_next_timestamp": data.next_funding_timestamp,
                "funding_rate_expiry": data.expiry,
                "funding_rate_minimum": data.minimum_funding_rate,
                "funding_rate_maximum": data.maximum_funding_rate,
            })
        return result

class FundingRateIndicator(LocalTradingPairFundingRateIndicator):

    @property
    def get_data(self) -> HealthyDataArray[float]:
        return self.global_indicator.funding_rates[self.symbol]

    def get_vars(self) -> dict[str, Any]:
        """返回本交易对的资金费率变量"""
        result = {"funding_rate_history": self.get_data.data_list}
        data = self.get_data.get_data()
        if data is not None:
            result.update({
                "funding_rate": data,
            })
        return result


class IndexPriceIndicator(LocalTradingPairFundingRateIndicator):

    @property
    def get_data(self) -> HealthyDataArray[float]:
        return self.global_indicator.index_prices[self.symbol]

    def get_vars(self) -> dict[str, Any]:
        """返回本交易对的指数价格变量"""
        result = {"index_price_history": self.get_data.data_list}
        data = self.get_data.get_data()
        if data is not None:
            result.update({
                "index_price": data,
            })
        return result


class MarkPriceIndicator(LocalTradingPairFundingRateIndicator):

    @property
    def get_data(self) -> HealthyDataArray[float]:
        return self.global_indicator.mark_prices[self.symbol]

    def get_vars(self) -> dict[str, Any]:
        """返回本交易对的标记价格变量"""
        result = {"mark_price_history": self.get_data.data_list}
        data = self.get_data.get_data()
        if data is not None:
            result.update({
                "mark_price": data,
            })
        return result

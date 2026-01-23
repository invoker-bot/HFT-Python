"""
资金费率数据源

Feature 0007: 移除 DataSourceGroup

将 GlobalFundingRateFetcher 迁移到 IndicatorGroup 架构：
- GlobalFundingRateIndicator: 全局资金费率指标，定时获取所有交易对的资金费率
- FundingRateIndicator: 交易对级资金费率指标，监听全局指标的 update 事件
"""
import time
from typing import TYPE_CHECKING, Any, Optional

from ...plugin import pm
from ..base import BaseIndicator, GlobalIndicator
from ..persist import FundingRatePersistListener

if TYPE_CHECKING:
    from ...exchange.base import BaseExchange, FundingRate


class GlobalFundingRateIndicator(GlobalIndicator[dict[str, "FundingRate"]]):
    """
    全局资金费率指标

    定时获取所有交易对的资金费率，通过事件分发到各个 FundingRateIndicator。

    特点：
    - 每个 exchange_class 只调用一次 medal_fetch_funding_rates()
    - 将结果通过 update 事件分发
    - 同时持久化到 ClickHouse 数据库
    """

    __pickle_exclude__ = (*GlobalIndicator.__pickle_exclude__, "_persist_listener")

    def __init__(
        self,
        exchange_class: str,
        interval: float = 3.0,
        window: float = 300.0,
    ):
        """
        Args:
            exchange_class: 交易所类名（如 "okx"）
            interval: 获取间隔（秒）
            window: 数据窗口大小（秒）
        """
        name = f"global_funding_rate:{exchange_class}"
        super().__init__(
            name=name,
            window=window,
            ready_condition=None,  # 有数据即 ready
            interval=interval,
        )
        self._exchange_class = exchange_class
        self._persist_listener: Optional[FundingRatePersistListener] = None

    @property
    def exchange_class(self) -> str:
        return self._exchange_class

    @property
    def exchange(self) -> Optional["BaseExchange"]:
        """获取交易所实例"""
        if self.root is None:
            return None
        exchange_group = getattr(self.root, 'exchange_group', None)
        if exchange_group is None:
            return None
        return exchange_group.get_exchange_by_class(self._exchange_class)

    async def on_start(self) -> None:
        """启动时创建持久化子 Listener"""
        await super().on_start()
        self._persist_listener = FundingRatePersistListener()
        self.add_child(self._persist_listener)

    async def on_tick(self) -> bool:
        """定时获取资金费率并分发"""
        exchange = self.exchange
        if exchange is None or not exchange.ready:
            return False

        try:
            await self._fetch_and_distribute(exchange)
        except Exception as e:
            self.logger.warning(
                "Failed to fetch funding rates for %s: %s",
                self._exchange_class, e
            )
            self._emit_error(e)

        return False

    async def _fetch_and_distribute(self, exchange: "BaseExchange") -> None:
        """获取并分发资金费率"""
        # 获取所有交易对的资金费率
        funding_rates = await exchange.medal_fetch_funding_rates()

        if not funding_rates:
            return

        # 存储到 _data
        now = time.time()
        self._data.append(now, funding_rates)

        # 发出 update 事件，供 FundingRateIndicator 监听
        self._emit_update(now, funding_rates)

        # 插件钩子：资金费率更新
        for symbol, funding_rate in funding_rates.items():
            pm.hook.on_funding_rate_update(
                exchange=exchange,
                symbol=symbol,
                funding_rate=funding_rate
            )

        # 持久化到数据库
        persisted_count = 0
        if self._persist_listener is not None:
            persisted_count = await self._persist_listener.persist(
                self._exchange_class, funding_rates
            )

        self.logger.debug(
            "Fetched %d funding rates for %s, persisted %d",
            len(funding_rates), self._exchange_class, persisted_count
        )

    def calculate_vars(self, direction: int) -> dict[str, Any]:
        """返回变量字典"""
        latest = self._data.latest
        if latest is None:
            return {"funding_rates": {}}
        return {"funding_rates": latest}

    @property
    def log_state_dict(self) -> dict:
        return {
            **super().log_state_dict,
            "exchange_class": self._exchange_class,
            "data_count": len(self._data),
        }


class FundingRateIndicator(BaseIndicator["FundingRate"]):
    """
    交易对级资金费率指标

    监听 GlobalFundingRateIndicator 的 update 事件，提取本交易对的数据。

    特点：
    - 事件驱动（interval=None）
    - 自动订阅对应 exchange_class 的全局资金费率指标
    """

    def __init__(
        self,
        exchange_class: str,
        symbol: str,
        window: float = 300.0,
    ):
        """
        Args:
            exchange_class: 交易所类名
            symbol: 交易对
            window: 数据窗口大小（秒）
        """
        name = f"funding_rate:{exchange_class}:{symbol}"
        super().__init__(
            name=name,
            window=window,
            ready_condition=None,  # 有数据即 ready
            interval=None,  # 事件驱动
        )
        self._exchange_class = exchange_class
        self._symbol = symbol
        self._subscribed = False

    @property
    def exchange_class(self) -> str:
        return self._exchange_class

    @property
    def symbol(self) -> str:
        return self._symbol

    async def on_start(self) -> None:
        """启动时订阅全局资金费率指标的 update 事件"""
        await super().on_start()
        self._subscribe_global()

    def _subscribe_global(self) -> None:
        """订阅全局资金费率指标"""
        if self._subscribed:
            return

        indicator_group = getattr(self.root, 'indicator_group', None)
        if indicator_group is None:
            self.logger.warning("indicator_group not found, cannot subscribe")
            return

        global_id = f"global_funding_rate:{self._exchange_class}"
        global_indicator = indicator_group.get_indicator(global_id, None, None)

        if global_indicator is None:
            self.logger.warning(
                "GlobalFundingRateIndicator not found: %s", global_id
            )
            return

        global_indicator.on("update", self._on_global_update)
        self._subscribed = True
        self.logger.debug("Subscribed to %s", global_id)

    def _on_global_update(
        self,
        timestamp: float,
        funding_rates: dict[str, "FundingRate"]
    ) -> None:
        """处理全局资金费率更新"""
        fr = funding_rates.get(self._symbol)
        if fr is None:
            return

        self._data.append(timestamp, fr)
        self._emit_update(timestamp, fr)

    def calculate_vars(self, direction: int) -> dict[str, Any]:
        """返回变量字典"""
        latest = self._data.latest
        if latest is None:
            return {
                "funding_rate": None,
                "daily_funding_rate": 0.0,
                "base_funding_rate": 0.0,
            }
        return {
            "funding_rate": latest,
            "daily_funding_rate": latest.daily_funding_rate,
            "base_funding_rate": latest.base_funding_rate,
            "index_price": latest.index_price,
            "mark_price": latest.mark_price,
        }

    @property
    def log_state_dict(self) -> dict:
        return {
            **super().log_state_dict,
            "exchange_class": self._exchange_class,
            "symbol": self._symbol,
            "subscribed": self._subscribed,
        }

"""
GlobalFundingRateFetcher - 全局资金费率获取器

挂载在 DataSourceGroup 上，定时获取所有交易对的资金费率：
- 每个 exchange_class 只调用一次 medal_fetch_funding_rates()
- 将结果分发到各个 TradingPairDataSource 的 FundingRateDataSource
- 同时持久化到 ClickHouse 数据库（通过 FundingRatePersistListener 子节点）

设计理念：
- 资金费率 API 一次返回所有交易对数据，避免重复调用
- 集中获取、分散存储
- 作为 DataSourceGroup 的子节点，享受统一生命周期管理
"""
import asyncio
from typing import TYPE_CHECKING, Optional
from ..core.listener import Listener
from ..plugin import pm

if TYPE_CHECKING:
    from .group import DataSourceGroup
    from ..exchange.base import BaseExchange
    from ..database.listeners import FundingRatePersistListener


class GlobalFundingRateFetcher(Listener):
    """
    全局资金费率获取器

    工作流程：
    1. on_tick() 被调用
    2. 遍历所有 exchange（每个 exchange_class 只处理一次）
    3. 调用 exchange.medal_fetch_funding_rates() 获取所有交易对费率
    4. 将结果分发到对应的 TradingPairDataSource

    配置：
    - interval: 获取间隔（默认 3 秒）

    使用示例：
        # 由 DataSourceGroup 自动创建和管理
        datasource_group.query("okx", "BTC/USDT:USDT", DataType.FUNDING_RATE)
        # 或直接获取
        pair = datasource_group.get_trading_pair("okx", "BTC/USDT:USDT")
        funding = pair.funding_rate_datasource.get_current()
    """

    __pickle_exclude__ = (*Listener.__pickle_exclude__, "_persist_listener")

    def __init__(self, interval: float = 3.0):
        super().__init__(name="GlobalFundingRateFetcher", interval=interval)
        self._processed_classes: set[str] = set()  # 每个 tick 周期内已处理的 exchange_class
        self._persist_listener: Optional["FundingRatePersistListener"] = None

    async def on_start(self) -> None:
        """启动时创建持久化子 Listener"""
        from ..database.listeners import FundingRatePersistListener

        self._persist_listener = FundingRatePersistListener()
        self.add_child(self._persist_listener)

    @property
    def datasource_group(self) -> "DataSourceGroup":
        """获取父节点 DataSourceGroup"""
        return self.parent

    @property
    def exchange_group(self):
        """获取 ExchangeGroup"""
        return self.datasource_group.exchange_group

    async def on_tick(self) -> bool:
        """定时获取资金费率并分发"""
        self._processed_classes.clear()

        # 遍历所有交易所
        for exchange in self.exchange_group.children.values():
            if not exchange.ready:
                continue

            exchange_class = exchange.class_name

            # 每个 exchange_class 只处理一次
            if exchange_class in self._processed_classes:
                continue
            self._processed_classes.add(exchange_class)

            try:
                await self._fetch_and_distribute(exchange)
            except Exception as e:
                self.logger.warning(
                    "Failed to fetch funding rates for %s: %s",
                    exchange_class, e
                )

        return False

    async def _fetch_and_distribute(self, exchange: "BaseExchange") -> None:
        """获取并分发资金费率"""
        # 获取所有交易对的资金费率
        funding_rates = await exchange.medal_fetch_funding_rates()

        if not funding_rates:
            return

        exchange_class = exchange.class_name
        distributed_count = 0

        # 分发到各个 TradingPairDataSource
        for symbol, funding_rate in funding_rates.items():
            child_name = f"{exchange_class}:{symbol}"

            # 查找对应的 TradingPairDataSource
            if child_name not in self.datasource_group.children:
                continue  # 该交易对未被创建，跳过

            pair = self.datasource_group.children[child_name]

            # 确保 FundingRateDataSource 存在并填充数据
            pair.ensure_funding_rate_datasource()
            pair.funding_rate_datasource.append(funding_rate)
            distributed_count += 1

            # 插件钩子：资金费率更新
            pm.hook.on_funding_rate_update(
                exchange=exchange,
                symbol=symbol,
                funding_rate=funding_rate
            )

        # 持久化到数据库
        persisted_count = 0
        if self._persist_listener is not None:
            persisted_count = await self._persist_listener.persist(
                exchange_class, funding_rates
            )

        self.logger.debug(
            "Fetched %d funding rates for %s, distributed to %d pairs, persisted %d",
            len(funding_rates), exchange_class, distributed_count, persisted_count
        )

    @property
    def log_state_dict(self) -> dict:
        return {
            **super().log_state_dict,
            "processed_classes": list(self._processed_classes),
        }

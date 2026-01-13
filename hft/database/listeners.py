"""
数据模型和监听器模块

定义数据采集监听器，自动从交易所获取数据并保存到 ClickHouse。

监听器类型：
- ExchangeFundingRateBillListener: 资金费率账单采集
- ExchangeBalanceUsdListener: 账户余额快照采集
- FundingRatePersistListener: 资金费率快照持久化（挂载到 GlobalFundingRateFetcher）
"""
from typing import TYPE_CHECKING, Optional
from ..core.listener import Listener
from .client import ClickHouseDatabase, FundingRateBillController, FundingRateController, BalanceUSDController
if TYPE_CHECKING:
    from ..exchange.base import BaseExchange, FundingRate


class DataListener(Listener):
    """
    数据采集监听器基类

    提供数据库访问和持久化配置检查功能。
    子类需要指定 persist_key 属性来关联持久化配置项。
    """
    __pickle_exclude__ = (*Listener.__pickle_exclude__, "db")
    persist_key: str = ""  # 子类覆盖，对应 PersistConfig 中的字段名

    def __init__(self, interval: float = 300.0):
        """
        初始化数据监听器

        Args:
            interval: 采集间隔（秒），默认 5 分钟
        """
        super().__init__(self.__class__.__name__, interval)
        self.db: Optional[ClickHouseDatabase] = None  # type: ignore

    @property
    def db_ready(self) -> bool:
        """检查数据库是否就绪（已配置且已初始化）"""
        # 惰性获取 DB：支持 lazy_start 的 Listener 在 on_start 未调用时也能访问 DB
        if self.db is None:
            self.db = getattr(self.root, 'database', None)
        return self.db is not None and self.db.client is not None

    @property
    def persist_enabled(self) -> bool:
        """检查当前数据类型是否启用持久化"""
        if not self.persist_key:
            return True  # 未指定 persist_key 时默认启用
        persist_config = self.root.config.persist  # type: ignore
        return getattr(persist_config, self.persist_key, True)

    async def on_start(self):
        await super().on_start()
        self.db = self.root.database  # type: ignore

    def on_reload(self, state):
        super().on_reload(state)
        self.db = None


class ExchangeFundingRateBillListener(DataListener):
    """
    资金费率账单监听器

    定期从交易所获取资金费率账单，保存到 ClickHouse 的 funding_rate_bill 表。
    需要挂载到 BaseExchange 实例下作为子监听器。

    表结构:
        - id: 唯一标识 (exchange_name-原始id)
        - exchange_name: 交易所类名
        - exchange_path: 交易所配置路径
        - trading_pair: 交易对
        - funding_profit: 资金费收益
        - timestamp: 时间戳
    """
    persist_key = "funding_rate_bill"

    async def on_tick(self):
        """
        定时回调：获取并保存资金费率账单

        仅当父交易所处于就绪状态且启用持久化时执行。
        """
        parent: 'BaseExchange' = self.parent

        # 检查交易所就绪、数据库就绪、持久化启用
        if not parent.ready or not self.db_ready or not self.persist_enabled:
            return
        controller = FundingRateBillController(self.db)
        # 从交易所获取资金费率账单
        bills = await parent.medal_fetch_funding_rates_history()
        if not bills:
            return
        await controller.update(bills, parent)


class ExchangeBalanceUsdListener(DataListener):
    """
    账户余额快照监听器

    定期获取账户余额和持仓价值，保存到 ClickHouse 的 balance_usd 表。
    需要挂载到 BaseExchange 实例下作为子监听器。

    表结构:
        - timestamp: 时间戳
        - exchange_name: 交易所类名
        - exchange_path: 交易所配置路径
        - position_usd: 持仓价值（美元）
        - balance_usd: 账户余额（美元）
    """
    persist_key = "balance_usd"

    async def on_tick(self):
        """
        定时回调：获取并保存余额快照

        仅当父交易所处于就绪状态且启用持久化时执行。
        """
        parent: 'BaseExchange' = self.parent

        # 检查交易所就绪、数据库就绪、持久化启用
        if not parent.ready or not self.db_ready or not self.persist_enabled:
            return

        # 获取余额和持仓
        balance_usd = await parent.medal_fetch_total_balance_usd()
        positions = await parent.medal_fetch_positions()

        # 计算持仓总价值（取绝对值）
        position_usd = sum(abs(pos) for pos in positions.values())
        controller = BalanceUSDController(self.db)
        await controller.update(position_usd, balance_usd, parent)


class FundingRatePersistListener(DataListener):
    """
    资金费率持久化监听器

    挂载到 GlobalFundingRateFetcher 下，将获取的资金费率数据持久化到 ClickHouse。
    不独立运行 tick，而是由 GlobalFundingRateFetcher 调用 persist() 方法。

    表结构:
        - timestamp: 时间戳
        - exchange_name: 交易所类名
        - trading_pair: 交易对
        - index_price: 指数价格
        - mark_price: 标记价格
        - funding_rate: 当前资金费率
        - daily_funding_rate: 日化资金费率
    """
    persist_key = "funding_rate"
    lazy_start = True  # 不独立 tick，由父节点调用

    def __init__(self):
        super().__init__(interval=0)  # interval=0 表示不独立运行

    async def on_tick(self):
        """不独立运行，由父节点调用 persist() 方法"""
        pass

    async def persist(
        self,
        exchange_class: str,
        funding_rates: dict[str, "FundingRate"]
    ) -> int:
        """
        持久化资金费率数据

        由 GlobalFundingRateFetcher 在获取数据后调用。

        Args:
            exchange_class: 交易所类名（如 "okx"）
            funding_rates: {symbol: FundingRate} 字典

        Returns:
            成功写入的记录数
        """
        if not self.db_ready or not self.persist_enabled:
            return 0

        controller = FundingRateController(self.db)
        count = 0

        for symbol, fr in funding_rates.items():
            try:
                await controller.update(
                    exchange_name=exchange_class,
                    trading_pair=symbol,
                    index_price=fr.index_price,
                    mark_price=fr.mark_price,
                    funding_rate=fr.base_funding_rate,
                    daily_funding_rate=fr.daily_funding_rate,
                )
                count += 1
            except Exception as e:
                self.logger.warning(
                    "Failed to persist funding rate for %s:%s: %s",
                    exchange_class, symbol, e
                )

        return count

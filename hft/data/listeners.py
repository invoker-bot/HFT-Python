"""
数据模型和监听器模块

定义数据采集监听器，自动从交易所获取数据并保存到 ClickHouse。

监听器类型：
- ExchangeFundingRateBillListener: 资金费率账单采集
- ExchangeBalanceUsdListener: 账户余额快照采集
"""
from typing import TYPE_CHECKING, Optional
from ..core.listener import Listener
from .database import ClickHouseDatabase, FundingRateBillController, BalanceUSDController
if TYPE_CHECKING:
    from ..exchange.base import BaseExchange


class DataListener(Listener):
    __pickle_exclude__ = (*Listener.__pickle_exclude__, "db")

    def __init__(self, interval: float = 300.0):
        """
        初始化数据监听器

        Args:
            interval: 采集间隔（秒），默认 5 分钟
        """
        super().__init__(self.__class__.__name__, interval)
        self.db: Optional[ClickHouseDatabase] = None  # type: ignore

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

    async def on_tick(self):
        """
        定时回调：获取并保存资金费率账单

        仅当父交易所处于就绪状态时执行。
        """
        parent: 'BaseExchange' = self.parent

        # 只有当交易所准备好时才执行
        if not parent.ready or not self.db:
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

    async def on_tick(self):
        """
        定时回调：获取并保存余额快照

        仅当父交易所处于就绪状态时执行。
        """
        parent: 'BaseExchange' = self.parent

        # 只有当交易所准备好时才执行
        if not parent.ready or not self.db:
            return

        # 获取余额和持仓
        balance_usd = await parent.medal_fetch_total_balance_usd()
        positions = await parent.medal_fetch_positions()

        # 计算持仓总价值（取绝对值）
        position_usd = sum(abs(pos) for pos in positions.values())
        controller = BalanceUSDController(self.db)
        await controller.update(position_usd, balance_usd, parent)

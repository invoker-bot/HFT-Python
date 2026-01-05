"""
数据模型和监听器模块

定义数据采集监听器，自动从交易所获取数据并保存到 ClickHouse。

监听器类型：
- ExchangeFundingRateBillListener: 资金费率账单采集
- ExchangeBalanceUsdListener: 账户余额快照采集
"""
from datetime import datetime
from typing import TYPE_CHECKING
from ..core.listener import Listener

if TYPE_CHECKING:
    from ..core.app import AppCore
    from ..exchange.base import BaseExchange


class ExchangeFundingRateBillListener(Listener):
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

    def __init__(self, interval: float = 300.0):
        """
        初始化资金费率账单监听器

        Args:
            interval: 采集间隔（秒），默认 5 分钟
        """
        super().__init__("ExchangeFundingRateBillListener", interval)

    async def on_tick(self):
        """
        定时回调：获取并保存资金费率账单

        仅当父交易所处于就绪状态时执行。
        """
        parent: 'BaseExchange' = self.parent
        root: 'AppCore' = self.root  # type: ignore

        # 只有当交易所准备好时才执行
        if not parent.ready:
            return

        # 从交易所获取资金费率账单
        bills = await parent.medal_fetch_funding_rates_history()
        if not bills:
            return

        # 转换为数据库格式
        data = [
            {
                'id': f"{parent.class_name}-{bill.id}",
                'exchange_name': parent.class_name,
                'exchange_path': parent.config.path,
                'trading_pair': bill.symbol,
                'funding_profit': bill.funding_amount,
                'timestamp': datetime.fromtimestamp(bill.funding_time),
            }
            for bill in bills
        ]

        # 批量插入数据库
        await root.database.insert('funding_rate_bill', data)
        self.logger.debug("Saved %d funding rate bills", len(data))


class ExchangeBalanceUsdListener(Listener):
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

    def __init__(self, interval: float = 60.0):
        """
        初始化余额快照监听器

        Args:
            interval: 采集间隔（秒），默认 1 分钟
        """
        super().__init__("ExchangeBalanceUsdListener", interval)

    async def on_tick(self):
        """
        定时回调：获取并保存余额快照

        仅当父交易所处于就绪状态时执行。
        """
        parent: 'BaseExchange' = self.parent
        root: 'AppCore' = self.root  # type: ignore

        # 只有当交易所准备好时才执行
        if not parent.ready:
            return

        # 获取余额和持仓
        balance_usd = await parent.medal_fetch_balance_usd()
        positions = await parent.medal_fetch_positions()

        # 计算持仓总价值（取绝对值）
        position_usd = sum(abs(pos) for pos in positions.values())

        # 插入数据库
        await root.database.insert_row(
            'balance_usd',
            timestamp=datetime.now(),
            exchange_name=parent.class_name,
            exchange_path=parent.config.path,
            balance_usd=balance_usd,
            position_usd=position_usd,
        )
        self.logger.debug("Saved balance snapshot: balance=%.2f, position=%.2f", balance_usd, position_usd)

"""
交易所分组管理模块

ExchangeGroup 按交易所类型（class_name）组织多账户：
- 同类交易所（如 okx）的多个账户共享数据订阅，避免重复获取
- 支持动态添加/移除交易所实例
- 为 Executor 提供按类型获取交易所的接口

数据流：
    Strategy 请求数据 -> ExchangeGroup.get_exchange_by_class()
                            -> 返回主交易所实例
                            -> 调用 exchange.watch_xxx() 或 fetch_xxx()

执行流：
    Executor.on_tick() -> ExchangeGroup.get_exchanges_by_class()
                            -> 返回该类型所有账户
                            -> 依次在所有账户执行（老鼠仓模式）
"""
from typing import TYPE_CHECKING, Optional
from collections import defaultdict
from cryptography.fernet import InvalidToken
from ..core.listener import Listener, ListenerState
from .base import BaseExchange
from .config import BaseExchangeConfig

if TYPE_CHECKING:
    from ..core.app import AppCore


class ExchangeGroup(Listener):
    """
    交易所分组管理器

    设计理念：
    - 按 class_name 分组（okx, binance, bybit 等）
    - 每个 class_name 下可有多个账户实例
    - 数据订阅去重：同类交易所共享数据源，由主交易所（第一个）负责订阅
    - 多账户执行：下单时同类交易所的所有账户同步执行

    结构示例：
        exchanges_map = {
            "okx": ["okx_main", "okx_sub1", "okx_sub2"],
            "binance": ["binance_main"],
        }

    使用方式：
        # 获取主交易所（用于数据订阅）
        okx = exchange_group.get_exchange_by_class("okx")
        ticker = await okx.fetch_ticker("BTC/USDT:USDT")

        # 获取所有账户（用于下单）
        all_okx = exchange_group.get_exchanges_by_class("okx")
        for ex in all_okx:
            await ex.create_order(...)

    Attributes:
        exchanges_map: class_name -> [instance_names] 映射
    """

    def __init__(self):
        super().__init__("ExchangeGroup", interval=60.0)
        self.exchanges_map = defaultdict(list)

    async def add_exchange(self, exchange: BaseExchange):
        self.exchanges_map[exchange.class_name].append(exchange.name)
        self.add_child(exchange)
        if self.state in (ListenerState.STARTING, ListenerState.RUNNING):
            await exchange.start()

    async def remove_exchange(self, exchange: BaseExchange):
        await exchange.stop()
        self.exchanges_map[exchange.class_name].remove(exchange.name)
        self.remove_child(exchange.name)

    async def on_tick(self):
        # Placeholder for periodic tasks related to exchange groups
        app: 'AppCore' = self.root
        for exchange in list(self.children.values()):
            if exchange.name not in app.config.exchanges:
                await self.remove_exchange(exchange)
        for exchange_name in app.config.exchanges:
            if exchange_name not in self.children:
                try:
                    exchange_config = BaseExchangeConfig.load(exchange_name)
                    exchange_instance: BaseExchange = exchange_config.instance
                except InvalidToken:
                    self.logger.error("Failed to decrypt exchange config file for %s, you should check password or config file.", exchange_name)
                    return True  # 配置文件解密失败，跳过加载该交易所
                await self.add_exchange(exchange_instance)

    def get_exchange_classes(self):
        classes = []
        for k, v in list(self.exchanges_map.items()):
            if len(v) > 0:
                classes.append(k)
        return classes

    def get_exchange_by_class(self, class_name: str) -> Optional[BaseExchange]:
        exchange_names = self.exchanges_map[class_name]
        for exchange_name in exchange_names:
            exchange = self.children.get(exchange_name, None)
            if exchange is not None:
                return exchange
        return None

    def get_exchanges_by_class(self, class_name: str) -> list[BaseExchange]:
        result = []
        exchange_names = self.exchanges_map[class_name]
        for exchange_name in exchange_names:
            exchange = self.children.get(exchange_name, None)
            if exchange is not None:
                result.append(exchange)
        return result

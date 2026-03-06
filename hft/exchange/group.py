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
from functools import cached_property
from typing import Optional
from pyee.asyncio import AsyncIOEventEmitter
from ..core.filters import get_matcher_raw
from ..core.listener import Listener
from ..core.group import Group
from ..core.cache_decorator import cache_sync
from .base import BaseExchange


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
    __pickle_exclude__ = {*Listener.__pickle_exclude__, "event",
                          "exchange_configs", "exchange_instances",
                          "exchange_group"}
    lazy_start = True
    disable_tick = True  # 没有on tick 方法

    @cached_property
    def exchange_configs(self):
        return self.root.config.exchanges.exchange_configs

    def exchange_group_func(self, exchange_path: str) -> str:  # 将exchange分组的函数
        instance = self.exchange_instances[exchange_path]
        return instance.class_name

    @cached_property
    def exchange_group(self) -> Group:
        """按交易所类型分组的 exchange 实例映射"""
        return Group(self.exchange_group_func, self.exchange_instances.keys())

    @cache_sync(ttl=60)
    def get_trade_classes(self, filters: Optional[str] = None) -> set[str]:
        """获取所有交易所类型列表"""
        result = set()
        matcher = get_matcher_raw(filters)
        for key, group in self.exchange_group.items():
            instance = self.exchange_instances[group[0]]  # 取每组的第一个实例
            data = instance.markets.get_data()
            if data is not None:
                for symbol in data.keys():
                    if matcher.matches(symbol):
                        result.add(f"{key}-{symbol}")
        return result

    def trade_class_group_func(self, trade_class: str) -> str:
        return trade_class.split("-", 1)[0]

    @cache_sync(ttl=60)
    def get_trade_class_group(self, filters: Optional[str] = None) -> Group:
        """按交易所类型分组的 trade_class 映射"""
        return Group(self.trade_class_group_func, self.get_trade_classes(filters))

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self.exchange_instances: dict[str, 'BaseExchange'] = {}
        self.event = AsyncIOEventEmitter()  # 触发exchange的相关事件，如 position变动，order变动等。
        # 当前支持 order:created, order:updated, order:deleted
        for name, config in self.exchange_configs.items():
            instance = self.root.factory.get_or_create_configurable_instance(config, self)
            self.exchange_instances[name] = instance  # yu

    async def on_tick(self):
        pass

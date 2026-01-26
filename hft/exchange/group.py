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
from functools import cached_property, lru_cache
from collections import defaultdict
from typing import TYPE_CHECKING, Optional

from cryptography.fernet import InvalidToken

from ..core.listener import Listener, ListenerState
from .base import BaseExchange

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
    __pickle_exclude__ = {*Listener.__pickle_exclude__, "config_path"}
    lazy_start = True
    disable_tick = True  # 没有on tick 方法

    @cached_property
    def config_path(self):
        return self.root.config.exchanges

    @lru_cache(maxsize=512)
    def get_exchange_instances_raw(self, selectors: str) -> dict[str, 'BaseExchange']:
        factory = self.root.factory
        results = {}
        for name, config_path in self.config_path.get_filtered_exchanges_map_raw(factory, selectors).items():
            results[name] = factory.get_or_create_configurable_instance(config_path, self)
        return results

    def get_exchange_instances(self, includes: Optional[list[str]] = None, excludes: Optional[list[str]] = None) -> dict[str, 'BaseExchange']:
        """
        获取过滤后的 exchanges_map

        Args:
            includes: 包含的交易所类型列表
            excludes: 排除的交易所类型列表

        Returns:
            过滤后的 exchanges_map
        """
        return self.get_exchange_instances_raw(
            self.config_path.join_selectors(includes, excludes)
        )

    @lru_cache(maxsize=512)
    def get_grouped_exchange_instances_raw(self, selectors: str) -> dict[str, list['BaseExchange']]:
        factory = self.root.factory
        grouped_configs = self.config_path.get_filtered_grouped_exchanges_map_raw(factory, selectors)
        results = {}
        for class_name, config_paths in grouped_configs.items():
            instances = []
            for config_path in config_paths:
                instance = factory.get_or_create_configurable_instance(config_path, self)
                instances.append(instance)
            results[class_name] = instances
        return results

    def get_grouped_exchange_instances(self, includes: Optional[list[str]] = None, excludes: Optional[list[str]] = None) -> dict[str, list['BaseExchange']]:
        """
        获取过滤后的分组 exchanges_map

        Args:
            includes: 包含的交易所类型列表
            excludes: 排除的交易所类型列表

        Returns:
            过滤后的分组 exchanges_map
        """
        return self.get_grouped_exchange_instances_raw(
            self.config_path.join_selectors(includes, excludes)
        )

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self.get_exchange_instances()  # 预加载交易所实例

    async def on_tick(self):
        pass

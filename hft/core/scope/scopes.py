"""
标准 Scope 类型定义

特殊变量（由 Scope 系统自动提供）：
- 所有 Scope: instance_id（等于 scope_instance_id）
- GlobalScope: app_core
- ExchangeClassScope: exchange_class
- ExchangeScope: exchange_id, exchange
- TradingPairClassScope: symbol, exchange_class（继承）
- TradingPairScope: exchange_id, symbol
"""
from typing import TYPE_CHECKING
from .base import BaseScope, ScopeInstanceId
from ..filters import get_matcher_quick

if TYPE_CHECKING:
    from ...core.app.base import AppCore
    from ...exchange.base import BaseExchange


__all__ = [
    "GlobalScope",
    "ExchangeClassScope",
    "ExchangeScope",
    "TradingPairClassGroupScope",
    "TradingPairClassScope",
    "TradingPairScope",
]


class GlobalScope(BaseScope):
    """
    全局 Scope

    特性：
    - 没有 parent
    - 通常只有一个实例
    - 存储全局配置变量
    - 提供常用函数（所有子节点都会继承）

    特殊变量（由 BaseScope 自动提供）：
    - instance_id: instance_id
    - app_core: AppCore 实例引用

    特殊函数：
    - min, max, sum, len, abs, round
    - clip: 限制值在范围内
    - avg: 计算平均值
    """

    def initialize(self, **kwargs):
        """初始化 GlobalScope 的函数"""
        super().initialize(**kwargs)
        # 添加常用函数（所有子节点都会继承）
        self.set_function('min', min)
        self.set_function('max', max)
        self.set_function('sum', sum)
        self.set_function('len', len)
        self.set_function('abs', abs)
        self.set_function('round', round)
        self.set_function('clip', lambda x, min_val, max_val: max(min_val, min(x, max_val)))
        self.set_function('avg', lambda lst: sum(lst) / len(lst) if lst else 0)
        self.set_function('sign', lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        self.set_function('matcher', get_matcher_quick)

    @property
    def exchange_group(self):
        return self._app_core.exchange_group

    @classmethod
    def get_all_instance_ids(cls, app_core: 'AppCore') -> set[ScopeInstanceId]:
        """
        获取指定 Scope 类型的所有实例 ID

        Args:
            scope_class: 目标 Scope 类
            filters: 可选的过滤字符串

        Returns:
            实例 ID 列表
        """
        return {("global", )}  # instance_id is "global"


def to_global_scope(current_instance_id: ScopeInstanceId) -> ScopeInstanceId:
    return ("global", )


class ExchangeClassScope(BaseScope):
    """
    交易所类 Scope

    特性：
    - parent 是 GlobalScope
    - scope_instance_id 是交易所类名（如 "okx", "binance"）

    特殊变量（由 BaseScope 自动提供）：
    - instance_id: scope_instance_id

    额外变量：
    - exchange_class: 交易所类名（等于 scope_instance_id）
    """

    flow_mapper = {
        GlobalScope: [to_global_scope],
    }

    def initialize(self, **kwargs):
        """初始化 ExchangeClassScope 的变量"""
        super().initialize(**kwargs)
        self.set_var("exchange_class", self.exchange_class)

    @property
    def exchange_class(self) -> str:
        """获取 exchange_class"""
        return self.instance_id[0]

    @property
    def exchanges(self) -> list['BaseExchange']:
        """获取该类的所有交易所实例"""
        exchange_class = self.exchange_class
        exchange_group = self._app_core.exchange_group
        group = exchange_group.exchange_group[exchange_class]
        return [exchange_group.exchange_instances[id_] for id_ in group]

    @classmethod
    def get_all_instance_ids(cls, app_core: 'AppCore') -> set[ScopeInstanceId]:
        exchange_group = app_core.exchange_group
        results = set()
        for group_name in exchange_group.exchange_group.keys():
            results.add((group_name, ))
        return results


def exchange_to_exchange_class_scope(current_instance_id: ScopeInstanceId) -> ScopeInstanceId:
    exchange_group, _exchange_path = current_instance_id
    return (exchange_group, )


class ExchangeScope(BaseScope):
    """
    交易所实例 Scope

    特性：
    - parent 是 ExchangeClassScope
    - instance_id 是交易所路径（如 "okx/main", "binance/spot"）

    特殊变量（由 BaseScope 自动提供）：
    - instance_id: scope_instance_id
    - class_id: scope_class_id
    - app_core: AppCore 实例引用

    额外变量（静态，从 scope_instance_id 解析）：
    - exchange_class: 交易所类（从parent继承）
    - exchange_path: 交易所路径（向后兼容，等于 instance_id）

    动态变量（需要时从 app_core 查找）：
    - exchange: exchange 实例引用（通过 app_core.exchange_group 动态查找）
    """
    flow_mapper = {
        ExchangeClassScope: [exchange_to_exchange_class_scope],
    }
    def initialize(self, **kwargs):
        """初始化 ExchangeScope 的变量"""
        super().initialize(**kwargs)
        exchange_class, exchange_path = self.instance_id
        self.set_var("exchange_class", exchange_class)
        self.set_var("exchange_path", exchange_path)  # 向后兼容

    @property
    def exchange_path(self) -> str:
        """获取 exchange_path（向后兼容）"""
        return self.instance_id[1]

    @property
    def exchange(self) -> 'BaseExchange':  # 映射到对应的 exchange instance
        return self._app_core.exchange_group.exchange_instances[self.exchange_path]

    @classmethod
    def get_all_instance_ids(cls, app_core: 'AppCore') -> set[ScopeInstanceId]:
        exchange_group = app_core.exchange_group
        results = set()
        for exchange_class, exchange_items in exchange_group.exchange_group.items():
            for exchange_item in exchange_items:
                results.add((exchange_class, exchange_item))
        return results


def trading_pair_class_to_exchange_class_scope(current_instance_id: ScopeInstanceId) -> ScopeInstanceId:
    exchange_class, _symbol = current_instance_id
    return (exchange_class, )


def trading_pair_class_to_group_scope(current_instance_id: ScopeInstanceId) -> ScopeInstanceId:
    """TradingPairClassScope -> TradingPairClassGroupScope（跨平台）"""
    _exchange_class, symbol = current_instance_id
    group_id = symbol.split('/')[0] if '/' in symbol else symbol
    return (group_id,)


class TradingPairClassGroupScope(BaseScope):
    """
    交易对分组 Scope（跨平台）

    将不同平台的交易对按 base currency 聚合成一个组。
    例如 okx ETH/USDT、binance ETH/USDT:USDT、WBETH/USDT 都归入 "ETH" 组。

    instance_id: (group_id,)，如 ("ETH",)
    """
    flow_mapper = {
        GlobalScope: [to_global_scope],
    }

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        group_id, = self.instance_id
        self.set_var("group_id", group_id)

    @property
    def group_id(self) -> str:
        return self.instance_id[0]

    @classmethod
    def get_all_instance_ids(cls, app_core: 'AppCore') -> set[ScopeInstanceId]:
        """遍历所有交易对，提取 base currency 作为 group_id"""
        exchange_group = app_core.exchange_group
        results = set()
        for exchange_class, exchange_paths in exchange_group.exchange_group.items():
            for exchange_path in exchange_paths:
                instance = exchange_group.exchange_instances[exchange_path]
                if instance.ready:
                    markets = instance.markets.get_data()
                    for symbol in markets.keys():
                        group_id = symbol.split('/')[0] if '/' in symbol else symbol
                        results.add((group_id,))
        return results


class TradingPairClassScope(BaseScope):
    """
    交易对类 Scope

    特性：
    - parent 可以是 ExchangeClassScope 或其它 TradingPairClassGroupScope
    - scope_instance_id 格式为 "exchange_class-symbol"（如 "okx-ETH/USDT"）

    特殊变量（由 BaseScope 自动提供）：
    - instance_id: scope_instance_id
    - class_id: scope_class_id

    额外变量：
    - symbol: 交易对符号（从 scope_instance_id 解析）
    - exchange_class: 继承自 parent
    """
    flow_mapper = {
        ExchangeClassScope: [trading_pair_class_to_exchange_class_scope],
        TradingPairClassGroupScope: [trading_pair_class_to_group_scope],
    }

    def initialize(self, **kwargs) -> None:
        """初始化 TradingPairClassScope 的变量"""
        # 解析 instance_id: "exchange_class-symbol"
        super().initialize(**kwargs)
        exchange_class, symbol = self.instance_id
        self.set_var("exchange_class", exchange_class)
        self.set_var("symbol", symbol)

    @property
    def exchanges(self) -> list['BaseExchange']:
        """获取该交易对类对应的所有 exchange 实例"""
        exchange_class = self.get_var("exchange_class")
        exchange_group = self._app_core.exchange_group
        exchange_paths = exchange_group.exchange_group[exchange_class]
        return [exchange_group.exchange_instances[path] for path in exchange_paths]

    @classmethod
    def get_all_instance_ids(cls, app_core: 'AppCore') -> set[ScopeInstanceId]:
        exchange_group = app_core.exchange_group
        results = set()
        for exchange_class, exchange_paths in exchange_group.exchange_group.items():
            for exchange_path in exchange_paths:
                instance = exchange_group.exchange_instances[exchange_path]
                if instance.ready:
                    markets = instance.markets.get_data()
                    for symbol in markets.keys():
                        results.add((exchange_class, symbol))
        return results


def trading_pair_to_trading_pair_class_scope(current_instance_id: ScopeInstanceId) -> ScopeInstanceId:
    exchange_class, _exchange_path, symbol = current_instance_id
    return (exchange_class, symbol)


def trading_pair_to_exchange_scope(current_instance_id: ScopeInstanceId) -> ScopeInstanceId:
    exchange_class, exchange_path, _symbol = current_instance_id
    return (exchange_class, exchange_path)


class TradingPairScope(BaseScope):
    """
    交易对实例 Scope

    特性：
    - parent 是 TradingPairClassScope 或 ExchangeScope
    - scope_instance_id 格式为 "exchange_path-symbol"（如 "okx/a-ETH/USDT"）

    额外变量：
    - exchange_path: exchange path（向后兼容）
    - symbol: 交易对符号（从 scope_instance_id 解析）
    """
    flow_mapper = {
        TradingPairClassScope: [trading_pair_to_trading_pair_class_scope],
        ExchangeScope: [trading_pair_to_exchange_scope],
    }

    def initialize(self, **kwargs):
        """初始化 TradingPairScope 的变量"""
        # 解析 exchange_path 和 symbol
        # 支持两种格式：
        # 1. "exchange_path-symbol" (新格式，如 "okx/a-ETH/USDT")
        super().initialize(**kwargs)

        exchange_class, exchange_path, symbol = self.instance_id
        self.set_var("exchange_class", exchange_class)
        self.set_var("exchange_path", exchange_path)
        self.set_var("symbol", symbol)

    @classmethod
    def get_all_instance_ids(cls, app_core: 'AppCore') -> set[ScopeInstanceId]:
        exchange_group = app_core.exchange_group
        results = set()
        for exchange_path, instance in exchange_group.exchange_instances.items():
            if instance.ready:
                markets = instance.markets.get_data()
                for symbol in markets.keys():
                    results.add((instance.class_name, exchange_path, symbol))
        return results

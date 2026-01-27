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

from .base import BaseScope
from .instance_ids import register_get_all_instance_ids
if TYPE_CHECKING:
    from ...core.app.base import AppCore
    from ...exchange.base import BaseExchange


class GlobalScope(BaseScope):
    """
    全局 Scope

    特性：
    - 没有 parent
    - 通常只有一个实例
    - 存储全局配置变量
    - 提供常用函数（所有子节点都会继承）

    特殊变量（由 BaseScope 自动提供）：
    - instance_id: scope_instance_id
    - class_id: scope_class_id
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


@register_get_all_instance_ids(None, GlobalScope)
def get_global_instance_ids(app_core, parent_scope, scope_class):
    """GlobalScope 固定返回 ["global"]"""
    return ["global"]  # instance_id is "global"


class ExchangeClassScope(BaseScope):
    """
    交易所类 Scope

    特性：
    - parent 是 GlobalScope
    - scope_instance_id 是交易所类名（如 "okx", "binance"）

    特殊变量（由 BaseScope 自动提供）：
    - instance_id: scope_instance_id
    - class_id: scope_class_id

    额外变量：
    - exchange_class: 交易所类名（等于 scope_instance_id）
    """

    def initialize(self, **kwargs):
        """初始化 ExchangeClassScope 的变量"""
        super().initialize(**kwargs)
        self.set_var("exchange_class", self._instance_id)

    @property
    def exchanges(self) -> list['BaseExchange']:
        """获取该交易所类的所有 exchange 实例"""
        exchange_class = self.instance_id
        exchanges = self._app_core.exchange_group.get_grouped_exchange_instances().get(exchange_class, [])
        return exchanges

@register_get_all_instance_ids(GlobalScope, ExchangeClassScope)
def get_exchange_class_instance_ids(app_core: 'AppCore', parent_scope, scope_class):
    """根据 app_core.exchange_group 获取所有 exchange class"""
    return list(app_core.exchange_group.get_grouped_exchange_instances().keys())


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

    def initialize(self, **kwargs):
        """初始化 ExchangeScope 的变量"""
        super().initialize(**kwargs)
        self.set_var("exchange_path", self.instance_id)  # 向后兼容

    @property
    def exchange(self) -> 'BaseExchange':  # 映射到对应的 exchange instance
        return self._app_core.exchange_group.get_exchange_instances()[self._instance_id]


@register_get_all_instance_ids(ExchangeClassScope, ExchangeScope)
def get_exchange_instance_ids(app_core: 'AppCore', parent_scope: ExchangeClassScope, scope_class):
    exchange_class = parent_scope.instance_id
    exchanges = app_core.exchange_group.get_grouped_exchange_instances()[exchange_class]
    return [exchange.config.path for exchange in exchanges]


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

    def initialize(self, **kwargs) -> None:
        """初始化 TradingPairClassScope 的变量"""
        # 解析 instance_id: "exchange_class-symbol"
        super().initialize(**kwargs)
        exchange_class, symbol = self.instance_id.split('-', 1)
        self.set_var("exchange_class", exchange_class)
        self.set_var("symbol", symbol)

    @property
    def exchanges(self) -> list['BaseExchange']:
        """获取该交易对类对应的所有 exchange 实例"""
        exchange_class = self.get_var("exchange_class")
        exchanges = self._app_core.exchange_group.get_grouped_exchange_instances().get(exchange_class, [])
        return exchanges

@register_get_all_instance_ids(ExchangeClassScope, TradingPairClassScope)
def get_trading_pair_class_instance_ids(app_core: 'AppCore', parent_scope: ExchangeClassScope, scope_class):
    """根据 parent exchange_class 获取该类型下所有唯一的 symbols"""
    exchange_class = parent_scope.instance_id
    # for exchange in app_core.exchange_group.children.values():
    exchange = app_core.exchange_group.get_grouped_exchange_instances()[exchange_class][0]  # 取第一个 exchange
    if not exchange.ready:
        return []
    markets = exchange.markets.get_data()
    # 生成 instance_id: "exchange_class-symbol"
    return [f"{exchange_class}-{symbol}" for symbol in markets.keys()]


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

    def initialize(self, **kwargs):
        """初始化 TradingPairScope 的变量"""
        # 解析 exchange_path 和 symbol
        # 支持两种格式：
        # 1. "exchange_path-symbol" (新格式，如 "okx/a-ETH/USDT")
        super().initialize(**kwargs)

        exchange_path, symbol = self.instance_id.split('-', 1)
        self.set_var("exchange_path", exchange_path)
        self.set_var("symbol", symbol)


@register_get_all_instance_ids(ExchangeScope, TradingPairScope)
def get_trading_pair_from_exchange_instance_ids(app_core: 'AppCore', parent_scope: ExchangeScope, scope_class):
    """根据 parent exchange 获取该 exchange 的所有交易对"""
    # 获取 exchange 实例
    exchange = parent_scope.exchange
    if not exchange.ready:
        return []
    exchange_path = exchange.config.path
    markets = exchange.markets.get_data()
    return [f"{exchange_path}-{symbol}" for symbol in markets.keys()]


@register_get_all_instance_ids(TradingPairClassScope, TradingPairScope)
def get_trading_pair_from_class_instance_ids(app_core: 'AppCore', parent_scope: TradingPairClassScope, scope_class):
    """根据 parent trading_pair_class 获取所有具体的交易对实例"""
    symbol = parent_scope.get_var('symbol')
    return [f"{exchange.config.path}-{symbol}" for exchange in parent_scope.exchanges]

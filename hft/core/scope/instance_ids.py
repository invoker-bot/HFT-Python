"""
get_all_instance_ids 注册机制

提供 Scope 实例发现的注册和调用机制。

使用方式：
1. 注册自定义函数：
   @register_get_all_instance_ids(ParentScopeClass, ScopeClass)
   def my_instance_ids(app_core, parent_scope):
       return [...]

2. 调用：
   instance_ids = get_all_instance_ids(app_core, parent_scope, parent_scope_class, scope_class)
"""
from typing import Callable, Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseScope
    from ...core.app.base import AppCore

# 全局注册表：{(ParentScopeClass, ScopeClass): func}
# 其中 ParentScopeClass 可以是 None（表示根节点）
_instance_ids_registry: dict[tuple[Optional[type], type], Callable] = {}


def register_get_all_instance_ids(
    parent_scope_class: Optional[type],
    scope_class: type
) -> Callable:
    """
    注册 get_all_instance_ids 函数的装饰器

    Args:
        parent_scope_class: 父 Scope 类（None 表示根节点）
        scope_class: 当前 Scope 类

    Returns:
        装饰器函数

    Example:
        @register_get_all_instance_ids(GlobalScope, ExchangeClassScope)
        def get_exchange_class_ids(app_core, parent_scope):
            return ["okx", "binance"]
    """
    def decorator(func: Callable) -> Callable:
        _instance_ids_registry[(parent_scope_class, scope_class)] = func
        return func
    return decorator


def get_all_instance_ids(
    app_core: "AppCore",
    parent_scope: Optional["BaseScope"],
    parent_scope_class: Optional[type],
    scope_class: type,
    **kwargs
) -> list[str]:
    """
    获取指定 Scope 类型的所有实例 ID

    Args:
        app_core: AppCore 实例
        parent_scope: 父 Scope 实例（根节点时为 None）
        parent_scope_class: 父 Scope 类（根节点时为 None）
        scope_class: 当前 Scope 类
        **kwargs: 额外参数

    Returns:
        实例 ID 列表

    Raises:
        ValueError: 如果没有找到对应的注册函数
    """
    key = (parent_scope_class, scope_class)
    func = _instance_ids_registry.get(key)

    if func is None:
        raise ValueError(
            f"No get_all_instance_ids registered for "
            f"({parent_scope_class.__name__ if parent_scope_class else 'None'}, {scope_class.__name__})"
        )

    return func(app_core, parent_scope, **kwargs)


def has_instance_ids_provider(
    parent_scope_class: Optional[type],
    scope_class: type
) -> bool:
    """
    检查是否有注册的实例发现函数

    Args:
        parent_scope_class: 父 Scope 类
        scope_class: 当前 Scope 类

    Returns:
        是否已注册
    """
    return (parent_scope_class, scope_class) in _instance_ids_registry


# ============================================================
# 内置标准 Scope 的实例发现函数
# ============================================================

def _register_standard_instance_ids():
    """注册内置标准 Scope 的实例发现函数"""
    from .scopes import (
        GlobalScope,
        ExchangeClassScope,
        ExchangeScope,
        TradingPairClassScope,
        TradingPairScope,
    )

    # (None, GlobalScope) -> ["global"]
    @register_get_all_instance_ids(None, GlobalScope)
    def get_global_instance_ids(app_core, parent_scope, **kwargs):
        """GlobalScope 固定返回 ["global"]"""
        return ["global"]

    # (GlobalScope, ExchangeClassScope) -> ["okx", "binance", ...]
    @register_get_all_instance_ids(GlobalScope, ExchangeClassScope)
    def get_exchange_class_instance_ids(app_core, parent_scope, **kwargs):
        """根据 app_core.exchange_group 获取所有 exchange class"""
        if not hasattr(app_core, 'exchange_group'):
            return []
        exchange_classes = set()
        for exchange in app_core.exchange_group.children.values():
            exchange_classes.add(exchange.class_name)
        return sorted(exchange_classes)

    # (ExchangeClassScope, ExchangeScope) -> ["okx/a", "okx/b", ...]
    @register_get_all_instance_ids(ExchangeClassScope, ExchangeScope)
    def get_exchange_instance_ids(app_core, parent_scope, **kwargs):
        """根据 parent exchange_class 获取该类型的所有 exchange"""
        if not hasattr(app_core, 'exchange_group') or parent_scope is None:
            return []
        exchange_class = parent_scope.scope_instance_id
        exchange_paths = []
        for exchange in app_core.exchange_group.children.values():
            if exchange.class_name == exchange_class:
                exchange_paths.append(exchange.config.path)
        return sorted(exchange_paths)

    # (ExchangeScope, TradingPairScope) -> ["okx/a-ETH/USDT", ...]
    @register_get_all_instance_ids(ExchangeScope, TradingPairScope)
    def get_trading_pair_from_exchange_instance_ids(app_core, parent_scope, **kwargs):
        """根据 parent exchange 获取该 exchange 的所有交易对"""
        if not hasattr(app_core, 'exchange_group') or parent_scope is None:
            return []
        exchange_path = parent_scope.scope_instance_id
        # 获取 exchange 实例
        exchange = None
        for ex in app_core.exchange_group.children.values():
            if ex.config.path == exchange_path:
                exchange = ex
                break
        if exchange is None:
            return []
        # 获取支持的 symbols
        if not hasattr(exchange, 'markets') or not exchange.markets:
            return []
        # 应用 filter（如果提供）
        symbols = list(exchange.markets.keys())
        symbol_filter = kwargs.get('symbol_filter')
        if symbol_filter:
            symbols = [s for s in symbols if symbol_filter(s)]
        # 生成 instance_id: "exchange_path-symbol"
        return [f"{exchange_path}-{symbol}" for symbol in sorted(symbols)]

    # (ExchangeClassScope, TradingPairClassScope) -> ["okx-ETH/USDT", ...]
    @register_get_all_instance_ids(ExchangeClassScope, TradingPairClassScope)
    def get_trading_pair_class_instance_ids(app_core, parent_scope, **kwargs):
        """根据 parent exchange_class 获取该类型下所有唯一的 symbols"""
        if not hasattr(app_core, 'exchange_group') or parent_scope is None:
            return []
        exchange_class = parent_scope.scope_instance_id
        symbols = set()
        for exchange in app_core.exchange_group.children.values():
            if exchange.class_name == exchange_class:
                if hasattr(exchange, 'markets') and exchange.markets:
                    symbols.update(exchange.markets.keys())
        # 应用 filter（如果提供）
        symbol_filter = kwargs.get('symbol_filter')
        if symbol_filter:
            symbols = {s for s in symbols if symbol_filter(s)}
        # 生成 instance_id: "exchange_class-symbol"
        return [f"{exchange_class}-{symbol}" for symbol in sorted(symbols)]

    # (TradingPairClassScope, TradingPairScope) -> ["okx/a-ETH/USDT", ...]
    @register_get_all_instance_ids(TradingPairClassScope, TradingPairScope)
    def get_trading_pair_from_class_instance_ids(app_core, parent_scope, **kwargs):
        """根据 parent trading_pair_class 获取所有具体的交易对实例"""
        if not hasattr(app_core, 'exchange_group') or parent_scope is None:
            return []
        # 解析 parent instance_id: "exchange_class-symbol"
        instance_id = parent_scope.scope_instance_id
        if '-' not in instance_id:
            return []
        parts = instance_id.split('-', 1)
        exchange_class = parts[0]
        symbol = parts[1]
        # 找到所有该 exchange_class 且支持该 symbol 的 exchange
        trading_pairs = []
        for exchange in app_core.exchange_group.children.values():
            if exchange.class_name == exchange_class:
                if hasattr(exchange, 'markets') and exchange.markets:
                    if symbol in exchange.markets:
                        exchange_path = exchange.config.path
                        trading_pairs.append(f"{exchange_path}-{symbol}")
        return sorted(trading_pairs)


# 模块加载时自动注册标准 Scope 的实例发现函数
_register_standard_instance_ids()

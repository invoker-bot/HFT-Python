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
# pylint: disable=import-outside-toplevel
from typing import TYPE_CHECKING, Callable, Optional, Type

if TYPE_CHECKING:
    from ...core.app.base import AppCore
    from .base import BaseScope

# 全局注册表：{(ParentScopeClass, ScopeClass): func}
# 其中 ParentScopeClass 可以是 None（表示根节点）
GetInstanceIds = Callable[['AppCore', Optional['BaseScope'], Type['BaseScope']], list[str]]
_instance_ids_registry: dict[tuple[Optional[Type['BaseScope']], Type['BaseScope']], GetInstanceIds] = {}


def register_get_all_instance_ids(
    parent_scope_class: Optional[Type['BaseScope']],
    scope_class: Type['BaseScope']
) -> Callable[[GetInstanceIds], GetInstanceIds]:
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
    def decorator(func: GetInstanceIds) -> GetInstanceIds:
        _instance_ids_registry[(parent_scope_class, scope_class)] = func
        return func
    return decorator


def get_all_instance_ids(
    app_core: "AppCore",
    parent_scope: Optional["BaseScope"],
    scope_class: Type["BaseScope"],
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
    if parent_scope is None:
        parent_scope_class = None
    else:
        parent_scope_class = parent_scope.__class__
    key = (parent_scope_class, scope_class)
    func = _instance_ids_registry.get(key, None)

    if func is None:
        raise NotImplementedError(
            f"No get_all_instance_ids registered for "
            f"({parent_scope_class.__name__ if parent_scope_class else 'None'}, {scope_class.__name__})"
        )

    return func(app_core, parent_scope, scope_class)


def has_instance_ids_provider(
    parent_scope_class: Optional[Type["BaseScope"]],
    scope_class: Type["BaseScope"]
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

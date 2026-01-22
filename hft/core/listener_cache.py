"""
Listener 缓存管理模块
+
提供 Listener 实例的缓存和恢复机制：
- get_or_create: 从缓存获取或创建 Listener 实例
- ListenerCache: 收集和管理 Listener 状态缓存
"""
# pylint: disable=import-outside-toplevel,protected-access
from typing import Type, Optional, TypeVar, Dict, Any, TYPE_CHECKING
if TYPE_CHECKING:
    from .listener import Listener

T = TypeVar('T', bound='Listener')


def build_cache_key(
    listener_class: Type['Listener'],
    name: str,
    parent: Optional['Listener'] = None
) -> str:
    """
    构建缓存键

    格式："ClassName:name/parent_key"

    Args:
        listener_class: Listener 类
        name: Listener 名称
        parent: 父 Listener

    Returns:
        缓存键字符串
    """
    current = f"{listener_class.__name__}:{name}"
    if parent is None:
        return current

    # 递归构建父路径
    parent_key = build_cache_key(type(parent), parent.name, parent.parent)
    return f"{current}/{parent_key}"


def get_or_create(
    cache: Dict[str, Dict[str, Any]],
    listener_class: Type[T],
    name: Optional[str] = None,
    parent: Optional['Listener'] = None,
    **kwargs
) -> T:
    """
    从缓存获取或创建 Listener 实例

    如果缓存中存在对应的状态，则创建实例并恢复状态；
    否则创建新实例。

    Args:
        cache: 缓存字典 {cache_key: state_dict}
        listener_class: Listener 类
        name: Listener 名称（可选，默认使用类名）
        parent: 父 Listener
        **kwargs: 传递给构造函数的参数（仅在创建新实例时使用）

    Returns:
        Listener 实例
    """
    # 如果没有提供 name，使用类名作为默认值
    if name is None:
        name = listener_class.__name__

    cache_key = build_cache_key(listener_class, name, parent)

    if cache_key in cache:
        # 从缓存恢复
        state = cache[cache_key]
        instance = listener_class.__new__(listener_class)
        instance.__setstate__(state)
    else:
        # 创建新实例
        # 如果构造函数接受 name 参数，则传递；否则不传递
        try:
            instance = listener_class(name=name, **kwargs)
        except TypeError:
            # 构造函数不接受 name 参数，尝试不传递 name
            instance = listener_class(**kwargs)

    # 建立父子关系
    if parent is not None:
        parent.add_child(instance)

    return instance


class ListenerCache:
    """
    Listener 缓存管理器

    负责收集和恢复 Listener 树的状态。
    """

    def __init__(self):
        """初始化缓存管理器"""
        self._cache: Dict[str, Dict[str, Any]] = {}

    @property
    def cache(self) -> Dict[str, Dict[str, Any]]:
        """获取缓存字典"""
        return self._cache

    def collect(self, listener: 'Listener') -> Dict[str, Dict[str, Any]]:
        """
        递归收集 Listener 树的状态

        Args:
            listener: 根 Listener

        Returns:
            缓存字典 {cache_key: state_dict}
        """
        result: Dict[str, Dict[str, Any]] = {}
        self._collect_recursive(listener, None, result)
        return result

    def _collect_recursive(
        self,
        listener: 'Listener',
        parent: Optional['Listener'],
        result: Dict[str, Dict[str, Any]]
    ) -> None:
        """
        递归收集单个 Listener 及其子节点的状态

        Args:
            listener: 当前 Listener
            parent: 父 Listener（用于构建 cache key）
            result: 结果字典
        """
        # 构建缓存键
        cache_key = build_cache_key(type(listener), listener.name, parent)

        # 获取状态（不含 children）
        state = listener.__getstate__()
        result[cache_key] = state

        # 递归收集子节点
        for child in listener.children.values():
            self._collect_recursive(child, listener, result)

    def restore(
        self,
        cache: Dict[str, Dict[str, Any]],
        listener_class: Type[T],
        name: str,
        parent: Optional['Listener'] = None,
        **kwargs
    ) -> T:
        """
        从缓存恢复或创建 Listener 实例

        Args:
            cache: 缓存字典
            listener_class: Listener 类
            name: Listener 名称
            parent: 父 Listener
            **kwargs: 构造函数参数

        Returns:
            Listener 实例
        """
        return get_or_create(cache, listener_class, name, parent, **kwargs)

    def clear(self) -> None:
        """清空缓存"""
        self._cache.clear()

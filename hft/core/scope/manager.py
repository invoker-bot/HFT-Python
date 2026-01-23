"""
ScopeManager - Scope 实例管理器

负责 Scope 实例的创建、缓存和管理。
"""
from typing import TYPE_CHECKING, Callable, Dict, Optional, Tuple, Type

from ..listener import Listener
from .base import BaseScope
from .instance_ids import get_all_instance_ids
from .tree import LinkedScopeNode, LinkedScopeTree

if TYPE_CHECKING:
    from ...core.app.base import AppCore


class ScopeManager(Listener):
    """
    Scope 管理器（Listener）

    特性：
    - 缓存 Scope 实例（避免重复创建）
    - 注册自定义 Scope 类型
    - 提供 Scope 查找和创建接口
    - 作为 Listener 挂载到 AppCore，支持序列化到磁盘

    注意：
    - interval=None（不执行 tick，事件驱动）
    - 通过 Listener 的序列化机制缓存 scope 到磁盘
    """

    def __init__(self):
        """初始化 ScopeManager"""
        # 初始化 Listener（interval=None 表示不执行 tick）
        super().__init__(name="ScopeManager", interval=None)

        # Scope 实例缓存：{(scope_class_id, scope_instance_id): scope_instance}
        # 缓存 key 不包含 parent 信息，确保同一 scope 在不同 links 中可以复用
        self._cache: Dict[Tuple[str, str], BaseScope] = {}

        # Scope 类型注册表：{scope_class_name: scope_class}
        self._scope_classes: Dict[str, Type[BaseScope]] = {}

        # 注册标准 Scope 类型
        self._register_standard_scopes()

    def _register_standard_scopes(self) -> None:
        """注册标准 Scope 类型"""
        # 注册类名 → 类的映射
        self._scope_classes.update(BaseScope.all_classes())

    def register_scope_class(
        self,
        scope_class_name: str,
        scope_class: Type[BaseScope]
    ) -> None:
        """
        注册自定义 Scope 类型

        Args:
            scope_class_name: Scope 类名（如 "GlobalScope", "CustomScope"）
            scope_class: Scope 类
        """
        self._scope_classes[scope_class_name] = scope_class

    def get_or_create(
        self,
        scope_class_name: str,
        scope_class_id: str,
        scope_instance_id: str,
        app_core: "AppCore" = None,
    ) -> BaseScope:
        """
        获取或创建 Scope 实例

        Args:
            scope_class_name: Scope 类名（如 "GlobalScope"）
            scope_class_id: Scope 类型 ID（用户在配置中定义，如 "global", "my_scope"）
            scope_instance_id: Scope 实例 ID
            app_core: AppCore 实例

        Returns:
            Scope 实例
        """
        # 使用 (scope_class_id, scope_instance_id) 作为缓存 key
        cache_key = (scope_class_id, scope_instance_id)

        # 从缓存中获取
        if cache_key in self._cache:
            return self._cache[cache_key]

        # 创建新实例
        scope_class = self._scope_classes.get(scope_class_name)
        if scope_class is None:
            raise ValueError(f"Unknown scope class: {scope_class_name}")

        # BaseScope 构造函数不再接受 parent 参数
        scope = scope_class(scope_class_id, scope_instance_id, app_core)

        # 缓存
        self._cache[cache_key] = scope

        return scope

    def get(
        self,
        scope_class_id: str,
        scope_instance_id: str
    ) -> Optional[BaseScope]:
        """
        获取 Scope 实例（不创建）

        Args:
            scope_class_id: Scope 类型 ID
            scope_instance_id: Scope 实例 ID

        Returns:
            Scope 实例，不存在则返回 None
        """
        cache_key = (scope_class_id, scope_instance_id)
        return self._cache.get(cache_key)

    def clear_cache(self) -> None:
        """清空缓存"""
        self._cache.clear()

    def reset_all_ready_states(self) -> None:
        """重置所有 Scope 的 ready 状态"""
        for scope in self._cache.values():
            scope.not_ready = False

    async def on_tick(self) -> bool:
        """
        Tick 回调（不执行任何操作）

        ScopeManager 是事件驱动的（interval=None），不需要定期执行任务。
        此方法仅为满足 Listener 抽象基类要求而实现。

        Returns:
            False（永不退出）
        """
        return False

"""
ScopeManager - Scope 实例管理器

负责 Scope 实例的创建、缓存和管理。
"""
from typing import TYPE_CHECKING, Dict, Optional, Tuple
from functools import cached_property
from ..listener import Listener
from .base import BaseScope
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
    disable_tick = True  # 不执行定时任务
    __pickle_exclude__ = {*Listener.__pickle_exclude__, "all_scopes", "_cache", "_cache_state"}

    @property
    def interval(self):
        return None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._cache_state: Dict[Tuple[str, str], dict] = {}

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self._cache: Dict[Tuple[str, str], BaseScope] = {}

    def on_save(self):
        self._cache_state.update({key:scope.__getstate__() for key, scope in self._cache.items()})
        return {
            '_cache_state': self._cache_state
        }

    @cached_property
    def all_scopes(self):
        return BaseScope.all_classes()

    def get_or_create(
        self,
        scope_class_name: str,
        scope_class_id: str,
        scope_instance_id: str,
        app_core: "AppCore" = None,
        **kwargs
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

        # 获取类对象
        scope_class = self.all_scopes.get(scope_class_name)
        if scope_class is None:
            raise ValueError(f"Unknown scope class: {scope_class_name}")
        kwargs.update(
            {
                "instance_id": scope_instance_id,
                "class_id": scope_class_id,
                "app_core": app_core,
            }
        )
        # BaseScope 构造函数不再接受 parent 参数
        # scope = scope_class(scope_class_id, scope_instance_id, app_core)
        if cache_key in self._cache_state:  # 存在缓存状态，恢复状态
            # 从缓存恢复
            state = self._cache_state[cache_key]
            state['kwargs'] = kwargs  # 将构造函数参数传入
            instance = scope_class.__new__(scope_class)
            instance.__setstate__(state)
        else:
            # 如果构造函数接受 name 参数，则传递；否则不传递
            instance = scope_class(**kwargs)
        # 缓存
        self._cache[cache_key] = instance

        return instance

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
        self._cache_state.clear()

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

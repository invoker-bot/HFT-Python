"""
ScopeManager - Scope 实例管理器

负责 Scope 实例的创建、缓存和管理。
"""
from typing import Dict, Tuple, Optional, Type
from .base import BaseScope
from .scopes import (
    GlobalScope,
    ExchangeClassScope,
    ExchangeScope,
    TradingPairClassScope,
    TradingPairScope,
    TradingPairClassGroupScope,
)


class ScopeManager:
    """
    Scope 管理器

    特性：
    - 缓存 Scope 实例（避免重复创建）
    - 注册自定义 Scope 类型
    - 提供 Scope 查找和创建接口
    """

    def __init__(self):
        """初始化 ScopeManager"""
        # Scope 实例缓存：{cache_key: scope_instance}
        # cache_key 格式："scope_class_id:scope_instance_id"
        # 注意：相同的 (scope_class_id, scope_instance_id) 会复用同一个实例，
        # 即使 parent 不同也会返回相同的实例
        # 例如：
        # - "global:global"
        # - "exchange:okx/main"
        # - "trading_pair:okx/main:BTC/USDT"
        self._cache: Dict[str, BaseScope] = {}

        # Scope 类型注册表：{scope_class_name: scope_class}
        self._scope_classes: Dict[str, Type[BaseScope]] = {}

        # 注册标准 Scope 类型
        self._register_standard_scopes()

    def _register_standard_scopes(self) -> None:
        """注册标准 Scope 类型"""
        # 注册类名 → 类的映射
        self._scope_classes["GlobalScope"] = GlobalScope
        self._scope_classes["ExchangeClassScope"] = ExchangeClassScope
        self._scope_classes["ExchangeScope"] = ExchangeScope
        self._scope_classes["TradingPairClassScope"] = TradingPairClassScope
        self._scope_classes["TradingPairScope"] = TradingPairScope
        self._scope_classes["TradingPairClassGroupScope"] = TradingPairClassGroupScope

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

    def _build_cache_key(
        self,
        scope_class_id: str,
        scope_instance_id: str
    ) -> str:
        """
        构建 Scope 缓存 key

        Args:
            scope_class_id: Scope 类型 ID
            scope_instance_id: Scope 实例 ID

        Returns:
            缓存 key，格式："scope_class_id:scope_instance_id"

        注意：
            缓存 key 不包含 parent 信息，因此相同的 (scope_class_id, scope_instance_id)
            会复用同一个实例，即使 parent 不同
        """
        return f"{scope_class_id}:{scope_instance_id}"

    def get_or_create(
        self,
        scope_class_name: str,
        scope_class_id: str,
        scope_instance_id: str,
        parent: Optional[BaseScope] = None,
        **kwargs
    ) -> BaseScope:
        """
        获取或创建 Scope 实例

        Args:
            scope_class_name: Scope 类名（如 "GlobalScope"）
            scope_class_id: Scope 类型 ID（用户在配置中定义，如 "global", "my_scope"）
            scope_instance_id: Scope 实例 ID
            parent: 父 Scope（注意：相同的 (scope_class_id, scope_instance_id) 会复用同一实例，即使 parent 不同）
            **kwargs: 传递给 Scope 构造函数的额外参数

        Returns:
            Scope 实例
        """
        # 构建缓存 key（不包含 parent）
        cache_key = self._build_cache_key(scope_class_id, scope_instance_id)

        # 从缓存中获取
        if cache_key in self._cache:
            existing_scope = self._cache[cache_key]
            # 如果 parent 不同，需要更新 parent 关系
            if parent is not None and existing_scope not in parent.children.values():
                parent.add_child(existing_scope)
            return existing_scope

        # 创建新实例
        scope_class = self._scope_classes.get(scope_class_name)
        if scope_class is None:
            raise ValueError(f"Unknown scope class: {scope_class_name}")

        # 所有 Scope 类现在都有统一的构造函数签名
        scope = scope_class(scope_class_id, scope_instance_id, parent, **kwargs)

        # 添加到父节点的 children
        if parent is not None:
            parent.add_child(scope)

        # 缓存
        self._cache[cache_key] = scope

        return scope

    def get(
        self,
        scope_class_id: str,
        scope_instance_id: str,
        parent: Optional[BaseScope] = None
    ) -> Optional[BaseScope]:
        """
        获取 Scope 实例（不创建）

        Args:
            scope_class_id: Scope 类型 ID
            scope_instance_id: Scope 实例 ID
            parent: 父 Scope（已废弃，保留用于兼容性）

        Returns:
            Scope 实例，不存在则返回 None
        """
        cache_key = self._build_cache_key(scope_class_id, scope_instance_id)
        return self._cache.get(cache_key)

    def clear_cache(self) -> None:
        """清空缓存"""
        self._cache.clear()

    def build_scope_tree(
        self,
        link: list[str],
        scope_configs: dict[str, dict],
        instance_ids_provider: callable
    ) -> list[BaseScope]:
        """
        根据 link 配置构建 Scope 树

        Args:
            link: Scope 链路，如 ["global", "exchange_class", "exchange", "trading_pair"]
            scope_configs: Scope 配置字典，格式：
                {
                    "global": {"class": "GlobalScope", "instance_id": "global"},
                    "exchange": {"class": "ExchangeScope"},
                    ...
                }
            instance_ids_provider: 函数，用于获取指定 scope_class_id 的所有实例 ID
                签名：(scope_class_id: str, parent_scope: BaseScope) -> list[str]

        Returns:
            叶子节点 Scope 列表（target_scope 层级）
        """
        if not link:
            return []

        # 从第一个节点开始构建
        first_scope_class_id = link[0]
        first_config = scope_configs.get(first_scope_class_id, {})

        # 获取第一个节点的实例 ID
        instance_ids = instance_ids_provider(first_scope_class_id, None)

        # 创建根节点
        root_scopes = []
        for instance_id in instance_ids:
            scope = self.get_or_create(
                scope_class_name=first_config.get("class", "GlobalScope"),
                scope_class_id=first_scope_class_id,
                scope_instance_id=instance_id,
                parent=None
            )
            root_scopes.append(scope)

        # 如果只有一个节点，直接返回
        if len(link) == 1:
            return root_scopes

        # 递归构建子树
        leaf_scopes = []
        for root_scope in root_scopes:
            leaves = self._build_scope_tree_recursive(
                link=link,
                current_index=1,
                parent_scope=root_scope,
                scope_configs=scope_configs,
                instance_ids_provider=instance_ids_provider
            )
            leaf_scopes.extend(leaves)

        return leaf_scopes

    def _build_scope_tree_recursive(
        self,
        link: list[str],
        current_index: int,
        parent_scope: BaseScope,
        scope_configs: dict[str, dict],
        instance_ids_provider: callable
    ) -> list[BaseScope]:
        """递归构建 Scope 树"""
        if current_index >= len(link):
            # 到达叶子节点
            return [parent_scope]

        current_scope_class_id = link[current_index]
        current_config = scope_configs.get(current_scope_class_id, {})

        # 获取当前层级的所有实例 ID
        instance_ids = instance_ids_provider(current_scope_class_id, parent_scope)

        # 为每个实例 ID 创建子 Scope
        leaf_scopes = []
        for instance_id in instance_ids:
            child_scope = self.get_or_create(
                scope_class_name=current_config.get("class", "BaseScope"),
                scope_class_id=current_scope_class_id,
                scope_instance_id=instance_id,
                parent=parent_scope
            )

            # 递归构建子树
            child_leaves = self._build_scope_tree_recursive(
                link=link,
                current_index=current_index + 1,
                parent_scope=child_scope,
                scope_configs=scope_configs,
                instance_ids_provider=instance_ids_provider
            )
            leaf_scopes.extend(child_leaves)

        return leaf_scopes


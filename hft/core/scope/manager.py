"""
ScopeManager - Scope 实例管理器

负责 Scope 实例的创建、缓存和管理。
"""
from typing import Dict, Tuple, Optional, Type, Callable, TYPE_CHECKING
from .base import BaseScope
from .tree import LinkedScopeNode, LinkedScopeTree
from .scopes import (
    GlobalScope,
    ExchangeClassScope,
    ExchangeScope,
    TradingPairClassScope,
    TradingPairScope,
    TradingPairClassGroupScope,
)
from .instance_ids import get_all_instance_ids

if TYPE_CHECKING:
    from ...core.app.base import AppCore


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

    def get_or_create(
        self,
        scope_class_name: str,
        scope_class_id: str,
        scope_instance_id: str,
        **kwargs
    ) -> BaseScope:
        """
        获取或创建 Scope 实例

        Args:
            scope_class_name: Scope 类名（如 "GlobalScope"）
            scope_class_id: Scope 类型 ID（用户在配置中定义，如 "global", "my_scope"）
            scope_instance_id: Scope 实例 ID
            **kwargs: 传递给 Scope 构造函数的额外参数

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
        scope = scope_class(scope_class_id, scope_instance_id, **kwargs)

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

    def build_scope_tree(
        self,
        link: list[str],
        scope_configs: dict[str, dict],
        instance_ids_provider: Callable = None,
        app_core: "AppCore" = None,
        symbol_filter: Callable[[str], bool] = None,
        exchange_filter: Callable[[str], bool] = None,
    ) -> list[LinkedScopeTree]:
        """
        根据 link 配置构建 Scope 树

        Args:
            link: Scope 链路，如 ["global", "exchange_class", "exchange", "trading_pair"]
            scope_configs: Scope 配置字典
            instance_ids_provider: 自定义实例发现函数（可选）
                签名: (scope_class_id: str, parent_node: LinkedScopeNode | None) -> list[str]
                注意：parent_node 是 LinkedScopeNode，可通过 parent_node.scope 访问 BaseScope
            app_core: AppCore 实例
            symbol_filter: 交易对过滤函数（可选）
            exchange_filter: 交易所过滤函数（可选）

        Returns:
            LinkedScopeTree 列表（每个 tree 对应一个根节点）
        """
        if not link:
            return []

        # 从第一个节点开始构建
        first_scope_class_id = link[0]
        first_config = scope_configs.get(first_scope_class_id, {})
        first_class_name = first_config.get("class", "GlobalScope")
        first_scope_class = self._scope_classes.get(first_class_name)

        # 获取第一个节点的实例 ID
        if instance_ids_provider:
            instance_ids = instance_ids_provider(first_scope_class_id, None)
        elif app_core and first_scope_class:
            instance_ids = get_all_instance_ids(
                app_core, None, None, first_scope_class,
                symbol_filter=symbol_filter,
                exchange_filter=exchange_filter,
            )
        else:
            instance_ids = ["global"] if first_class_name == "GlobalScope" else []

        # 创建根节点（LinkedScopeNode）
        trees = []
        for instance_id in instance_ids:
            scope = self.get_or_create(
                scope_class_name=first_class_name,
                scope_class_id=first_scope_class_id,
                scope_instance_id=instance_id,
                app_core=app_core,
            )
            root_node = LinkedScopeNode(scope=scope, parent=None)

            # 如果只有一个节点，直接创建树并返回
            if len(link) == 1:
                trees.append(LinkedScopeTree(root=root_node))
            else:
                # 递归构建子树
                self._build_scope_tree_recursive(
                    link=link,
                    current_index=1,
                    parent_node=root_node,
                    scope_configs=scope_configs,
                    instance_ids_provider=instance_ids_provider,
                    app_core=app_core,
                    symbol_filter=symbol_filter,
                    exchange_filter=exchange_filter,
                )
                trees.append(LinkedScopeTree(root=root_node))

        return trees

    def _build_scope_tree_recursive(
        self,
        link: list[str],
        current_index: int,
        parent_node: LinkedScopeNode,
        scope_configs: dict[str, dict],
        instance_ids_provider: Callable = None,
        app_core: "AppCore" = None,
        symbol_filter: Callable[[str], bool] = None,
        exchange_filter: Callable[[str], bool] = None,
    ) -> None:
        """递归构建 Scope 树（修改 parent_node 的 children）"""
        if current_index >= len(link):
            # 到达叶子节点
            return

        current_scope_class_id = link[current_index]
        current_config = scope_configs.get(current_scope_class_id, {})
        current_class_name = current_config.get("class", "BaseScope")
        current_scope_class = self._scope_classes.get(current_class_name)

        # 获取 parent 的 scope class
        parent_scope = parent_node.scope
        parent_class_name = scope_configs.get(
            parent_scope.scope_class_id, {}
        ).get("class", "BaseScope")
        parent_scope_class = self._scope_classes.get(parent_class_name)

        # 获取当前层级的所有实例 ID
        if instance_ids_provider:
            # 注意：现在传递 parent_node 而不是 parent_scope
            instance_ids = instance_ids_provider(current_scope_class_id, parent_node)
        elif app_core and current_scope_class and parent_scope_class:
            instance_ids = get_all_instance_ids(
                app_core, parent_scope, parent_scope_class, current_scope_class,
                symbol_filter=symbol_filter,
                exchange_filter=exchange_filter,
            )
        else:
            instance_ids = []

        # 应用 exchange_filter（如果适用）
        if exchange_filter and current_class_name in ("ExchangeScope", "ExchangeClassScope"):
            instance_ids = [
                iid for iid in instance_ids
                if exchange_filter(iid.split('/')[0] if '/' in iid else iid)
            ]

        # 为每个实例 ID 创建子节点
        for instance_id in instance_ids:
            child_scope = self.get_or_create(
                scope_class_name=current_class_name,
                scope_class_id=current_scope_class_id,
                scope_instance_id=instance_id,
                app_core=app_core,
            )

            # 创建子节点并添加到父节点
            child_node = LinkedScopeNode(scope=child_scope, parent=parent_node)
            parent_node.add_child(child_node)

            # 递归构建子树
            self._build_scope_tree_recursive(
                link=link,
                current_index=current_index + 1,
                parent_node=child_node,
                scope_configs=scope_configs,
                instance_ids_provider=instance_ids_provider,
                app_core=app_core,
                symbol_filter=symbol_filter,
                exchange_filter=exchange_filter,
            )

    def reset_all_ready_states(self) -> None:
        """
        重置所有缓存的 scope 的 ready 状态

        应在每个 tick 开始时调用
        """
        for scope in self._cache.values():
            scope.reset_ready_state()


"""
BaseScope 基类

提供多层级变量作用域的基础实现。
"""
from typing import Any, Optional
from functools import cached_property
from collections import ChainMap


class BaseScope:
    """
    Scope 基类

    特性：
    - 使用 ChainMap 实现变量继承
    - 支持 parent/children 关系
    - 提供 get_var/set_var 接口
    - 支持 not_ready 标记及级联传播

    计算顺序：
    1. Indicator 注入：首先注入所有 Indicator 提供的变量
    2. vars 计算：然后按照 Scope 树的层级顺序计算 vars

    parent/children 访问：
    - parent 可以访问 children 的 indicator 注入的变量（自下而上聚合）
    - child 可以访问 parent 的 vars 计算结果（自上而下分配）

    not_ready 机制：
    - 当 Indicator not ready 时，该 scope 及其所有 children 都标记为 not_ready
    - not_ready 的 scope 不参与 vars 计算和 target 匹配
    """

    def __init__(
        self,
        scope_class_id: str,
        scope_instance_id: str,
        parent: Optional['BaseScope'] = None
    ):
        """
        初始化 Scope

        Args:
            scope_class_id: Scope 类型 ID（如 "global", "exchange"）
            scope_instance_id: Scope 实例 ID（如 "okx/main", "ETH/USDT"）
            parent: 父 Scope
        """
        self.scope_class_id = scope_class_id
        self.scope_instance_id = scope_instance_id
        self.parent = parent
        self.children: dict[str, 'BaseScope'] = {}
        self._vars: dict[str, Any] = {}
        # not_ready 标记（每个 tick 重置）
        self._not_ready: bool = False

    @cached_property  # 缓存属性，首次计算后存储结果
    def vars(self) -> ChainMap:
        """
        获取变量 ChainMap（包含父 Scope 的变量）

        Returns:
            ChainMap: 当前 Scope 和所有父 Scope 的变量
        """
        if self.parent is None:
            return ChainMap(self._vars)
        return ChainMap(self._vars, self.parent.vars)

    def get_var(self, name: str, default: Any = None) -> Any:
        """
        获取变量值（支持从父 Scope 继承）

        Args:
            name: 变量名
            default: 默认值

        Returns:
            变量值
        """
        return self.vars.get(name, default)

    def set_var(self, name: str, value: Any) -> None:
        """
        设置变量值（仅在当前 Scope）

        Args:
            name: 变量名
            value: 变量值
        """
        self._vars[name] = value

    def add_child(self, child: 'BaseScope') -> None:
        """
        添加子 Scope

        Args:
            child: 子 Scope
        """
        self.children[child.scope_instance_id] = child

    def get_child(self, scope_instance_id: str) -> Optional['BaseScope']:
        """
        获取子 Scope

        Args:
            scope_instance_id: 子 Scope 实例 ID

        Returns:
            子 Scope，不存在则返回 None
        """
        return self.children.get(scope_instance_id)

    def __repr__(self) -> str:
        """字符串表示"""
        return f"<{self.__class__.__name__} {self.scope_class_id}:{self.scope_instance_id}>"

    def __getitem__(self, name: str) -> Any:
        """支持字典式访问变量"""
        return self.get_var(name)

    def __setitem__(self, name: str, value: Any) -> None:
        """支持字典式设置变量"""
        self.set_var(name, value)

    # ============================================================
    # not_ready 机制
    # ============================================================

    @property
    def is_not_ready(self) -> bool:
        """
        检查当前 scope 是否 not_ready

        Returns:
            True 如果该 scope 或其任何祖先被标记为 not_ready
        """
        if self._not_ready:
            return True
        # 检查祖先是否 not_ready（级联）
        if self.parent is not None:
            return self.parent.is_not_ready
        return False

    def mark_not_ready(self) -> None:
        """
        将当前 scope 标记为 not_ready

        注意：这也会使所有 children 变为 not_ready（通过 is_not_ready 级联检查）
        """
        self._not_ready = True

    def reset_ready_state(self) -> None:
        """
        重置 not_ready 状态（每个 tick 开始时调用）

        注意：只重置当前 scope，不递归到 children
        """
        self._not_ready = False

    def get_all_descendants(self) -> set['BaseScope']:
        """
        获取所有后代 scope（递归）

        Returns:
            所有后代 scope 的集合
        """
        descendants = set()
        for child in self.children.values():
            descendants.add(child)
            descendants.update(child.get_all_descendants())
        return descendants

    def get_ancestor_chain(self) -> list['BaseScope']:
        """
        获取祖先链（从 root 到 parent）

        Returns:
            祖先 scope 列表，顺序从 root 到 parent（不包含自己）
        """
        chain = []
        current = self.parent
        while current is not None:
            chain.insert(0, current)
            current = current.parent
        return chain

    def update_vars(self, vars_dict: dict[str, Any]) -> None:
        """
        批量更新变量

        Args:
            vars_dict: 要更新的变量字典
        """
        self._vars.update(vars_dict)

    def clear_vars(self) -> None:
        """清空当前 scope 的变量（不影响继承的变量）"""
        self._vars.clear()


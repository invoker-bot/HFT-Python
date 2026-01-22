"""
LinkedScopeTree - Scope 树形结构

将 Scope 本身和树形结构分离：
- BaseScope: 只存储 scope_class_id, scope_instance_id 和 _vars
- LinkedScopeNode: 由 (scope, parent) 组成，负责树形结构
- LinkedScopeTree: 管理整个树，提供树操作方法
"""
import weakref
from typing import Optional, Any
from functools import cached_property
from collections import ChainMap
from .base import BaseScope


class LinkedScopeNode:
    """
    链接的 Scope 节点

    将 Scope 和树形结构分离：
    - scope: BaseScope 实例（只包含 class_id, instance_id, vars）
    - parent: 父节点（LinkedScopeNode）
    - children: 子节点列表
    """

    def __init__(
        self,
        scope: BaseScope,
        parent: Optional['LinkedScopeNode'] = None
    ):
        """
        初始化 LinkedScopeNode

        Args:
            scope: BaseScope 实例
            parent: 父节点
        """
        self.scope = scope
        self._parent = None if parent is None else weakref.ref(parent)
        self.children: dict[str, 'LinkedScopeNode'] = {}
        self.injected_vars: dict[str, Any] = {
            "parent": self.parent,
            "children": {}
        }

    @cached_property
    def current_chain_map(self):
        return ChainMap(self.injected_vars, self.scope.vars, self.scope.functions)

    @property
    def parent(self) -> Optional['LinkedScopeNode']:
        """获取父节点"""
        return None if self._parent is None else self._parent()

    @parent.setter
    def parent(self, value: Optional['LinkedScopeNode']) -> None:
        """设置父节点"""
        if value is None:
            self._parent = None
        else:
            self._parent = weakref.ref(value)
            self.injected_vars['parent'] = value.current_chain_map

    def add_child(self, child: 'LinkedScopeNode') -> None:
        """添加子节点"""
        self.children[child.scope.scope_instance_id] = child
        self.injected_vars["children"][child.scope.scope_instance_id] = child.scope.vars

    def remove_child(self, child: 'LinkedScopeNode') -> None:
        """移除子节点"""
        del self.children[child.scope.scope_instance_id]
        del self.injected_vars["children"][child.scope.scope_instance_id]

    @cached_property
    def vars_list(self) -> list[dict]:
        """
        获取当前节点及其所有祖先节点的变量列表（从根到当前节点）

        Returns:
            变量字典列表
        """
        current_list = [self.injected_vars, self.scope.vars]
        if self.parent is not None:
            return [*current_list, *self.parent.vars_list]
        return current_list

    @cached_property
    def functions_list(self) -> list[dict]:
        """
        获取当前节点及其所有祖先节点的函数列表（从根到当前节点）

        Returns:
            函数字典列表
        """
        if self.parent is not None:
            return [self.scope.functions, *self.parent.functions_list]
        return [self.scope.functions]

    @cached_property
    def vars(self) -> ChainMap:
        """
        获取节点的变量（包含祖先变量）

        Returns:
            ChainMap: 当前节点和所有祖先节点的变量
        """
        return ChainMap(*self.vars_list)

    @cached_property
    def functions(self) -> ChainMap:
        """
        获取节点的函数（包含祖先函数）

        Returns:
            ChainMap: 当前节点和所有祖先节点的函数
        """
        return ChainMap(*self.functions_list)

    @property
    def not_ready(self) -> bool:
        """获取节点的 not_ready 状态"""
        return self.scope.not_ready

    @not_ready.setter
    def not_ready(self, value: bool) -> None:
        """
        设置节点及其所有子节点的 not_ready 状态
        """
        self.scope.not_ready = value
        for child in self.children.values():
            child.not_ready = value

    def get_all_descendants(self) -> list['LinkedScopeNode']:
        """
        获取节点的所有后代节点（递归）

        Returns:
            所有后代节点的列表
        """
        descendants = []
        for child in self.children.values():
            descendants.append(child)
            descendants.extend(child.get_all_descendants())
        return descendants

    def get_ancestor_chain(self) -> list['LinkedScopeNode']:
        """
        获取节点的祖先链（从 root 到 parent）

        Returns:
            祖先节点列表，顺序从 root 到 parent（不包含自己）
        """
        chain = []
        current = self.parent
        while current is not None:
            chain.insert(0, current)
            current = current.parent
        return chain

    def __repr__(self) -> str:
        """字符串表示"""
        return f"<LinkedScopeNode scope={self.scope}>"


class LinkedScopeTree:
    """
    链接的 Scope 树

    管理整个 Scope 树，提供树操作方法：
    - get_vars(): 获取节点的变量（包含祖先变量）
    - mark_not_ready(): 标记节点及其所有子节点为 not_ready
    - reset_ready_state(): 重置所有节点的 ready 状态
    """

    def __init__(self, root: LinkedScopeNode):
        """
        初始化 LinkedScopeTree

        Args:
            root: 根节点
        """
        self.root = root

    def get_vars(self, node: LinkedScopeNode) -> ChainMap:
        """
        获取节点的变量（包含祖先变量）

        Args:
            node: 目标节点

        Returns:
            ChainMap: 当前节点和所有祖先节点的变量
        """
        return node.vars

    def get_functions(self, node: LinkedScopeNode) -> ChainMap:
        """
        获取节点的函数（包含祖先函数）

        Args:
            node: 目标节点

        Returns:
            ChainMap: 当前节点和所有祖先节点的函数
        """
        return node.functions

    def mark_not_ready(self, node: LinkedScopeNode) -> None:
        """
        标记节点及其所有子节点为 not_ready

        Args:
            node: 目标节点
        """
        node.not_ready = True

    def reset_ready_state(self, node: LinkedScopeNode) -> None:
        """
        重置节点及其所有子节点的 ready 状态

        Args:
            node: 目标节点
        """
        node.not_ready = False

    def get_all_descendants(self, node: LinkedScopeNode) -> list[LinkedScopeNode]:
        """
        获取节点的所有后代节点（递归）

        Args:
            node: 目标节点

        Returns:
            所有后代节点的列表
        """
        return node.get_all_descendants()

    def get_ancestor_chain(self, node: LinkedScopeNode) -> list[LinkedScopeNode]:
        """
        获取节点的祖先链（从 root 到 parent）

        Args:
            node: 目标节点

        Returns:
            祖先节点列表，顺序从 root 到 parent（不包含自己）
        """
        return node.get_ancestor_chain()

    def __repr__(self) -> str:
        """字符串表示"""
        return f"<LinkedScopeTree root={self.root}>"

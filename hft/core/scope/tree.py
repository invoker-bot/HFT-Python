"""
LinkedScopeTree - Scope 树形结构

将 Scope 本身和树形结构分离：
- BaseScope: 只存储 scope_class_id, scope_instance_id 和 _vars
- LinkedScopeNode: 由 (scope, parent) 组成，负责树形结构
- LinkedScopeTree: 管理整个树，提供树操作方法
"""
from typing import Optional, Any
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
        self.parent = parent
        self.children: list['LinkedScopeNode'] = []

    def add_child(self, child: 'LinkedScopeNode') -> None:
        """添加子节点"""
        self.children.append(child)

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
        if node.parent is None:
            return ChainMap(node.scope._vars)
        return ChainMap(node.scope._vars, self.get_vars(node.parent))

    def mark_not_ready(self, node: LinkedScopeNode) -> None:
        """
        标记节点及其所有子节点为 not_ready

        Args:
            node: 目标节点
        """
        node.scope._not_ready = True
        for child in node.children:
            self.mark_not_ready(child)

    def reset_ready_state(self, node: LinkedScopeNode) -> None:
        """
        重置节点及其所有子节点的 ready 状态

        Args:
            node: 目标节点
        """
        node.scope._not_ready = False
        for child in node.children:
            self.reset_ready_state(child)

    def get_all_descendants(self, node: LinkedScopeNode) -> list[LinkedScopeNode]:
        """
        获取节点的所有后代节点（递归）

        Args:
            node: 目标节点

        Returns:
            所有后代节点的列表
        """
        descendants = []
        for child in node.children:
            descendants.append(child)
            descendants.extend(self.get_all_descendants(child))
        return descendants

    def get_ancestor_chain(self, node: LinkedScopeNode) -> list[LinkedScopeNode]:
        """
        获取节点的祖先链（从 root 到 parent）

        Args:
            node: 目标节点

        Returns:
            祖先节点列表，顺序从 root 到 parent（不包含自己）
        """
        chain = []
        current = node.parent
        while current is not None:
            chain.insert(0, current)
            current = current.parent
        return chain

    def __repr__(self) -> str:
        """字符串表示"""
        return f"<LinkedScopeTree root={self.root}>"

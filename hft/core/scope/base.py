"""
BaseScope 基类

提供多层级变量作用域的基础实现。
"""
from typing import Any, Optional, TYPE_CHECKING
from collections import ChainMap

if TYPE_CHECKING:
    pass


class BaseScope:
    """
    Scope 基类

    特性：
    - 使用 ChainMap 实现变量继承
    - 支持 parent/children 关系
    - 提供 get_var/set_var 接口

    计算顺序：
    1. Indicator 注入：首先注入所有 Indicator 提供的变量
    2. vars 计算：然后按照 Scope 树的层级顺序计算 vars

    parent/children 访问：
    - parent 可以访问 children 的 indicator 注入的变量（自下而上聚合）
    - child 可以访问 parent 的 vars 计算结果（自上而下分配）
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

    @property
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


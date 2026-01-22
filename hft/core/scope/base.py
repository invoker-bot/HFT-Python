"""
BaseScope 基类

提供变量和函数作用域的基础实现。

注意：BaseScope 只存储 scope_class_id, scope_instance_id, _vars 和 _functions。
树形结构由 LinkedScopeNode 和 LinkedScopeTree 管理。
"""
from typing import Any, Callable


class BaseScope:
    """
    Scope 基类

    特性：
    - 只存储 scope_class_id, scope_instance_id, _vars 和 _functions
    - 不记录 parent/children（由 LinkedScopeTree 管理）
    - 提供 get_var/set_var 和 get_function/set_function 接口
    - 支持 not_ready 标记

    计算顺序：
    1. Indicator 注入：首先注入所有 Indicator 提供的变量和函数
    2. vars 计算：通过 LinkedScopeTree.get_vars() 获取（包含祖先变量）
    3. functions 计算：通过 LinkedScopeTree.get_functions() 获取（包含祖先函数）

    not_ready 机制：
    - 当 Indicator not ready 时，通过 LinkedScopeTree.mark_not_ready() 标记
    - not_ready 的 scope 不参与 vars 计算和 target 匹配
    """

    def __init__(
        self,
        scope_class_id: str,
        scope_instance_id: str
    ):
        """
        初始化 Scope

        Args:
            scope_class_id: Scope 类型 ID（如 "global", "exchange"）
            scope_instance_id: Scope 实例 ID（如 "okx/main", "ETH/USDT"）
        """
        self.scope_class_id = scope_class_id
        self.scope_instance_id = scope_instance_id
        self._vars: dict[str, Any] = {}
        self._functions: dict[str, Callable] = {}
        # not_ready 标记（每个 tick 重置）
        self._not_ready: bool = False

    @property
    def vars(self) -> dict[str, Any]:
        return self._vars

    @property
    def functions(self) -> dict[str, Any]:
        return self._functions

    def get_var(self, name: str, default: Any = None) -> Any:
        """
        获取变量值（仅从当前 Scope）

        注意：要获取包含祖先变量的值，请使用 LinkedScopeTree.get_vars()

        Args:
            name: 变量名
            default: 默认值

        Returns:
            变量值
        """
        return self._vars.get(name, default)

    def set_var(self, name: str, value: Any) -> None:
        """
        设置变量值（仅在当前 Scope）

        Args:
            name: 变量名
            value: 变量值
        """
        self._vars[name] = value

    def get_function(self, name: str, default: Callable = None) -> Callable:
        """
        获取函数（仅从当前 Scope）

        注意：要获取包含祖先函数的值，请使用 LinkedScopeTree.get_functions()

        Args:
            name: 函数名
            default: 默认值

        Returns:
            函数对象
        """
        return self._functions.get(name, default)

    def set_function(self, name: str, func: Callable) -> None:
        """
        设置函数（仅在当前 Scope）

        Args:
            name: 函数名
            func: 函数对象
        """
        self._functions[name] = func

    def __repr__(self) -> str:
        """字符串表示"""
        return f"<{self.__class__.__name__} {self.scope_class_id}:{self.scope_instance_id}>"

    def __getitem__(self, name: str) -> Any:
        """支持字典式访问变量"""
        return self.get_var(name)

    def __setitem__(self, name: str, value: Any) -> None:
        """支持字典式设置变量"""
        self.set_var(name, value)

    @property
    def not_ready(self) -> bool:
        """当前 scope 是否被标记为 not_ready。"""
        return self._not_ready

    @not_ready.setter
    def not_ready(self, value: bool) -> None:
        """设置当前 scope 的 not_ready 状态。"""
        self._not_ready = value

    def update_vars(self, vars_dict: dict[str, Any]) -> None:
        """
        批量更新变量

        Args:
            vars_dict: 要更新的变量字典
        """
        self._vars.update(vars_dict)

    def update_functions(self, functions_dict: dict[str, Callable]) -> None:
        """
        批量更新函数

        Args:
            functions_dict: 要更新的函数字典
        """
        self._functions.update(functions_dict)

    def clear_vars(self) -> None:
        """清空当前 scope 的变量（不影响继承的变量）"""
        self._vars.clear()

    def clear_functions(self) -> None:
        """清空当前 scope 的函数（不影响继承的函数）"""
        self._functions.clear()

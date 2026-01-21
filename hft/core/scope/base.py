"""
BaseScope 基类

提供变量作用域的基础实现。

注意：BaseScope 只存储 scope_class_id, scope_instance_id 和 _vars。
树形结构由 LinkedScopeNode 和 LinkedScopeTree 管理。
"""
from typing import Any


class BaseScope:
    """
    Scope 基类

    特性：
    - 只存储 scope_class_id, scope_instance_id 和 _vars
    - 不记录 parent/children（由 LinkedScopeTree 管理）
    - 提供 get_var/set_var 接口
    - 支持 not_ready 标记

    计算顺序：
    1. Indicator 注入：首先注入所有 Indicator 提供的变量
    2. vars 计算：通过 LinkedScopeTree.get_vars() 获取（包含祖先变量）

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
        # not_ready 标记（每个 tick 重置）
        self._not_ready: bool = False

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
            True 如果该 scope 被标记为 not_ready
        """
        return self._not_ready

    def mark_not_ready(self) -> None:
        """
        将当前 scope 标记为 not_ready

        注意：要标记整个子树，请使用 LinkedScopeTree.mark_not_ready()
        """
        self._not_ready = True

    def reset_ready_state(self) -> None:
        """
        重置 not_ready 状态（每个 tick 开始时调用）

        注意：只重置当前 scope
        """
        self._not_ready = False

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


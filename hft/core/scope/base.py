"""
BaseScope 基类

提供变量和函数作用域的基础实现。

注意：BaseScope 只存储 scope_class_id, scope_instance_id, _vars 和 _functions。
树形结构由 LinkedScopeNode 和 LinkedScopeTree 管理。
"""
import inspect
from functools import cache
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from ...core.app.base import AppCore


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
        scope_instance_id: str,
        app_core: "AppCore" = None,
    ):
        """
        初始化 Scope

        Args:
            scope_class_id: Scope 类型 ID（如 "global", "exchange"）
            scope_instance_id: Scope 实例 ID（如 "okx/main", "ETH/USDT"）
        """
        self.scope_class_id = scope_class_id
        self.scope_instance_id = scope_instance_id
        self.app_core = app_core
        self._vars: dict[str, Any] = {
            "instance_id": scope_instance_id,
            "class_id": scope_class_id,
            "app_core": app_core,
        }
        self._functions: dict[str, Callable] = {}
        # not_ready 标记（每个 tick 重置）
        self._not_ready: bool = False

        # 调用子类的 initialize 方法设置 functions 和普通 vars
        self.initialize()

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

    @classmethod
    def all_classes(cls) -> dict[str, type['BaseScope']]:
        """
        递归获取所有子类

        Returns:
            字典，键为类名，值为类类型
        """
        result = {}
        # 使用 cls.__name__ 而不是 cls.__class__.__name__
        if not inspect.isabstract(cls):
            result[cls.__name__] = cls
        for subcls in cls.__subclasses__():
            result.update(subcls.all_classes())
        return result

    def initialize(self) -> None:
        """
        初始化 Scope 的 functions 和普通 vars

        此方法在 __init__ 和 __setstate__ 时调用，用于设置：
        - functions（所有函数）
        - 普通 vars（非条件变量）

        条件变量（带 on 字段的变量）不在这里设置，它们的状态会被 pickle 保存。

        子类应该重写此方法来设置自己的 functions 和 vars。
        """
        pass  # 基类不需要设置任何东西，由子类重写

    def __getstate__(self) -> dict:
        """
        Pickle 序列化：只保存条件变量的状态

        保存内容：
        - scope_class_id, scope_instance_id
        - 条件变量的值和时间戳（以 __ 开头的时间戳变量）
        - not_ready 标记

        不保存：
        - app_core（不可序列化）
        - functions（通过 initialize() 重建）
        - 普通 vars（通过 initialize() 重建）
        """
        state = {
            'scope_class_id': self.scope_class_id,
            'scope_instance_id': self.scope_instance_id,
            '_not_ready': self._not_ready,
        }

        # 只保存条件变量（有对应时间戳的变量）
        conditional_vars = {}
        for key, value in self._vars.items():
            # 保存时间戳变量
            if key.startswith('__') and key.endswith('_last_update_time'):
                conditional_vars[key] = value
                # 同时保存对应的条件变量值
                var_name = key[2:-len('_last_update_time')]
                if var_name in self._vars:
                    conditional_vars[var_name] = self._vars[var_name]

        state['conditional_vars'] = conditional_vars
        return state

    def __setstate__(self, state: dict) -> None:
        """
        Pickle 反序列化：恢复条件变量状态并重建其他内容

        恢复步骤：
        1. 恢复基本属性（scope_class_id, scope_instance_id）
        2. 重建基础 _vars（instance_id, class_id, app_core=None）
        3. 恢复条件变量的值和时间戳
        4. 调用 initialize() 重建 functions 和普通 vars
        """
        self.scope_class_id = state['scope_class_id']
        self.scope_instance_id = state['scope_instance_id']
        self.app_core = None  # 反序列化时 app_core 需要外部重新设置
        self._not_ready = state.get('_not_ready', False)

        # 重建基础 _vars
        self._vars = {
            "instance_id": self.scope_instance_id,
            "class_id": self.scope_class_id,
            "app_core": None,
        }

        # 恢复条件变量
        conditional_vars = state.get('conditional_vars', {})
        self._vars.update(conditional_vars)

        # 重建 functions
        self._functions = {}

        # 调用 initialize() 重建 functions 和普通 vars
        self.initialize()

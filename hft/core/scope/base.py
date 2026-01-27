"""
BaseScope 基类

提供变量和函数作用域的基础实现。

注意：BaseScope 只存储 scope_class_id, scope_instance_id, _vars 和 _functions。
树形结构由 LinkedScopeNode 和 LinkedScopeTree 管理。
"""
import time
import inspect
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

    def __init__(self, **kwargs):
        """
        初始化 Scope

        Args:
            class_id: Scope 类型 ID（如 "global", "exchange"）
            instance_id: Scope 实例 ID（如 "okx/main", "ETH/USDT"）
        """
        self._vars: dict[str, Any] = {
        }
        self._conditional_vars_update_times: dict[str, float] = {
        }
        # not_ready 标记（每个 tick 重置, 默认为ready）
        self._not_ready: bool = False

        # 调用子类的 initialize 方法设置 functions 和普通 vars
        self.initialize(**kwargs)

    def initialize(self, **kwargs) -> None:
        """
        初始化 Scope 的 functions 和普通 vars

        此方法在 __init__ 和 __setstate__ 时调用，用于设置：
        - functions（所有函数）
        - 普通 vars（非条件变量）

        条件变量（带 on 字段的变量）不在这里设置，它们的状态会被 pickle 保存。

        子类应该重写此方法来设置自己的 functions 和 vars。
        """
        self._instance_id: str = kwargs['instance_id']
        self._class_id: str = kwargs['class_id']
        self._app_core: 'AppCore' = kwargs['app_core']  # must be set before use
        self._functions: dict[str, Callable] = {}
        self._vars.update({
            "instance_id": self._instance_id,
            "class_id": self._class_id,
            "app_core": self._app_core,
        })

    @property
    def instance_id(self) -> str:
        return self._instance_id

    @property
    def class_id(self) -> str:
        return self._class_id

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

    def set_var(self, name: str, value: Any, conditional: bool = False) -> None:
        """
        设置变量值（仅在当前 Scope）

        Args:
            name: 变量名
            value: 变量值
        """
        self._vars[name] = value
        if conditional:
            self._conditional_vars_update_times[name] = time.time()

    def get_var_update_time(self, name: str) -> float:
        """
        获取变量的最后更新时间戳

        Args:
            name: 变量名

        Returns:
            最后更新时间戳，变量不存在或非条件变量则返回 0
        """
        return self._conditional_vars_update_times.get(name, 0.0)

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
        # 只持久化保存条件变量（有对应时间戳的变量）
        _vars = {key: value for key, value in self._vars.items() if key in self._conditional_vars_update_times}
        state = {
            '_not_ready': self._not_ready,
            '_conditional_vars_update_times': self._conditional_vars_update_times,
            '_vars': _vars,
        }
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
        self.__dict__.update(state)

        # 调用 initialize() 重建 functions 和普通 vars
        self.initialize(state['kwargs'])

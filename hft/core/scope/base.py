"""
BaseScope 基类

提供变量和函数作用域的基础实现。

注意：BaseScope 只存储 scope_class_id, scope_instance_id, _vars 和 _functions。
树形结构由 LinkedScopeNode 和 LinkedScopeTree 管理。
"""
import time
import inspect
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Callable, Mapping
from collections import ChainMap
from functools import cached_property
if TYPE_CHECKING:
    from ...core.app.base import AppCore

ScopeInstanceId = tuple[str, ...]


class VirtualScope(ABC):
    """
    虚拟 Scope 基类

    特性：
    - 不存储任何状态
    - 只提供变量和函数接口
    - 适用于临时计算或无状态场景
    """

    @property
    @abstractmethod
    def vars(self) -> Mapping[str, Any]:
        """获取变量字典"""

    @abstractmethod
    def get_var(self, name: str, default: Any = None) -> Any:
        """获取变量值"""

    @abstractmethod
    def set_var(self, name: str, value: Any, conditional: bool = False) -> None:
        """设置变量值"""

    @abstractmethod
    def get_var_update_time(self, name: str) -> float:
        """获取变量的最后更新时间戳"""

    @property
    @abstractmethod
    def functions(self) -> Mapping[str, Callable]:
        """获取函数字典"""

    @abstractmethod
    def get_function(self, name: str, default: Callable = None) -> Callable:
        """获取函数"""

    @abstractmethod
    def set_function(self, name: str, func: Callable) -> None:
        """设置函数"""


class BaseScope(VirtualScope):
    """
    Scope 基类

    特性：
    - 只存储 scope_class_id, scope_instance_id, _vars 和 _functions
    - 不记录 parent/children（由 LinkedScopeTree 管理）
    - 提供 get_var/set_var 和 get_function/set_function 接口

    计算顺序：
    1. Indicator 注入：首先注入所有 Indicator 提供的变量和函数
    2. vars 计算：通过 LinkedScopeTree.get_vars() 获取（包含祖先变量）
    3. functions 计算：通过 LinkedScopeTree.get_functions() 获取（包含祖先函数）

    not_ready 机制：
    - 当 Indicator not ready 时，通过 LinkedScopeTree.mark_not_ready() 标记
    """
    def __init__(self, **kwargs):
        """
        初始化 Scope

        Args:
            class_id: Scope 类型 ID（如 "global", "exchange"）
            instance_id: Scope 实例 ID（如 ("okx/main", ), ("ETH/USDT", ))
        """
        self._vars: dict[str, Any] = {
        }
        self._conditional_vars_update_times: dict[str, float] = {
        }
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
        self._instance_id: ScopeInstanceId = kwargs['instance_id']
        self._app_core: 'AppCore' = kwargs['app_core']  # must be set before use?
        self._functions: dict[str, Callable] = {}
        self._vars.update({
            "instance_id": self._instance_id,
            "class_name": self.class_name,
            "app_core": self._app_core,
        })

    @property
    def instance_id(self) -> ScopeInstanceId:
        return self._instance_id

    @property
    def class_name(self) -> str:
        return self.__class__.__name__

    @classmethod
    def calculate_id(cls, class_name: str, instance_id: ScopeInstanceId) -> str:
        return f"{class_name}-{'-'.join(instance_id)}"

    @property
    def id(self) -> str:
        return self.calculate_id(self.class_name, self._instance_id)
    @property
    def app_core(self) -> 'AppCore':
        return self._app_core

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
        *namespace, actual_name = name.split(".")
        current_level = self._vars
        for ns in namespace:
            if ns not in current_level:
                return default
            current_level = current_level[ns]
        return current_level.get(actual_name, default)

    def set_var(self, name: str, value: Any, conditional: bool = False) -> None:
        """
        设置变量值（仅在当前 Scope）

        Args:
            name: 变量名
            value: 变量值
        """
        *namespace, actual_name = name.split(".")
        current_level = self._vars
        for ns in namespace:
            if ns not in current_level:
                current_level[ns] = {}
            current_level = current_level[ns]
        current_level[actual_name] = value
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
        return f"<{self.class_name}:{self.instance_id}>"

    def __getitem__(self, name: str) -> Any:
        """支持字典式访问变量"""
        return self.get_var(name)

    def __setitem__(self, name: str, value: Any) -> None:
        """支持字典式设置变量"""
        self.set_var(name, value)

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

    classes = {}

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
            # '_not_ready': self._not_ready,
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
        self.initialize(**state['kwargs'])

    def __hash__(self):
        return id(self)

    def __eq__(self, value):
        return id(self) == id(value)

    flow_mapper = {
        # Scope: [map functions], current instance_id -> mapped instance_id
    }

    @classmethod
    def instance_id_map_func(cls, Scope: type['BaseScope'], instance_id: ScopeInstanceId) -> ScopeInstanceId:
        """默认的 instance_id 映射函数，直接返回原始 instance_id"""
        map_func_lists = cls.flow_mapper[Scope]
        for func in map_func_lists:
            instance_id = func(instance_id)
        return instance_id

    @classmethod
    @abstractmethod
    def get_all_instance_ids(cls, app_core: 'AppCore') -> set[ScopeInstanceId]:
        ...

    @classmethod
    def update_flow_mapper(cls, Scope: type['BaseScope'], functions: list[str]) -> None:
        # 给 self 添加映射
        # cls.flow_mapper[scope] = functions
        for parent_scope, parent_functions in list(Scope.flow_mapper.items()):
            # if issubclass(scope, parent_scope):
                # 继承父类的映射
            combined_functions = functions + parent_functions
            cls.flow_mapper[parent_scope] = combined_functions
            cls.update_flow_mapper(parent_scope, combined_functions)

    def __init_subclass__(cls, **kwargs):
        cls.update_flow_mapper(cls, [])
        if not inspect.isabstract(cls):
            BaseScope.classes[cls.__name__] = cls  # 注册子类
        # for parent_scope, parent_functions in cls.flow_mapper.items():
        #     cls.update_flow_mapper(parent_scope, parent_functions)
        # print("class registered:", cls.__name__)


class FlowScopeNode(VirtualScope):
    """
    链接的 Scope 节点

    将 Scope 和树形结构分离：
    - scope: BaseScope 实例（只包含 class_id, instance_id, vars）
    """

    def __init__(
        self,
        scope: BaseScope,
        prev: list['FlowScopeNode']
    ):
        """
        初始化 LinkedScopeNode

        Args:
            scope: BaseScope 实例
            prev: 父节点列表
        """
        self.scope = scope
        self.prev = prev
        self.injected_vars: dict[str, Any] = {
            "prev": [prev_node.current_chain_map for prev_node in prev],
        }

    @cached_property
    def current_chain_map(self):
        return ChainMap(self.injected_vars, self.scope.vars, self.scope.functions)

    @cached_property
    def vars_list(self) -> list[dict]:
        """
        获取当前节点及其所有祖先节点的变量列表（从根到当前节点）

        Returns:
            变量字典列表
        """
        current_list = [self.injected_vars, self.scope.vars]
        if len(self.prev) > 0:
            return [*current_list, *self.prev[0].vars_list]
        return current_list

    @cached_property
    def vars_update_times_list(self) -> list[dict]:
        """
        获取当前节点及其所有祖先节点的变量更新时间戳列表（从根到当前节点）

        Returns:
            变量更新时间戳字典列表
        """
        current = self.scope._conditional_vars_update_times
        if len(self.prev) > 0:
            return [current, *self.prev[0].vars_update_times_list]
        return [current]

    @cached_property
    def vars_update_times(self) -> ChainMap:
        """
        获取节点的变量更新时间戳（包含祖先变量）

        Returns:
            ChainMap: 当前节点和所有祖先节点的变量更新时间戳
        """
        return ChainMap(*self.vars_update_times_list)

    @cached_property
    def functions_list(self) -> list[dict]:
        """
        获取当前节点及其所有祖先节点的函数列表（从根到当前节点）

        Returns:
            函数字典列表
        """
        if len(self.prev) > 0:
            return [self.scope.functions, *self.prev[0].functions_list]
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

    def set_var(self, name: str, value: Any, conditional: bool = False) -> None:
        """
        设置变量（委托给 scope）

        Args:
            name: 变量名
            value: 变量值
        """
        self.scope.set_var(name, value, conditional=conditional)

    def get_var(self, name, default = None):
        return self.vars.get(name, default)

    def get_var_update_time(self, name):
        return self.vars_update_times.get(name, 0.0)

    def set_function(self, name: str, func: Any) -> None:
        """
        设置函数（委托给 scope）

        Args:
            name: 函数名
            func: 函数对象
        """
        self.scope.set_function(name, func)

    def get_function(self, name, default = None):
        return self.functions.get(name, default)

    def __repr__(self) -> str:
        """字符串表示"""
        prev = [node.scope for node in self.prev]
        return f"<LinkedScopeNode scope={self.scope} prev={prev}>"

    def search_prev_scope(self, scope_class: type[BaseScope]) -> 'FlowScopeNode':
        """查找前向节点"""
        node = self
        while node is not None:
            if isinstance(node.scope, scope_class):
                break
            if len(node.prev) > 0:
                node = node.prev[0]
            else:
                node = None
        return node

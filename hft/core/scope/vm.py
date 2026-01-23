"""
VirtualMachine - 表达式求值引擎

基于 simpleeval 实现安全的表达式求值。
"""
import time
from typing import TYPE_CHECKING, Any, Optional, Union

from simpleeval import (DEFAULT_FUNCTIONS, DEFAULT_NAMES, DEFAULT_OPERATORS,
                        EvalWithCompoundTypes)

from .tree import LinkedScopeNode

if TYPE_CHECKING:
    from .base import BaseScope


class VirtualMachine:
    """
    虚拟机 - 统一的表达式求值引擎

    特性：
    - 基于 simpleeval.safe_eval
    - 提供安全的表达式求值环境
    - 支持自定义函数和变量
    - 默认函数从 GlobalScope 获取
    """

    def __init__(self):
        """初始化虚拟机"""
        # 默认函数（来自 simpleeval）
        # 注意：常用函数（min, max, sum, len, abs, round, clip, avg）
        # 在 GlobalScope 中定义，会通过 Scope 树继承传递给所有子节点
        self.functions = DEFAULT_FUNCTIONS.copy()

        # 默认操作符（使用 simpleeval 的默认配置）
        self.operators = DEFAULT_OPERATORS.copy()
        self.names = DEFAULT_NAMES.copy()
        self.evaler = EvalWithCompoundTypes(
            operators=self.operators, functions=self.functions, names=self.names
        )

    def eval(
        self,
        expression: Any,
        scope: Optional[Union['BaseScope', 'LinkedScopeNode']] = None,
    ) -> Any:
        """
        求值表达式

        Args:
            expression: 表达式字符串或字面量值
                - 如果是字符串，会作为表达式求值
                - 如果是其他类型，直接返回原值
            scope: Scope 对象（可选）
                - BaseScope: 使用 scope.vars 和 scope.functions
                - LinkedScopeNode: 使用 node.vars 和 node.functions（包含祖先继承）
                - None: 使用默认的 simpleeval 函数和变量

        Returns:
            求值结果
        """
        if not isinstance(expression, str):
            return expression

        # 处理不同类型的 scope 参数
        if scope is None:
            # 没有 scope，使用默认函数和变量
            self.evaler.functions = self.functions
            self.evaler.names = self.names
        else:
            # 有 scope，合并默认函数和 scope 的函数（scope 的函数优先）
            merged_functions = self.functions.copy()
            merged_functions.update(scope.functions)
            self.evaler.functions = merged_functions
            self.evaler.names = scope.vars
        return self.evaler.eval(expression)

    def execute(
        self,
        vars_config: Union[list, dict],
        scope: Union['BaseScope', 'LinkedScopeNode'],
    ) -> None:
        """
        执行变量赋值：将 vars_config 中的表达式求值后赋值到 scope

        支持三种格式（可混合使用）：
        1. 标准格式（list[dict]）：
           [{"name": "var_name", "value": "expression", "on": "condition", "initial_value": value}]
        2. dict 简化格式：
           {"var_name": "expression"}
        3. list[str] 简化格式：
           ["var_name=expression"]

        Args:
            vars_config: 变量配置，支持以下格式：
                - list[dict]: 标准格式，支持条件变量和初始值
                - list[str]: 简化格式，使用 "name=value" 格式
                - dict: 简化格式，{var_name: expression}
                - 混合格式：list 中可以混合 dict 和 str
            scope: 目标 Scope 对象
                - BaseScope: 直接赋值到 scope._vars
                - LinkedScopeNode: 赋值到 node.scope._vars

        Example:
            vm = VirtualMachine()
            scope = BaseScope("test", "test_instance")
            scope.set_var("x", 10)

            # 格式 1：标准格式
            vm.execute([
                {"name": "y", "value": "x * 2"},
                {"name": "z", "value": "mid_price", "on": "position == 0", "initial_value": 100}
            ], scope)

            # 格式 2：dict 简化格式
            vm.execute({"y": "x * 2", "z": 100}, scope)

            # 格式 3：list[str] 简化格式
            vm.execute(["y=x * 2", "z=100"], scope)

            # 混合格式
            vm.execute([
                "y=x * 2",
                {"name": "z", "value": "mid_price", "on": "position == 0"}
            ], scope)
        """
        # 获取实际的 BaseScope 对象
        target_scope = scope.scope if isinstance(scope, LinkedScopeNode) else scope

        # 标准化为 list[dict] 格式
        normalized_vars = self._normalize_vars_config(vars_config)

        # 按顺序处理每个变量
        for var_def in normalized_vars:
            self._execute_single_var(var_def, scope, target_scope)

    def _normalize_vars_config(self, vars_config: Union[list, dict]) -> list[dict]:
        """
        标准化 vars 配置为统一的 list[dict] 格式

        Args:
            vars_config: 原始配置（list/dict）

        Returns:
            标准化后的 list[dict]，每个 dict 包含：
            - name: 变量名
            - value: 表达式
            - on: 条件表达式（可选）
            - initial_value: 初始值（可选）
        """
        if isinstance(vars_config, dict):
            # 格式 2：dict 简化格式 {"var_name": "expression"}
            return [{"name": name, "value": value} for name, value in vars_config.items()]

        if isinstance(vars_config, list):
            result = []
            for item in vars_config:
                if isinstance(item, str):
                    # 格式 3：list[str] 简化格式 "var_name=expression"
                    if "=" not in item:
                        raise ValueError(f"Invalid var format: {item}, expected 'name=value'")
                    name, value = item.split("=", 1)
                    result.append({"name": name.strip(), "value": value.strip()})
                elif isinstance(item, dict):
                    # 格式 1：标准格式（已经是 dict）
                    if "name" not in item:
                        raise ValueError(f"Invalid var format: {item}, missing 'name' field")
                    if "value" not in item:
                        raise ValueError(f"Invalid var format: {item}, missing 'value' field")
                    result.append(item)
                else:
                    raise ValueError(f"Invalid var format: {item}, expected str or dict")
            return result

        raise ValueError(f"Invalid vars_config type: {type(vars_config)}, expected list or dict")

    def _execute_single_var(
        self,
        var_def: dict,
        scope: Union['BaseScope', 'LinkedScopeNode'],
        target_scope: 'BaseScope',
    ) -> None:
        """
        执行单个变量的赋值

        Args:
            var_def: 变量定义，包含 name, value, on（可选）, initial_value（可选）
            scope: 用于表达式求值的 scope（可能是 LinkedScopeNode，包含继承的变量）
            target_scope: 实际赋值的 BaseScope 对象
        """
        name = var_def["name"]
        value_expr = var_def["value"]
        condition_expr = var_def.get("on", True)
        initial_value = var_def.get("initial_value", None)

        # 如果没有条件，直接求值并赋值
        if condition_expr is None:
            value = self.eval(value_expr, scope)
            target_scope.set_var(name, value)
            return

        # 有条件的变量：需要检查条件是否满足
        # 计算 duration（距上次更新的秒数）
        last_update_time = target_scope.get_var(f"__{name}_last_update_time", 0.0)
        duration = time.time() - last_update_time
        scope.set_var("duration", duration)
        # 求值条件表达式
        condition_result = self.eval(condition_expr, scope)
        if condition_result:
            # 条件满足，更新变量值和时间戳
            value = self.eval(value_expr, scope)
            target_scope.set_var(name, value)
            target_scope.set_var(f"__{name}_last_update_time", time.time())
        else:
            # 条件不满足，检查是否需要设置初始值
            if name not in target_scope.vars:
                target_scope.set_var(name, initial_value)

"""
VirtualMachine - 表达式求值引擎

基于 simpleeval.safe_eval 实现安全的表达式求值。
"""
from typing import Any, Optional, TYPE_CHECKING
from simpleeval import EvalWithCompoundTypes, DEFAULT_FUNCTIONS, DEFAULT_OPERATORS, DEFAULT_NAMES
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
        from .scopes import GlobalScope

        # 默认函数（来自 simpleeval）
        self.functions = DEFAULT_FUNCTIONS.copy()

        # 添加 GlobalScope 的常用函数
        # 创建一个临时 GlobalScope 实例来获取函数
        temp_global = GlobalScope(scope_class_id="global", scope_instance_id="global")
        self.functions.update(temp_global._functions)

        # 默认操作符（使用 simpleeval 的默认配置）
        self.operators = DEFAULT_OPERATORS.copy()
        self.names = DEFAULT_NAMES.copy()
        self.evaler = EvalWithCompoundTypes(operators=self.operators, functions=self.functions, names=self.names)

    def eval(
        self,
        expression: Any,
        names: Optional[Any] = None,
        functions: Optional[Any] = None
    ) -> Any:
        """
        求值表达式

        Args:
            expression: 表达式
            names: 变量字典、BaseScope、或 (LinkedScopeNode, LinkedScopeTree) 元组
            functions: 函数字典（可选）

        Returns:
            求值结果
        """
        if not isinstance(expression, str):
            return expression

        # 处理不同类型的 names 参数
        if names is not None:
            if isinstance(names, dict):
                # 直接使用字典
                self.evaler.names = names
            elif hasattr(names, '_vars'):
                # BaseScope 对象
                self.evaler.names = names._vars
            elif isinstance(names, tuple) and len(names) == 2:
                # (LinkedScopeNode, LinkedScopeTree) 元组
                node, tree = names
                self.evaler.names = dict(tree.get_vars(node))
            else:
                # 其他情况，尝试直接使用
                self.evaler.names = names

        # 处理函数参数
        if functions is not None:
            # 合并默认函数和自定义函数
            merged_functions = self.functions.copy()
            if isinstance(functions, dict):
                merged_functions.update(functions)
            elif hasattr(functions, '_functions'):
                # BaseScope 对象
                merged_functions.update(functions._functions)
            elif isinstance(functions, tuple) and len(functions) == 2:
                # (LinkedScopeNode, LinkedScopeTree) 元组
                node, tree = functions
                merged_functions.update(dict(tree.get_functions(node)))
            self.evaler.functions = merged_functions

        return self.evaler.eval(expression)

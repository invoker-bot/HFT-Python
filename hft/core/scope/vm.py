"""
VirtualMachine - 表达式求值引擎

基于 simpleeval.safe_eval 实现安全的表达式求值。
"""
from typing import Any, Dict, Optional
from simpleeval import simple_eval, DEFAULT_FUNCTIONS, DEFAULT_OPERATORS
import operator


class VirtualMachine:
    """
    虚拟机 - 统一的表达式求值引擎

    特性：
    - 基于 simpleeval.safe_eval
    - 提供安全的表达式求值环境
    - 支持自定义函数和变量
    """

    def __init__(self):
        """初始化虚拟机"""
        # 默认函数（来自 simpleeval）
        self.functions = DEFAULT_FUNCTIONS.copy()

        # 添加常用函数
        self.functions.update({
            'min': min,
            'max': max,
            'sum': sum,
            'len': len,
            'abs': abs,
            'round': round,
            'clip': lambda x, min_val, max_val: max(min_val, min(x, max_val)),
            'avg': lambda lst: sum(lst) / len(lst) if lst else 0,
        })

        # 默认操作符（使用 simpleeval 的默认配置）
        self.operators = DEFAULT_OPERATORS.copy()

    def eval(
        self,
        expression: str,
        names: Optional[Dict[str, Any]] = None
    ) -> Any:
        """
        求值表达式

        Args:
            expression: 表达式字符串
            names: 变量字典

        Returns:
            求值结果
        """
        if names is None:
            names = {}

        return simple_eval(
            expression,
            functions=self.functions,
            names=names,
            operators=self.operators
        )

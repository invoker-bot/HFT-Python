"""
VirtualMachine - 表达式求值引擎

基于 simpleeval 实现安全的表达式求值。
"""
from typing import TYPE_CHECKING, Any, Optional
from collections import defaultdict
from simpleeval import (DEFAULT_FUNCTIONS, DEFAULT_NAMES, DEFAULT_OPERATORS,
                        EvalWithCompoundTypes)
from .base import VirtualScope, FlowScopeNode, ScopeInstanceId
from ...indicator.base import BaseIndicator
if TYPE_CHECKING:
    from ...config.var import StandardVarDefinition, StandardVarsDefinition
    from ...config.scope import ScopeFlowConfig
    from ..app.base import AppCore
    from .manager import ScopeManager

ScopeFlowLayers = list[dict[ScopeInstanceId, 'FlowScopeNode']]

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
        scope: Optional['VirtualScope'] = None,
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
            self.evaler.functions = scope.functions
            self.evaler.names = scope.vars
        return self.evaler.eval(expression)

    def eval_condition(self, condition_expr: Optional[str], scope: 'VirtualScope') -> bool:
        """
        计算条件表达式的布尔值

        Args:
            condition_expr: 条件表达式字符串（可选）
            scope: 用于表达式求值的 scope（可能是 LinkedScopeNode，包含继承的变量）
        """
        if condition_expr is None:
            return True
        return bool(self.eval(condition_expr, scope))

    def execute_vars(
        self,
        vars_def: Optional['StandardVarsDefinition'],
        scope: 'VirtualScope',
    ) -> None:
        if vars_def is not None:
            for var_def in vars_def:
                self.execute_var(var_def, scope)

    def execute_var(
        self,
        var_def: 'StandardVarDefinition',
        scope: 'VirtualScope',
    ) -> None:
        """
        执行单个变量的赋值

        Args:
            var_def: 变量定义，包含 name, value, on（可选）, initial_value（可选）
            scope: 用于表达式求值的 scope（可能是 LinkedScopeNode，包含继承的变量）
            target_scope: 实际赋值的 BaseScope 对象
        """
        name = var_def.name
        value_expr = var_def.value
        condition_expr = var_def.on
        initial_value = var_def.initial_value

        # 如果没有条件，直接求值并赋值
        if condition_expr is None:
            value = self.eval(value_expr, scope)
            scope.set_var(name, value)
            return
        # 计算求值条件表达式
        condition_result = self.eval(condition_expr, scope)
        if condition_result:
            # 条件满足，更新变量值和时间戳
            value = self.eval(value_expr, scope)
            scope.set_var(name, value, True)
        else:
            # 条件不满足，可能需要设置初始值
            if name not in scope.vars:
                scope.set_var(name, initial_value, True)

    def inject_functions(self, injected_functions: dict[str, Any], scope: 'VirtualScope') -> None:
        for name, func in injected_functions.items():
            scope.set_function(name, func)

    def inject_vars(self, injected_vars: dict[str, Any], scope: 'VirtualScope', namespace: Optional[str]) -> None:
        if namespace:
            scope.set_var(namespace, injected_vars)
        else:
            for name, value in injected_vars.items():
                scope.set_var(name, value)

    def inject_indicators(self, requires: list[str], node: 'FlowScopeNode', app_core: 'AppCore') -> bool:
        # 返回是否ready
        ready = True
        for required_indicator in requires:
            indicator_class_name = app_core.config.indicators[required_indicator].class_name
            indicator_class = BaseIndicator.classes[indicator_class_name]
            indicator_node = node  # 从当前节点查找前向节点
            if indicator_class.supported_scope is not None:
                while indicator_node is not None:
                    if isinstance(indicator_node.scope, indicator_class.supported_scope):
                        break
                    if len(indicator_node.prev) > 0:
                        indicator_node = indicator_node.prev[0]
                    else:
                        indicator_node = None
            if indicator_node is None:
                raise ValueError(f"Cannot find suitable scope for indicator {required_indicator} in scope {node.scope.path}")
            indicator = app_core.query_indicator(required_indicator, indicator_node)
            if not indicator.ready:
                ready = False
                break
            injected_vars = indicator.get_vars()
            injected_functions = indicator.get_functions()
            self.inject_vars(injected_vars, node, indicator.namespace)  # 注入
            self.inject_functions(injected_functions, node)
        return ready

    def execute(self, flow_config: 'ScopeFlowConfig', app_core: 'AppCore') -> dict[ScopeInstanceId, 'FlowScopeNode']:
        """
        执行一组变量赋值

        Args:
            flow_config: 变量流配置
        """
        scope_manager: 'ScopeManager' = app_core.scope_manager
        includes: dict[str, set[ScopeInstanceId]] = {}  # {class_name: {instance_id} }
        layers: ScopeFlowLayers = []  # [{instance_id: FlowScopeNode}, ...]
        for layer_config in flow_config:
            class_name = layer_config.class_name
            scope_class = scope_manager.get_class(class_name)
            previous_scopes = defaultdict(list)  # {current_instance_id: list[]}
            current_layer: dict[ScopeInstanceId, FlowScopeNode] = {}
            if scope_class is None:
                raise ValueError(f"Unknown scope class: {layer_config.class_name}")
            if class_name not in includes:
                instance_ids = scope_class.get_all_instance_ids(app_core)
            else:
                instance_ids = includes[class_name]  # 如果有，只使用已计算的结果
            if len(layers) == 0:  # 如果没有前节点，则
                for instance_id in instance_ids:
                    previous_scopes[instance_id] = []
            else:
                prev_nodes = layers[-1]
                if len(prev_nodes) == 0:  # 如果没有留下的target了
                    return {}
                prev_node_class = next(iter(prev_nodes.values())).scope.__class__
                if prev_node_class == scope_class:  # 一对一
                    for instance_id in instance_ids:
                        if instance_id in prev_nodes:
                            previous_scopes[instance_id] = [prev_nodes[instance_id]]
                elif prev_node_class in scope_class.flow_mapper:  # 一对多
                    for instance_id in instance_ids:
                        prev_node_instance_id = scope_class.instance_id_map_func(prev_node_class, instance_id)
                        if prev_node_instance_id in prev_nodes:
                            previous_scopes[instance_id] = [prev_nodes[prev_node_instance_id]]
                else:  # 多对一
                    for prev_node_instance_id, prev_node in prev_nodes.items():
                        mapped_instance_id = prev_node_class.instance_id_map_func(scope_class, prev_node_instance_id)
                        previous_scopes[mapped_instance_id].append(prev_node)
            for instance_id in instance_ids:
                if instance_id in previous_scopes:
                    scope = scope_manager.get_or_create(
                        class_name=class_name,
                        instance_id=instance_id,
                    )
                    # 创建 FlowScopeNode，用于表达式求值
                    node = FlowScopeNode(
                        scope=scope,
                        prev=previous_scopes[instance_id],
                    )
                    if not self.eval_condition(layer_config.filter, node):
                        continue
                    if not self.inject_indicators(layer_config.requires, node, app_core):
                        continue
                    self.execute_vars(layer_config.standard_vars_definition, node)  # 执行变量
                    if not self.eval_condition(layer_config.condition, node):  # 后验条件
                        continue
                    current_layer[instance_id] = node
            includes[class_name] = set(current_layer.keys())
            layers.append(current_layer)
        return layers[-1] if len(layers) > 0 else {}

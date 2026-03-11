"""
VirtualMachine 和 FlowScopeNode 单元测试
"""
import pytest
from unittest.mock import MagicMock
from hft.core.scope.base import BaseScope, FlowScopeNode, VirtualScope
from hft.core.scope.vm import VirtualMachine


# ---------------------------------------------------------------------------
# 测试辅助：轻量级 Scope mock（无需 AppCore）
# ---------------------------------------------------------------------------

def make_mock_scope(vars_dict=None, functions_dict=None):
    """创建用于测试的轻量级 Scope mock 对象"""
    scope = MagicMock(spec=BaseScope)
    scope._vars = vars_dict.copy() if vars_dict else {}
    scope._functions = functions_dict.copy() if functions_dict else {}
    scope._conditional_vars_update_times = {}
    scope.vars = scope._vars
    scope.functions = scope._functions
    scope.class_name = "MockScope"
    scope.instance_id = ("mock",)

    def _set_var(name, value, conditional=False):
        scope._vars[name] = value
        if conditional:
            scope._conditional_vars_update_times[name] = __import__('time').time()

    def _get_var(name, default=None):
        return scope._vars.get(name, default)

    scope.set_var = _set_var
    scope.get_var = _get_var
    scope.set_function = lambda name, func: scope._functions.__setitem__(name, func)
    return scope


def _make_var_def(name, value, on=None, initial_value=None):
    """创建变量定义 mock"""
    mock = MagicMock()
    mock.name = name
    mock.value = value
    mock.on = on
    mock.initial_value = initial_value
    return mock


# ---------------------------------------------------------------------------
# VirtualMachine.eval() 测试
# ---------------------------------------------------------------------------

class TestVirtualMachineEval:
    """测试 VirtualMachine.eval() 方法"""

    def setup_method(self):
        self.vm = VirtualMachine()

    def test_literal_int_returned_directly(self):
        """整数字面量直接返回，不当作表达式求值"""
        assert self.vm.eval(42) == 42

    def test_literal_float_returned_directly(self):
        """浮点数字面量直接返回"""
        assert self.vm.eval(3.14) == 3.14

    def test_literal_none_returned_directly(self):
        """None 直接返回"""
        assert self.vm.eval(None) is None

    def test_literal_bool_returned_directly(self):
        """布尔值直接返回"""
        assert self.vm.eval(True) is True
        assert self.vm.eval(False) is False

    def test_simple_arithmetic_expression(self):
        """简单算术表达式求值"""
        assert self.vm.eval("1 + 2") == 3

    def test_arithmetic_with_precedence(self):
        """带优先级的算术表达式"""
        assert self.vm.eval("2 + 3 * 4") == 14

    def test_expression_with_scope_variables(self):
        """使用 scope 变量的表达式"""
        scope = make_mock_scope(vars_dict={"x": 10, "y": 20})
        assert self.vm.eval("x + y", scope) == 30

    def test_expression_with_single_scope_variable(self):
        """包含单个 scope 变量的表达式"""
        scope = make_mock_scope(vars_dict={"price": 100.5})
        assert self.vm.eval("price * 2", scope) == 201.0

    def test_expression_with_scope_function(self):
        """使用 scope 函数的表达式"""
        scope = make_mock_scope(vars_dict={"x": -5}, functions_dict={"abs": abs})
        assert self.vm.eval("abs(x)", scope) == 5

    def test_zero_division_raises(self):
        """除以零应该抛出 ZeroDivisionError"""
        with pytest.raises(ZeroDivisionError):
            self.vm.eval("1 / 0")

    def test_zero_division_with_scope_raises(self):
        """使用 scope 变量的除以零也应抛出 ZeroDivisionError"""
        scope = make_mock_scope(vars_dict={"a": 1, "b": 0})
        with pytest.raises(ZeroDivisionError):
            self.vm.eval("a / b", scope)

    def test_no_scope_uses_defaults(self):
        """不传 scope 时使用默认函数和变量"""
        # simpleeval 默认支持 True/False/None 等名称
        result = self.vm.eval("True")
        assert result is True

    def test_boolean_expression_greater_than(self):
        """布尔比较表达式 >"""
        scope = make_mock_scope(vars_dict={"x": 10})
        assert self.vm.eval("x > 5", scope) is True

    def test_boolean_expression_less_than(self):
        """布尔比较表达式 <"""
        scope = make_mock_scope(vars_dict={"x": 3})
        assert self.vm.eval("x > 5", scope) is False

    def test_boolean_expression_equality(self):
        """布尔相等比较"""
        scope = make_mock_scope(vars_dict={"x": 5})
        assert self.vm.eval("x == 5", scope) is True

    def test_list_literal_expression(self):
        """列表字面量表达式"""
        result = self.vm.eval("[1, 2, 3]")
        assert result == [1, 2, 3]


# ---------------------------------------------------------------------------
# VirtualMachine.eval_condition() 测试
# ---------------------------------------------------------------------------

class TestVirtualMachineEvalCondition:
    """测试 VirtualMachine.eval_condition() 方法"""

    def setup_method(self):
        self.vm = VirtualMachine()

    def test_none_condition_returns_true(self):
        """条件为 None 时直接返回 True"""
        scope = make_mock_scope()
        assert self.vm.eval_condition(None, scope) is True

    def test_truthy_condition_returns_true(self):
        """条件表达式为真时返回 True"""
        scope = make_mock_scope(vars_dict={"x": 10})
        assert self.vm.eval_condition("x > 5", scope) is True

    def test_falsy_condition_count_zero(self):
        """条件表达式为假（count=0）时返回 False"""
        scope = make_mock_scope(vars_dict={"count": 0})
        assert self.vm.eval_condition("count", scope) is False

    def test_falsy_condition_comparison_false(self):
        """条件表达式比较结果为 False 时返回 False"""
        scope = make_mock_scope(vars_dict={"x": 3})
        assert self.vm.eval_condition("x > 5", scope) is False

    def test_truthy_numeric_condition(self):
        """非零数值条件返回 True"""
        scope = make_mock_scope(vars_dict={"n": 42})
        assert self.vm.eval_condition("n", scope) is True


# ---------------------------------------------------------------------------
# VirtualMachine.execute_var() 和 execute_vars() 测试
# ---------------------------------------------------------------------------

class TestVirtualMachineExecuteVar:
    """测试 VirtualMachine.execute_var() 和 execute_vars() 方法"""

    def setup_method(self):
        self.vm = VirtualMachine()

    def test_simple_var_assignment(self):
        """无条件变量赋值"""
        scope = make_mock_scope(vars_dict={"a": 5})
        var_def = _make_var_def(name="result", value="a + 1")
        self.vm.execute_var(var_def, scope)
        assert scope._vars["result"] == 6

    def test_literal_var_assignment(self):
        """字面量值变量赋值"""
        scope = make_mock_scope()
        var_def = _make_var_def(name="count", value=100)
        self.vm.execute_var(var_def, scope)
        assert scope._vars["count"] == 100

    def test_conditional_var_condition_true(self):
        """条件为真时，设置计算值"""
        scope = make_mock_scope(vars_dict={"flag": True, "a": 10})
        var_def = _make_var_def(name="result", value="a * 2", on="flag", initial_value=0)
        self.vm.execute_var(var_def, scope)
        assert scope._vars["result"] == 20

    def test_conditional_var_condition_false_sets_initial(self):
        """条件为假且变量不存在时，设置初始值"""
        scope = make_mock_scope(vars_dict={"flag": False})
        var_def = _make_var_def(name="result", value="99", on="flag", initial_value=-1)
        self.vm.execute_var(var_def, scope)
        assert scope._vars["result"] == -1

    def test_conditional_var_condition_false_existing_value_preserved(self):
        """条件为假且变量已存在时，保留已有值"""
        scope = make_mock_scope(vars_dict={"flag": False, "result": 42})
        var_def = _make_var_def(name="result", value="99", on="flag", initial_value=-1)
        self.vm.execute_var(var_def, scope)
        # 条件为假但变量已存在，不覆盖
        assert scope._vars["result"] == 42

    def test_execute_vars_with_list(self):
        """execute_vars 处理变量定义列表"""
        scope = make_mock_scope(vars_dict={"x": 5})
        var_defs = [
            _make_var_def(name="a", value="x + 1"),
            _make_var_def(name="b", value="x * 2"),
        ]
        self.vm.execute_vars(var_defs, scope)
        assert scope._vars["a"] == 6
        assert scope._vars["b"] == 10

    def test_execute_vars_none_is_noop(self):
        """execute_vars 传入 None 时不执行任何操作"""
        scope = make_mock_scope(vars_dict={"x": 5})
        # 不应抛出异常
        self.vm.execute_vars(None, scope)
        assert scope._vars == {"x": 5}

    def test_execute_vars_sequential_dependency(self):
        """execute_vars 顺序执行，后续变量可依赖前面定义的变量"""
        scope = make_mock_scope(vars_dict={"base": 10})
        var_defs = [
            _make_var_def(name="step1", value="base * 2"),
            _make_var_def(name="step2", value="step1 + 5"),
        ]
        self.vm.execute_vars(var_defs, scope)
        assert scope._vars["step1"] == 20
        assert scope._vars["step2"] == 25


# ---------------------------------------------------------------------------
# FlowScopeNode 测试
# ---------------------------------------------------------------------------

class TestFlowScopeNode:
    """测试 FlowScopeNode 的行为"""

    def _make_node(self, vars_dict=None, functions_dict=None, prev=None):
        """创建 FlowScopeNode 测试辅助"""
        scope = make_mock_scope(vars_dict=vars_dict, functions_dict=functions_dict)
        return FlowScopeNode(scope=scope, prev=prev or [])

    def test_basic_var_access(self):
        """基本变量访问"""
        node = self._make_node(vars_dict={"x": 42})
        assert node.get_var("x") == 42

    def test_get_var_default_when_missing(self):
        """变量不存在时返回 default"""
        node = self._make_node(vars_dict={})
        assert node.get_var("missing") is None
        assert node.get_var("missing", 99) == 99

    def test_variable_inheritance_from_prev_node(self):
        """子节点能继承 prev 节点的变量"""
        parent = self._make_node(vars_dict={"parent_var": "hello"})
        child = self._make_node(vars_dict={}, prev=[parent])
        assert child.get_var("parent_var") == "hello"

    def test_child_var_shadows_parent_var(self):
        """子节点的变量覆盖 prev 节点中同名变量"""
        parent = self._make_node(vars_dict={"x": 1})
        child = self._make_node(vars_dict={"x": 100}, prev=[parent])
        assert child.get_var("x") == 100

    def test_set_var_writes_to_scope(self):
        """set_var 写入到底层 scope"""
        node = self._make_node(vars_dict={})
        node.set_var("new_var", 123)
        assert node.scope._vars["new_var"] == 123

    def test_set_var_visible_in_vars(self):
        """set_var 后通过 vars 可访问"""
        node = self._make_node(vars_dict={})
        node.set_var("result", 7)
        assert node.get_var("result") == 7

    def test_prev_accessible_as_list(self):
        """prev 节点通过 injected_vars['prev'] 可以列表形式访问"""
        parent = self._make_node(vars_dict={"score": 88})
        child = self._make_node(vars_dict={}, prev=[parent])
        prev_list = child.injected_vars["prev"]
        assert isinstance(prev_list, list)
        assert len(prev_list) == 1

    def test_prev_list_contains_parent_chainmap(self):
        """prev 列表中的 ChainMap 包含父节点的变量"""
        parent = self._make_node(vars_dict={"score": 88})
        child = self._make_node(vars_dict={}, prev=[parent])
        prev_list = child.injected_vars["prev"]
        # 父节点的 current_chain_map 包含其变量
        assert prev_list[0]["score"] == 88

    def test_no_prev_gives_empty_list(self):
        """没有 prev 时，injected_vars['prev'] 为空列表"""
        node = self._make_node(vars_dict={"a": 1})
        assert node.injected_vars["prev"] == []

    def test_function_inheritance_from_prev(self):
        """子节点能继承 prev 节点的函数"""
        parent = self._make_node(functions_dict={"double": lambda x: x * 2})
        child = self._make_node(vars_dict={}, prev=[parent])
        assert child.get_function("double") is not None
        assert child.get_function("double")(5) == 10

    def test_set_function_writes_to_scope(self):
        """set_function 委托给底层 scope"""
        node = self._make_node()
        fn = lambda x: x + 1
        node.set_function("increment", fn)
        assert node.scope._functions["increment"] is fn

    def test_multi_level_inheritance(self):
        """多层继承：孙节点能访问祖父节点的变量"""
        grandparent = self._make_node(vars_dict={"root_val": 999})
        parent = self._make_node(vars_dict={"parent_val": 10}, prev=[grandparent])
        child = self._make_node(vars_dict={}, prev=[parent])
        assert child.get_var("root_val") == 999
        assert child.get_var("parent_val") == 10


# ---------------------------------------------------------------------------
# FlowScopeNode.search_prev_scope() 测试
# ---------------------------------------------------------------------------

class _ConcreteScope(BaseScope):
    """具体化的 BaseScope 子类，用于测试 search_prev_scope 的类型匹配"""

    def initialize(self, **kwargs):
        self._instance_id = kwargs.get("instance_id", ("test",))
        self._app_core = kwargs.get("app_core", None)
        self._functions = {}
        self._vars = {
            "instance_id": self._instance_id,
            "class_name": self.class_name,
            "app_core": self._app_core,
        }

    @classmethod
    def get_all_instance_ids(cls, app_core):
        return {("test",)}


class _AnotherConcreteScope(BaseScope):
    """另一个具体化 BaseScope 子类"""

    def initialize(self, **kwargs):
        self._instance_id = kwargs.get("instance_id", ("other",))
        self._app_core = kwargs.get("app_core", None)
        self._functions = {}
        self._vars = {
            "instance_id": self._instance_id,
            "class_name": self.class_name,
            "app_core": self._app_core,
        }

    @classmethod
    def get_all_instance_ids(cls, app_core):
        return {("other",)}


class TestFlowScopeNodeSearchPrev:
    """测试 FlowScopeNode.search_prev_scope()"""

    def _make_real_node(self, scope_class, instance_id=("test",), prev=None):
        """创建使用真实 BaseScope 子类的 FlowScopeNode"""
        scope = scope_class.__new__(scope_class)
        scope._vars = {"instance_id": instance_id, "class_name": scope_class.__name__, "app_core": None}
        scope._functions = {}
        scope._conditional_vars_update_times = {}
        scope._instance_id = instance_id
        scope._app_core = None
        return FlowScopeNode(scope=scope, prev=prev or [])

    def test_search_finds_self(self):
        """search_prev_scope 当当前节点类型匹配时返回自身"""
        node = self._make_real_node(_ConcreteScope)
        result = node.search_prev_scope(_ConcreteScope)
        assert result is node

    def test_search_finds_ancestor_of_correct_type(self):
        """search_prev_scope 沿 prev 链向上查找，返回正确类型的祖先节点"""
        grandparent = self._make_real_node(_ConcreteScope, instance_id=("gp",))
        parent = self._make_real_node(_AnotherConcreteScope, instance_id=("p",), prev=[grandparent])
        child = self._make_real_node(_AnotherConcreteScope, instance_id=("c",), prev=[parent])

        result = child.search_prev_scope(_ConcreteScope)
        assert result is grandparent

    def test_search_returns_none_when_not_found(self):
        """search_prev_scope 找不到目标类型时返回 None"""
        parent = self._make_real_node(_AnotherConcreteScope, instance_id=("p",))
        child = self._make_real_node(_AnotherConcreteScope, instance_id=("c",), prev=[parent])

        result = child.search_prev_scope(_ConcreteScope)
        assert result is None

    def test_search_returns_none_on_isolated_node(self):
        """孤立节点（无 prev）查找不到其他类型返回 None"""
        node = self._make_real_node(_ConcreteScope)
        result = node.search_prev_scope(_AnotherConcreteScope)
        assert result is None


# ---------------------------------------------------------------------------
# VirtualMachine 与 FlowScopeNode 集成测试
# ---------------------------------------------------------------------------

class TestVMWithFlowScopeNode:
    """VirtualMachine 与 FlowScopeNode 组合使用的集成测试"""

    def setup_method(self):
        self.vm = VirtualMachine()

    def _make_node(self, vars_dict=None, functions_dict=None, prev=None):
        scope = make_mock_scope(vars_dict=vars_dict, functions_dict=functions_dict)
        return FlowScopeNode(scope=scope, prev=prev or [])

    def test_eval_with_flow_scope_node(self):
        """通过 FlowScopeNode 的 vars/functions 求值"""
        node = self._make_node(
            vars_dict={"price": 100, "qty": 3},
            functions_dict={"abs": abs}
        )
        result = self.vm.eval("price * qty", node)
        assert result == 300

    def test_eval_uses_inherited_vars(self):
        """表达式求值能使用继承自父节点的变量"""
        parent = self._make_node(vars_dict={"base_price": 50})
        child = self._make_node(vars_dict={"multiplier": 2}, prev=[parent])
        result = self.vm.eval("base_price * multiplier", child)
        assert result == 100

    def test_execute_var_with_flow_node(self):
        """execute_var 通过 FlowScopeNode 求值并写入底层 scope"""
        node = self._make_node(vars_dict={"a": 10, "b": 5})
        var_def = _make_var_def(name="diff", value="a - b")
        self.vm.execute_var(var_def, node)
        assert node.scope._vars["diff"] == 5

    def test_conditional_var_with_flow_node_true(self):
        """条件变量通过 FlowScopeNode：条件为真时写入值"""
        node = self._make_node(vars_dict={"active": True, "x": 7})
        var_def = _make_var_def(name="y", value="x * 3", on="active", initial_value=0)
        self.vm.execute_var(var_def, node)
        assert node.scope._vars["y"] == 21

    def test_eval_condition_with_flow_node(self):
        """eval_condition 与 FlowScopeNode 继承变量协同工作"""
        parent = self._make_node(vars_dict={"threshold": 50})
        child = self._make_node(vars_dict={"value": 60}, prev=[parent])
        result = self.vm.eval_condition("value > threshold", child)
        assert result is True

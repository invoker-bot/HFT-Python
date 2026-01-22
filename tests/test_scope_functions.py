"""
Scope 系统函数支持 - 单元测试

测试内容：
1. Scope 函数设置和获取
2. Scope 函数继承（通过 LinkedScopeTree）
3. VirtualMachine 函数求值
4. GlobalScope 默认函数
"""
from hft.core.scope import BaseScope, VirtualMachine
from hft.core.scope.scopes import GlobalScope, ExchangeScope
from hft.core.scope.tree import LinkedScopeNode, LinkedScopeTree


class TestScopeFunctions:
    """Scope 函数管理测试"""

    def test_set_and_get_function(self):
        """测试设置和获取函数"""
        scope = BaseScope("test", "test_instance")

        # 定义一个简单函数
        def my_func(x):
            return x * 2

        scope.set_function("my_func", my_func)

        # 获取函数
        func = scope.get_function("my_func")
        assert func is not None
        assert func(5) == 10

        # 获取不存在的函数
        assert scope.get_function("non_existent") is None
        assert scope.get_function("non_existent", lambda: "default")() == "default"

    def test_update_functions(self):
        """测试批量更新函数"""
        scope = BaseScope("test", "test_instance")

        functions = {
            "add": lambda a, b: a + b,
            "mul": lambda a, b: a * b,
        }

        scope.update_functions(functions)

        assert scope.get_function("add")(2, 3) == 5
        assert scope.get_function("mul")(2, 3) == 6

    def test_clear_functions(self):
        """测试清空函数"""
        scope = BaseScope("test", "test_instance")
        scope.set_function("func1", lambda: 1)
        scope.set_function("func2", lambda: 2)

        assert scope.get_function("func1") is not None

        scope.clear_functions()

        assert scope.get_function("func1") is None
        assert scope.get_function("func2") is None


class TestScopeFunctionInheritance:
    """Scope 函数继承测试"""

    def test_child_inherits_parent_functions(self):
        """测试子 Scope 继承父 Scope 的函数"""
        parent_scope = GlobalScope("global", "global")
        parent_scope.set_function("parent_func", lambda x: x * 2)

        child_scope = ExchangeScope("exchange", "okx/main")
        child_scope.set_function("child_func", lambda x: x + 10)

        # 创建树结构
        parent_node = LinkedScopeNode(scope=parent_scope, parent=None)
        child_node = LinkedScopeNode(scope=child_scope, parent=parent_node)
        parent_node.add_child(child_node)

        # 通过 node.functions 获取包含祖先的函数
        child_functions = child_node.functions

        # 子节点可以访问父节点的函数
        assert "parent_func" in child_functions
        assert child_functions["parent_func"](5) == 10

        # 子节点也有自己的函数
        assert "child_func" in child_functions
        assert child_functions["child_func"](5) == 15

    def test_child_overrides_parent_functions(self):
        """测试子 Scope 覆盖父 Scope 的函数"""
        parent_scope = GlobalScope("global", "global")
        parent_scope.set_function("compute", lambda x: x * 2)

        child_scope = ExchangeScope("exchange", "okx/main")
        child_scope.set_function("compute", lambda x: x * 3)  # 覆盖父节点的函数

        # 创建树结构
        parent_node = LinkedScopeNode(scope=parent_scope, parent=None)
        child_node = LinkedScopeNode(scope=child_scope, parent=parent_node)
        parent_node.add_child(child_node)

        # 子节点的函数覆盖父节点
        child_functions = child_node.functions
        assert child_functions["compute"](5) == 15

        # 父节点的函数不变
        assert parent_scope.get_function("compute")(5) == 10


class TestDefaultFunctions:
    """默认函数测试"""

    def test_global_scope_has_default_functions(self):
        """测试 GlobalScope 包含默认函数"""
        global_scope = GlobalScope(scope_class_id="global", scope_instance_id="global")

        # 验证所有默认函数都存在
        assert global_scope.get_function("min") is not None
        assert global_scope.get_function("max") is not None
        assert global_scope.get_function("sum") is not None
        assert global_scope.get_function("len") is not None
        assert global_scope.get_function("abs") is not None
        assert global_scope.get_function("round") is not None
        assert global_scope.get_function("clip") is not None
        assert global_scope.get_function("avg") is not None

    def test_default_functions_work(self):
        """测试默认函数功能正常"""
        global_scope = GlobalScope(scope_class_id="global", scope_instance_id="global")

        # 测试 clip 函数
        clip_func = global_scope.get_function("clip")
        assert clip_func(5, 0, 10) == 5
        assert clip_func(-5, 0, 10) == 0
        assert clip_func(15, 0, 10) == 10

        # 测试 avg 函数
        avg_func = global_scope.get_function("avg")
        assert avg_func([1, 2, 3, 4, 5]) == 3.0
        assert avg_func([]) == 0

    def test_vm_has_default_functions(self):
        """测试 VirtualMachine 通过 GlobalScope 获取默认函数"""
        vm = VirtualMachine()

        # 创建 GlobalScope，它包含默认函数
        global_scope = GlobalScope(scope_class_id="global", scope_instance_id="global")

        # 验证 GlobalScope 包含所有默认函数
        assert "clip" in global_scope.functions
        assert "avg" in global_scope.functions

        # 测试使用 GlobalScope 的默认函数求值
        assert vm.eval("clip(5, 0, 10)", scope=global_scope) == 5
        assert vm.eval("avg([1, 2, 3, 4, 5])", scope=global_scope) == 3.0


class TestVirtualMachineWithFunctions:
    """VirtualMachine 函数求值测试"""

    def test_eval_with_custom_functions(self):
        """测试使用自定义函数求值"""
        vm = VirtualMachine()

        # 创建一个 Scope 并添加自定义函数
        scope = BaseScope("test", "test_instance")
        scope.set_function("double", lambda x: x * 2)
        scope.set_function("triple", lambda x: x * 3)

        # 使用自定义函数求值
        result = vm.eval("double(5) + triple(3)", scope=scope)
        assert result == 10 + 9
        assert result == 19

    def test_eval_with_scope_functions(self):
        """测试使用 Scope 中的函数求值"""
        vm = VirtualMachine()

        scope = BaseScope("test", "test_instance")
        scope.set_function("square", lambda x: x ** 2)
        scope.set_var("x", 5)

        # 使用 Scope 的函数和变量求值
        result = vm.eval("square(x)", scope=scope)
        assert result == 25

    def test_eval_with_inherited_functions(self):
        """测试使用继承的函数求值"""
        vm = VirtualMachine()

        parent_scope = GlobalScope("global", "global")
        parent_scope.set_function("add_ten", lambda x: x + 10)
        parent_scope.set_var("base", 5)

        child_scope = ExchangeScope("exchange", "okx/main")
        child_scope.set_function("multiply_two", lambda x: x * 2)

        # 创建树结构
        parent_node = LinkedScopeNode(scope=parent_scope, parent=None)
        child_node = LinkedScopeNode(scope=child_scope, parent=parent_node)
        parent_node.add_child(child_node)

        # 使用继承的函数和变量求值（通过 LinkedScopeNode）
        result = vm.eval("multiply_two(add_ten(base))", scope=child_node)
        # base=5, add_ten(5)=15, multiply_two(15)=30
        assert result == 30


class TestVirtualMachineExecute:
    """VirtualMachine execute 方法测试"""

    def test_execute_format1_standard(self):
        """测试格式 1：标准格式 list[dict]"""
        vm = VirtualMachine()
        scope = BaseScope("test", "test_instance")

        scope.set_var("a", 10)
        scope.set_var("b", 5)

        # 格式 1：标准格式
        vm.execute([
            {"name": "sum_val", "value": "a + b"},
            {"name": "product", "value": "a * b"},
            {"name": "doubled", "value": "a * 2"}
        ], scope)

        assert scope.get_var("sum_val") == 15
        assert scope.get_var("product") == 50
        assert scope.get_var("doubled") == 20

    def test_execute_format2_dict(self):
        """测试格式 2：dict 简化格式"""
        vm = VirtualMachine()
        scope = BaseScope("test", "test_instance")

        scope.set_var("a", 10)

        # 格式 2：dict 简化格式
        vm.execute({
            "x": 10,
            "y": 20,
            "z": "a * 2"
        }, scope)

        assert scope.get_var("x") == 10
        assert scope.get_var("y") == 20
        assert scope.get_var("z") == 20

    def test_execute_format3_list_str(self):
        """测试格式 3：list[str] 简化格式"""
        vm = VirtualMachine()
        scope = BaseScope("test", "test_instance")

        scope.set_var("base", 100)

        # 格式 3：list[str] 简化格式
        vm.execute([
            "x=10",
            "y=20",
            "z=base * 2"
        ], scope)

        assert scope.get_var("x") == 10
        assert scope.get_var("y") == 20
        assert scope.get_var("z") == 200

    def test_execute_mixed_format(self):
        """测试混合格式：list 中混合 dict 和 str"""
        vm = VirtualMachine()
        scope = BaseScope("test", "test_instance")

        scope.set_var("base", 100)

        # 混合格式
        vm.execute([
            "x=10",  # 格式 3
            {"name": "y", "value": "x * 2"},  # 格式 1
            "z=base + y"  # 格式 3
        ], scope)

        assert scope.get_var("x") == 10
        assert scope.get_var("y") == 20
        assert scope.get_var("z") == 120

    def test_execute_with_functions(self):
        """测试使用内置函数"""
        vm = VirtualMachine()
        # 使用 GlobalScope 以获得内置函数
        scope = GlobalScope("global", "global")

        scope.set_var("values", [1, 2, 3, 4, 5])

        vm.execute([
            {"name": "total", "value": "sum(values)"},
            {"name": "average", "value": "avg(values)"},
            {"name": "maximum", "value": "max(values)"},
            {"name": "minimum", "value": "min(values)"}
        ], scope)

        assert scope.get_var("total") == 15
        assert scope.get_var("average") == 3.0
        assert scope.get_var("maximum") == 5
        assert scope.get_var("minimum") == 1

    def test_execute_conditional_var_true(self):
        """测试条件变量：条件满足时更新"""
        vm = VirtualMachine()
        scope = BaseScope("test", "test_instance")

        scope.set_var("position", 0)
        scope.set_var("mid_price", 100.5)

        # 条件变量：当 position == 0 时更新 entry_price
        vm.execute([
            {
                "name": "entry_price",
                "value": "mid_price",
                "on": "position == 0",
                "initial_value": None
            }
        ], scope)

        # 条件满足，应该更新
        assert scope.get_var("entry_price") == 100.5

    def test_execute_conditional_var_false(self):
        """测试条件变量：条件不满足时保持原值"""
        vm = VirtualMachine()
        scope = BaseScope("test", "test_instance")

        scope.set_var("position", 10)
        scope.set_var("mid_price", 100.5)

        # 第一次执行：条件不满足，使用 initial_value
        vm.execute([
            {
                "name": "entry_price",
                "value": "mid_price",
                "on": "position == 0",
                "initial_value": 95.0
            }
        ], scope)

        # 条件不满足，应该使用 initial_value
        assert scope.get_var("entry_price") == 95.0

        # 第二次执行：条件仍不满足，保持原值
        scope.set_var("mid_price", 105.0)
        vm.execute([
            {
                "name": "entry_price",
                "value": "mid_price",
                "on": "position == 0",
                "initial_value": 95.0
            }
        ], scope)

        # 条件不满足，保持原值
        assert scope.get_var("entry_price") == 95.0

    def test_execute_conditional_var_with_duration(self):
        """测试条件变量：使用 duration 变量"""
        import time
        vm = VirtualMachine()
        scope = BaseScope("test", "test_instance")

        scope.set_var("rsi", 50)
        scope.set_var("mid_price", 100.0)

        # 第一次执行：条件满足（rsi < 30 or rsi > 70）
        scope.set_var("rsi", 25)
        vm.execute([
            {
                "name": "center_price",
                "value": "mid_price",
                "on": "rsi < 30 or rsi > 70 or duration > 1",
                "initial_value": 100.0
            }
        ], scope)

        # 条件满足，更新为 100.0
        assert scope.get_var("center_price") == 100.0

        # 第二次执行：条件不满足，但 duration 很小
        scope.set_var("rsi", 50)
        scope.set_var("mid_price", 105.0)
        vm.execute([
            {
                "name": "center_price",
                "value": "mid_price",
                "on": "rsi < 30 or rsi > 70 or duration > 1",
                "initial_value": 100.0
            }
        ], scope)

        # 条件不满足，保持原值
        assert scope.get_var("center_price") == 100.0

        # 等待一段时间后再执行
        time.sleep(1.1)
        vm.execute([
            {
                "name": "center_price",
                "value": "mid_price",
                "on": "rsi < 30 or rsi > 70 or duration > 1",
                "initial_value": 100.0
            }
        ], scope)

        # duration > 1，条件满足，更新为 105.0
        assert scope.get_var("center_price") == 105.0

    def test_execute_with_linked_scope_node(self):
        """测试使用 LinkedScopeNode（可以访问父节点变量）"""
        vm = VirtualMachine()

        parent_scope = GlobalScope("global", "global")
        parent_scope.set_var("base", 100)

        child_scope = BaseScope("child", "child_instance")

        # 创建树结构
        parent_node = LinkedScopeNode(scope=parent_scope, parent=None)
        child_node = LinkedScopeNode(scope=child_scope, parent=parent_node)
        parent_node.add_child(child_node)

        # 使用 LinkedScopeNode 执行赋值（可以访问父节点变量）
        vm.execute([
            {"name": "derived", "value": "base * 2"},
            {"name": "local", "value": 50}
        ], child_node)

        # 验证变量赋值到 child_scope
        assert child_scope.get_var("derived") == 200
        assert child_scope.get_var("local") == 50
        # 父节点变量不受影响
        assert parent_scope.get_var("base") == 100

    def test_execute_calculation_order(self):
        """测试计算顺序：后面的变量可以引用前面的变量"""
        vm = VirtualMachine()
        scope = BaseScope("test", "test_instance")

        # 按顺序计算，后面的变量可以引用前面的变量
        vm.execute([
            "a=100",
            "b=a * 2",
            "c=a + b",
            {"name": "d", "value": "c - a"}
        ], scope)

        assert scope.get_var("a") == 100
        assert scope.get_var("b") == 200
        assert scope.get_var("c") == 300
        assert scope.get_var("d") == 200

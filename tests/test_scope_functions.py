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
        """测试 VirtualMachine 包含默认函数"""
        vm = VirtualMachine()

        # 验证 VM 包含所有默认函数
        assert "clip" in vm.functions
        assert "avg" in vm.functions

        # 测试使用默认函数求值
        assert vm.eval("clip(5, 0, 10)") == 5
        assert vm.eval("avg([1, 2, 3, 4, 5])") == 3.0


class TestVirtualMachineWithFunctions:
    """VirtualMachine 函数求值测试"""

    def test_eval_with_custom_functions(self):
        """测试使用自定义函数求值"""
        vm = VirtualMachine()

        # 定义自定义函数
        custom_functions = {
            "double": lambda x: x * 2,
            "triple": lambda x: x * 3,
        }

        # 使用自定义函数求值
        result = vm.eval("double(5) + triple(3)", functions=custom_functions)
        assert result == 10 + 9
        assert result == 19

    def test_eval_with_scope_functions(self):
        """测试使用 Scope 中的函数求值"""
        vm = VirtualMachine()

        scope = BaseScope("test", "test_instance")
        scope.set_function("square", lambda x: x ** 2)
        scope.set_var("x", 5)

        # 使用 Scope 的函数和变量求值
        result = vm.eval("square(x)", names=scope, functions=scope)
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
        tree = LinkedScopeTree(root=parent_node)

        # 使用继承的函数和变量求值
        result = vm.eval(
            "multiply_two(add_ten(base))",
            names=(child_node, tree),
            functions=(child_node, tree)
        )
        # base=5, add_ten(5)=15, multiply_two(15)=30
        assert result == 30

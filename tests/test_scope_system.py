"""
Scope 系统单元测试
"""
from hft.core.scope import BaseScope, ScopeManager, VirtualMachine
from hft.core.scope.scopes import GlobalScope
from hft.core.scope.tree import LinkedScopeNode, LinkedScopeTree


class TestBaseScope:
    """测试 BaseScope 基类"""

    def test_init(self):
        """测试初始化"""
        scope = BaseScope("test", "test_instance")
        assert scope.scope_class_id == "test"
        assert scope.scope_instance_id == "test_instance"
        # parent 和 children 已移除，不再是 BaseScope 的属性

    def test_set_and_get_var(self):
        """测试设置和获取变量"""
        scope = BaseScope("test", "test_instance")
        scope.set_var("key1", "value1")
        scope.set_var("key2", 123)

        assert scope.get_var("key1") == "value1"
        assert scope.get_var("key2") == 123
        assert scope.get_var("key3", "default") == "default"

    def test_vars_inheritance(self):
        """测试变量继承（通过 LinkedScopeTree）"""
        parent_scope = BaseScope("parent", "parent_instance")
        parent_scope.set_var("parent_var", "parent_value")
        parent_scope.set_var("shared_var", "parent_shared")

        child_scope = BaseScope("child", "child_instance")
        child_scope.set_var("child_var", "child_value")
        child_scope.set_var("shared_var", "child_shared")

        # 创建树结构
        parent_node = LinkedScopeNode(scope=parent_scope, parent=None)
        child_node = LinkedScopeNode(scope=child_scope, parent=parent_node)
        parent_node.add_child(child_node)
        tree = LinkedScopeTree(root=parent_node)

        # 通过 tree.get_vars() 获取包含祖先的变量
        child_vars = tree.get_vars(child_node)

        # 子节点可以访问父节点的变量
        assert child_vars["parent_var"] == "parent_value"
        assert child_vars["child_var"] == "child_value"
        # 子节点的变量会覆盖父节点的同名变量
        assert child_vars["shared_var"] == "child_shared"

    def test_dict_access(self):
        """测试字典式访问"""
        scope = BaseScope("test", "test_instance")
        scope["key1"] = "value1"
        assert scope["key1"] == "value1"


class TestVirtualMachine:
    """测试 VirtualMachine"""

    def test_basic_eval(self):
        """测试基本表达式求值"""
        vm = VirtualMachine()
        assert vm.eval("1 + 2") == 3
        assert vm.eval("10 * 5") == 50
        assert vm.eval("100 / 4") == 25.0

    def test_eval_with_variables(self):
        """测试带变量的表达式求值"""
        vm = VirtualMachine()
        names = {"x": 10, "y": 20}
        assert vm.eval("x + y", names) == 30
        assert vm.eval("x * y", names) == 200

    def test_eval_with_functions(self):
        """测试带函数的表达式求值"""
        vm = VirtualMachine()
        assert vm.eval("min(1, 2, 3)") == 1
        assert vm.eval("max(1, 2, 3)") == 3
        # 使用变量传递列表
        assert vm.eval("sum(lst)", {"lst": [1, 2, 3]}) == 6
        assert vm.eval("clip(5, 0, 10)") == 5
        assert vm.eval("clip(-5, 0, 10)") == 0
        assert vm.eval("clip(15, 0, 10)") == 10


class TestScopeManager:
    """测试 ScopeManager"""

    def test_get_or_create_global_scope(self):
        """测试创建 GlobalScope"""
        manager = ScopeManager()
        # 参数：scope_class_name, scope_class_id, scope_instance_id
        scope = manager.get_or_create("GlobalScope", "global", "global")

        assert isinstance(scope, GlobalScope)
        assert scope.scope_class_id == "global"
        assert scope.scope_instance_id == "global"

    def test_cache(self):
        """测试缓存机制"""
        manager = ScopeManager()
        scope1 = manager.get_or_create("GlobalScope", "global", "global")
        scope2 = manager.get_or_create("GlobalScope", "global", "global")

        # 应该返回同一个实例
        assert scope1 is scope2

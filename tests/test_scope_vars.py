"""
Scope 系统变量计算 - 单元测试

测试内容：
1. Scope 变量继承（通过 LinkedScopeTree）
2. Scope 条件变量
3. Scope 树构建
4. parent/children 访问（通过 LinkedScopeNode）
"""
from hft.core.scope.scopes import (
    GlobalScope,
    ExchangeScope,
    TradingPairScope,
)
from hft.core.scope.manager import ScopeManager
from hft.core.scope.tree import LinkedScopeNode, LinkedScopeTree


class TestScopeVariableInheritance:
    """Scope 变量继承测试"""

    def test_child_inherits_parent_vars(self):
        """测试子 Scope 继承父 Scope 的变量"""
        parent_scope = GlobalScope("global", "global", None)
        parent_scope.set_var("max_position", 10000)
        parent_scope.set_var("speed", 0.5)

        child_scope = ExchangeScope("exchange", "okx/main")
        child_scope.set_var("exchange_path", "okx/main")

        # 创建树结构
        parent_node = LinkedScopeNode(scope=parent_scope, parent=None)
        child_node = LinkedScopeNode(scope=child_scope, parent=parent_node)
        parent_node.add_child(child_node)
        tree = LinkedScopeTree(root=parent_node)

        # 通过 tree.get_vars() 获取包含祖先的变量
        child_vars = tree.get_vars(child_node)

        # 子节点可以访问父节点的变量
        assert child_vars["max_position"] == 10000
        assert child_vars["speed"] == 0.5
        # 子节点也有自己的变量
        assert child_vars["exchange_path"] == "okx/main"

    def test_child_overrides_parent_vars(self):
        """测试子 Scope 覆盖父 Scope 的变量"""
        parent_scope = GlobalScope("global", "global", None)
        parent_scope.set_var("speed", 0.5)

        child_scope = ExchangeScope("exchange", "okx/main")
        child_scope.set_var("speed", 0.8)  # 覆盖父节点的值

        # 创建树结构
        parent_node = LinkedScopeNode(scope=parent_scope, parent=None)
        child_node = LinkedScopeNode(scope=child_scope, parent=parent_node)
        parent_node.add_child(child_node)
        tree = LinkedScopeTree(root=parent_node)

        # 子节点的值覆盖父节点
        child_vars = tree.get_vars(child_node)
        assert child_vars["speed"] == 0.8
        # 父节点的值不变
        assert parent_scope.get_var("speed") == 0.5

    def test_multi_level_inheritance(self):
        """测试多层级继承"""
        global_scope = GlobalScope("global", "global", None)
        global_scope.set_var("max_position", 10000)

        exchange_scope = ExchangeScope("exchange", "okx/main")
        exchange_scope.set_var("exchange_path", "okx/main")

        trading_pair_scope = TradingPairScope("trading_pair", "okx/main:BTC/USDT")
        trading_pair_scope.set_var("symbol", "BTC/USDT")

        # 创建三层树结构
        global_node = LinkedScopeNode(scope=global_scope, parent=None)
        exchange_node = LinkedScopeNode(scope=exchange_scope, parent=global_node)
        trading_pair_node = LinkedScopeNode(scope=trading_pair_scope, parent=exchange_node)
        global_node.add_child(exchange_node)
        exchange_node.add_child(trading_pair_node)
        tree = LinkedScopeTree(root=global_node)

        # 最底层可以访问所有上层的变量
        tp_vars = tree.get_vars(trading_pair_node)
        assert tp_vars["max_position"] == 10000
        assert tp_vars["exchange_path"] == "okx/main"
        assert tp_vars["symbol"] == "BTC/USDT"


class TestScopeParentChildrenAccess:
    """Scope parent/children 访问测试（通过 LinkedScopeNode）"""

    def test_parent_access(self):
        """测试访问 parent"""
        parent_scope = GlobalScope("global", "global", None)
        parent_scope.set_var("max_position", 10000)

        child_scope = ExchangeScope("exchange", "okx/main")

        # 创建树结构
        parent_node = LinkedScopeNode(scope=parent_scope, parent=None)
        child_node = LinkedScopeNode(scope=child_scope, parent=parent_node)
        parent_node.add_child(child_node)

        # 子节点可以通过 node.parent 访问父节点
        assert child_node.parent is parent_node
        assert child_node.parent.scope is parent_scope

    def test_children_access(self):
        """测试访问 children"""
        parent_scope = GlobalScope("global", "global", None)
        child1_scope = ExchangeScope("exchange", "okx/main")
        child2_scope = ExchangeScope("exchange", "binance/spot")

        # 创建树结构
        parent_node = LinkedScopeNode(scope=parent_scope, parent=None)
        child1_node = LinkedScopeNode(scope=child1_scope, parent=parent_node)
        child2_node = LinkedScopeNode(scope=child2_scope, parent=parent_node)
        parent_node.add_child(child1_node)
        parent_node.add_child(child2_node)

        # 父节点可以通过 node.children 访问子节点
        assert len(parent_node.children) == 2
        assert "okx/main" in parent_node.children
        assert "binance/spot" in parent_node.children
        assert parent_node.children["okx/main"] == child1_node
        assert parent_node.children["binance/spot"] == child2_node


class TestScopeManager:
    """ScopeManager 测试"""

    def test_get_or_create_scope(self):
        """测试创建 Scope"""
        manager = ScopeManager()

        scope = manager.get_or_create(
            scope_class_name="GlobalScope",
            scope_class_id="global",
            scope_instance_id="global"
        )

        assert scope is not None
        assert scope.scope_class_id == "global"
        assert scope.scope_instance_id == "global"

    def test_scope_caching(self):
        """测试 Scope 缓存"""
        manager = ScopeManager()

        scope1 = manager.get_or_create(
            scope_class_name="GlobalScope",
            scope_class_id="global",
            scope_instance_id="global"
        )

        scope2 = manager.get_or_create(
            scope_class_name="GlobalScope",
            scope_class_id="global",
            scope_instance_id="global"
        )

        # 应该返回同一个实例
        assert scope1 is scope2

    def test_same_key_returns_same_instance(self):
        """测试相同 (scope_class_id, scope_instance_id) 返回同一实例（Issue 0012）"""
        manager = ScopeManager()

        scope1 = manager.get_or_create(
            scope_class_name="ExchangeScope",
            scope_class_id="exchange",
            scope_instance_id="okx/main"
        )

        scope2 = manager.get_or_create(
            scope_class_name="ExchangeScope",
            scope_class_id="exchange",
            scope_instance_id="okx/main"
        )

        # 即使在不同的树中，相同的 (class_id, instance_id) 应该返回同一实例
        assert scope1 is scope2


class TestScopeTreeBuilding:
    """Scope 树构建测试"""

    def test_simple_tree(self):
        """测试简单的树结构"""
        global_scope = GlobalScope("global", "global", None)
        exchange1_scope = ExchangeScope("exchange", "okx/main")
        exchange2_scope = ExchangeScope("exchange", "binance/spot")

        # 创建树结构
        global_node = LinkedScopeNode(scope=global_scope, parent=None)
        exchange1_node = LinkedScopeNode(scope=exchange1_scope, parent=global_node)
        exchange2_node = LinkedScopeNode(scope=exchange2_scope, parent=global_node)
        global_node.add_child(exchange1_node)
        global_node.add_child(exchange2_node)

        # 验证树结构
        assert len(global_node.children) == 2
        assert exchange1_node.parent is global_node
        assert exchange2_node.parent is global_node

    def test_three_level_tree(self):
        """测试三层树结构"""
        global_scope = GlobalScope("global", "global", None)
        exchange_scope = ExchangeScope("exchange", "okx/main")
        pair1_scope = TradingPairScope("trading_pair", "okx/main:BTC/USDT")
        pair2_scope = TradingPairScope("trading_pair", "okx/main:ETH/USDT")

        # 创建三层树结构
        global_node = LinkedScopeNode(scope=global_scope, parent=None)
        exchange_node = LinkedScopeNode(scope=exchange_scope, parent=global_node)
        pair1_node = LinkedScopeNode(scope=pair1_scope, parent=exchange_node)
        pair2_node = LinkedScopeNode(scope=pair2_scope, parent=exchange_node)

        global_node.add_child(exchange_node)
        exchange_node.add_child(pair1_node)
        exchange_node.add_child(pair2_node)

        # 验证树结构
        assert len(global_node.children) == 1
        assert len(exchange_node.children) == 2
        assert pair1_node.parent is exchange_node
        assert pair2_node.parent is exchange_node
        assert exchange_node.parent is global_node


class TestLinkedScopeNodeMethods:
    """LinkedScopeNode 方法测试"""

    def test_set_var(self):
        """测试 LinkedScopeNode.set_var 方法"""
        scope = GlobalScope("global", "global", None)
        node = LinkedScopeNode(scope=scope, parent=None)

        # 使用 node.set_var 设置变量
        node.set_var("test_var", 100)
        node.set_var("name", "test")

        # 验证变量已设置到 scope
        assert scope.get_var("test_var") == 100
        assert scope.get_var("name") == "test"
        # 也可以通过 node.vars 访问
        assert node.vars["test_var"] == 100
        assert node.vars["name"] == "test"

    def test_set_function(self):
        """测试 LinkedScopeNode.set_function 方法"""
        scope = GlobalScope("global", "global", None)
        node = LinkedScopeNode(scope=scope, parent=None)

        # 定义测试函数
        def double(x):
            return x * 2

        def add(a, b):
            return a + b

        # 使用 node.set_function 设置函数
        node.set_function("double", double)
        node.set_function("add", add)

        # 验证函数已设置到 scope
        assert scope.get_function("double") is double
        assert scope.get_function("add") is add
        # 也可以通过 node.functions 访问
        assert node.functions["double"] is double
        assert node.functions["add"] is add

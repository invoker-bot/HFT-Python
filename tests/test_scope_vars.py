"""
Scope 系统变量计算 - 单元测试

测试内容：
1. Scope 变量继承（ChainMap）
2. Scope 条件变量
3. Scope 树构建
4. parent/children 访问
"""
from hft.core.scope.scopes import (
    GlobalScope,
    ExchangeScope,
    TradingPairScope,
)
from hft.core.scope.manager import ScopeManager


class TestScopeVariableInheritance:
    """Scope 变量继承测试"""

    def test_child_inherits_parent_vars(self):
        """测试子 Scope 继承父 Scope 的变量"""
        parent = GlobalScope("global", "global", None)
        parent.set_var("max_position", 10000)
        parent.set_var("speed", 0.5)

        child = ExchangeScope("exchange", "okx/main", parent)
        child.set_var("exchange_path", "okx/main")

        # 子节点可以访问父节点的变量
        assert child.get_var("max_position") == 10000
        assert child.get_var("speed") == 0.5
        # 子节点也有自己的变量
        assert child.get_var("exchange_path") == "okx/main"

    def test_child_overrides_parent_vars(self):
        """测试子 Scope 覆盖父 Scope 的变量"""
        parent = GlobalScope("global", "global", None)
        parent.set_var("speed", 0.5)

        child = ExchangeScope("exchange", "okx/main", parent)
        child.set_var("speed", 0.8)  # 覆盖父节点的值

        # 子节点的值覆盖父节点
        assert child.get_var("speed") == 0.8
        # 父节点的值不变
        assert parent.get_var("speed") == 0.5

    def test_multi_level_inheritance(self):
        """测试多层级继承"""
        global_scope = GlobalScope("global", "global", None)
        global_scope.set_var("max_position", 10000)

        exchange_scope = ExchangeScope("exchange", "okx/main", global_scope)
        exchange_scope.set_var("exchange_path", "okx/main")

        trading_pair_scope = TradingPairScope(
            "trading_pair", "okx/main:BTC/USDT", exchange_scope
        )
        trading_pair_scope.set_var("symbol", "BTC/USDT")

        # 最底层可以访问所有上层的变量
        assert trading_pair_scope.get_var("max_position") == 10000
        assert trading_pair_scope.get_var("exchange_path") == "okx/main"
        assert trading_pair_scope.get_var("symbol") == "BTC/USDT"


class TestScopeParentChildrenAccess:
    """Scope parent/children 访问测试"""

    def test_parent_access(self):
        """测试访问 parent"""
        parent = GlobalScope("global", "global", None)
        parent.set_var("max_position", 10000)

        child = ExchangeScope("exchange", "okx/main", parent)

        # 子节点可以访问 parent
        assert child.parent is parent
        assert child.parent.get_var("max_position") == 10000

    def test_children_access(self):
        """测试访问 children"""
        parent = GlobalScope("global", "global", None)

        child1 = ExchangeScope("exchange", "okx/main", parent)
        child1.set_var("amount", 100)
        parent.add_child(child1)

        child2 = ExchangeScope("exchange", "binance/spot", parent)
        child2.set_var("amount", 200)
        parent.add_child(child2)

        # 父节点可以访问 children
        assert len(parent.children) == 2
        assert "okx/main" in parent.children
        assert "binance/spot" in parent.children
        assert parent.children["okx/main"].get_var("amount") == 100
        assert parent.children["binance/spot"].get_var("amount") == 200


class TestScopeManager:
    """ScopeManager 测试"""

    def test_get_or_create_scope(self):
        """测试创建 Scope"""
        manager = ScopeManager()

        scope = manager.get_or_create(
            scope_class_name="GlobalScope",
            scope_class_id="global",
            scope_instance_id="global",
            parent=None
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
            scope_instance_id="global",
            parent=None
        )

        scope2 = manager.get_or_create(
            scope_class_name="GlobalScope",
            scope_class_id="global",
            scope_instance_id="global",
            parent=None
        )

        # 应该返回同一个实例
        assert scope1 is scope2

    def test_same_key_returns_same_instance(self):
        """测试相同 (scope_class_id, scope_instance_id) 返回同一实例（Issue 0012）"""
        manager = ScopeManager()

        parent1 = GlobalScope("global", "global1", None)
        parent2 = GlobalScope("global", "global2", None)

        scope1 = manager.get_or_create(
            scope_class_name="ExchangeScope",
            scope_class_id="exchange",
            scope_instance_id="okx/main",
            parent=parent1
        )

        scope2 = manager.get_or_create(
            scope_class_name="ExchangeScope",
            scope_class_id="exchange",
            scope_instance_id="okx/main",
            parent=parent2
        )

        # 相同 cache key 应该返回同一实例（缓存 key 不包含 parent）
        assert scope1 is scope2
        # 第一次创建时的 parent 被保留
        assert scope1.parent is parent1


class TestScopeTreeBuilding:
    """Scope 树构建测试"""

    def test_simple_tree(self):
        """测试简单的树结构"""
        global_scope = GlobalScope("global", "global", None)

        exchange1 = ExchangeScope("exchange", "okx/main", global_scope)
        global_scope.add_child(exchange1)

        exchange2 = ExchangeScope("exchange", "binance/spot", global_scope)
        global_scope.add_child(exchange2)

        # 验证树结构
        assert len(global_scope.children) == 2
        assert exchange1.parent is global_scope
        assert exchange2.parent is global_scope

    def test_three_level_tree(self):
        """测试三层树结构"""
        global_scope = GlobalScope("global", "global", None)

        exchange_scope = ExchangeScope("exchange", "okx/main", global_scope)
        global_scope.add_child(exchange_scope)

        pair1 = TradingPairScope("trading_pair", "okx/main:BTC/USDT", exchange_scope)
        exchange_scope.add_child(pair1)

        pair2 = TradingPairScope("trading_pair", "okx/main:ETH/USDT", exchange_scope)
        exchange_scope.add_child(pair2)

        # 验证树结构
        assert len(global_scope.children) == 1
        assert len(exchange_scope.children) == 2
        assert pair1.parent is exchange_scope
        assert pair2.parent is exchange_scope
        assert pair1.parent.parent is global_scope



"""
AppCore Scope 初始化单元测试

测试 AppCore 与 Scope 系统的集成
"""
from hft.core.scope import ScopeManager


class TestAppCoreScopeIntegration:
    """测试 AppCore Scope 集成"""

    def test_scope_manager_can_be_instantiated(self):
        """测试 ScopeManager 可以被实例化"""
        scope_manager = ScopeManager()

        assert scope_manager is not None
        assert isinstance(scope_manager, ScopeManager)

    def test_scope_manager_has_required_methods(self):
        """测试 ScopeManager 有必需的方法"""
        scope_manager = ScopeManager()

        # 验证关键方法存在
        assert hasattr(scope_manager, 'register_scope_class')
        assert hasattr(scope_manager, 'get_or_create')
        assert hasattr(scope_manager, 'get')
        assert hasattr(scope_manager, 'clear_cache')
        assert hasattr(scope_manager, 'reset_all_ready_states')
        assert callable(scope_manager.register_scope_class)
        assert callable(scope_manager.get_or_create)
        assert callable(scope_manager.get)
        assert callable(scope_manager.clear_cache)
        assert callable(scope_manager.reset_all_ready_states)

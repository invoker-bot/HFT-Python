"""
Tests for Listener cache mechanism.

Tests cover:
- build_cache_key: 构建缓存键
- get_or_create: 从缓存获取或创建
- ListenerCache: 收集和恢复状态
"""
# pylint: disable=protected-access
from hft.core.listener import Listener
from hft.core.listener_cache import ListenerCache, build_cache_key, get_or_create


class MockListener(Listener):
    """Mock listener for testing."""

    def __init__(self, name=None, interval=1.0, value=0):
        super().__init__(name=name, interval=interval)
        self.value = value

    async def on_tick(self):
        pass


class TestBuildCacheKey:
    """Tests for build_cache_key function."""

    def test_root_listener_key(self):
        """Root listener should have simple key format."""
        key = build_cache_key(MockListener, 'root', None)
        assert key == 'MockListener:root'

    def test_child_listener_key(self):
        """Child listener should include parent path."""
        root = MockListener(name='root')
        key = build_cache_key(MockListener, 'child', root)
        assert key == 'MockListener:child/MockListener:root'

    def test_nested_listener_key(self):
        """Deeply nested listener should have full path."""
        root = MockListener(name='root')
        child1 = MockListener(name='child1')
        root.add_child(child1)
        child2 = MockListener(name='child2')
        child1.add_child(child2)

        key = build_cache_key(MockListener, 'grandchild', child2)
        assert key == 'MockListener:grandchild/MockListener:child2/MockListener:child1/MockListener:root'


class TestGetOrCreate:
    """Tests for get_or_create function."""

    def test_create_new_instance(self):
        """Should create new instance when cache is empty."""
        cache = {}
        instance = get_or_create(cache, MockListener, 'test', None, value=42)

        assert instance.name == 'test'
        assert instance.value == 42

    def test_restore_from_cache(self):
        """Should restore instance from cache."""
        # Create and serialize
        original = MockListener(name='test', value=100)
        cache = {
            'MockListener:test': original.__getstate__()
        }

        # Restore
        restored = get_or_create(cache, MockListener, 'test', None)

        assert restored.name == 'test'
        assert restored.value == 100

    def test_add_to_parent(self):
        """Should add child to parent when parent provided."""
        cache = {}
        parent = MockListener(name='parent')
        child = get_or_create(cache, MockListener, 'child', parent, value=10)

        assert 'child' in parent.children
        assert parent.children['child'] is child

    def test_restore_child_with_parent(self):
        """Should restore child and add to parent."""
        # Create original structure
        original_parent = MockListener(name='parent')
        original_child = MockListener(name='child', value=50)
        original_parent.add_child(original_child)

        # Build cache
        cache_mgr = ListenerCache()
        cache = cache_mgr.collect(original_parent)

        # Create new parent and restore child
        new_parent = MockListener(name='parent')
        restored_child = get_or_create(cache, MockListener, 'child', new_parent)

        assert restored_child.value == 50
        assert 'child' in new_parent.children


class TestListenerCache:
    """Tests for ListenerCache class."""

    def test_collect_single_listener(self):
        """Should collect state from single listener."""
        listener = MockListener(name='test', value=123)
        cache_mgr = ListenerCache()

        cache = cache_mgr.collect(listener)

        assert len(cache) == 1
        assert 'MockListener:test' in cache
        assert cache['MockListener:test']['value'] == 123

    def test_collect_tree(self):
        """Should collect state from all listeners in tree."""
        root = MockListener(name='root', value=1)
        child1 = MockListener(name='child1', value=2)
        child2 = MockListener(name='child2', value=3)
        grandchild = MockListener(name='grandchild', value=4)

        root.add_child(child1)
        root.add_child(child2)
        child1.add_child(grandchild)

        cache_mgr = ListenerCache()
        cache = cache_mgr.collect(root)

        assert len(cache) == 4
        assert 'MockListener:root' in cache
        assert 'MockListener:child1/MockListener:root' in cache
        assert 'MockListener:child2/MockListener:root' in cache
        assert 'MockListener:grandchild/MockListener:child1/MockListener:root' in cache

    def test_state_excludes_children(self):
        """Collected state should not include _children."""
        root = MockListener(name='root')
        child = MockListener(name='child')
        root.add_child(child)

        cache_mgr = ListenerCache()
        cache = cache_mgr.collect(root)

        # Check that _children is not in state
        for key, state in cache.items():
            assert '_children' not in state, f"_children found in state for {key}"

    def test_restore_method(self):
        """Restore method should work like get_or_create."""
        original = MockListener(name='test', value=999)
        cache_mgr = ListenerCache()
        cache = cache_mgr.collect(original)

        restored = cache_mgr.restore(cache, MockListener, 'test', None)

        assert restored.value == 999

    def test_clear_cache(self):
        """Clear should empty internal cache."""
        cache_mgr = ListenerCache()
        cache_mgr._cache['test'] = {'foo': 'bar'}

        cache_mgr.clear()

        assert len(cache_mgr._cache) == 0


class TestListenerPickleExclude:
    """Tests for Listener __pickle_exclude__."""

    def test_children_excluded(self):
        """_children should be in __pickle_exclude__."""
        assert '_children' in Listener.__pickle_exclude__

    def test_getstate_excludes_children(self):
        """__getstate__ should not include _children."""
        root = MockListener(name='root')
        child = MockListener(name='child')
        root.add_child(child)

        state = root.__getstate__()

        assert '_children' not in state
        assert '_parent' not in state
        assert '_alock' not in state

    def test_setstate_initializes_empty_children(self):
        """__setstate__ should initialize empty _children."""
        original = MockListener(name='test', value=42)
        state = original.__getstate__()

        # Create new instance and restore
        restored = MockListener.__new__(MockListener)
        restored.__setstate__(state)

        assert hasattr(restored, '_children')
        assert len(restored._children) == 0
        assert restored.value == 42

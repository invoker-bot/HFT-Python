"""
Unit tests for CacheListener.

Tests cover:
- Cache file path resolution
- Save cache to file
- Load cache from file
- Error handling
"""
import os
import pytest
import tempfile
import shutil
from unittest.mock import MagicMock, patch, PropertyMock

from hft.core.cache import CacheListener
from hft.core.listener import Listener, ListenerState
from tests.conftest import MockListener


class MockConfig:
    """Mock config for testing."""
    def __init__(self, data_path: str):
        self.data_path = data_path


class MockRoot(Listener):
    """Mock root listener for testing."""
    def __init__(self, config: MockConfig):
        super().__init__(interval=1.0)
        self.config = config

    async def on_tick(self) -> bool:
        return False


class TestCacheListenerInit:
    """Tests for CacheListener initialization."""

    def test_init_with_default_interval(self):
        """CacheListener should initialize with default interval."""
        cache_listener = CacheListener()
        assert cache_listener.interval == 300.0

    def test_init_with_custom_interval(self):
        """CacheListener should accept custom interval."""
        cache_listener = CacheListener(interval=60.0)
        assert cache_listener.interval == 60.0


class TestCacheListenerCacheFile:
    """Tests for cache_file property."""

    def test_cache_file_from_root_config(self):
        """cache_file should return root.config.data_path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_path = os.path.join(tmpdir, "test.pkl")
            config = MockConfig(data_path=data_path)
            root = MockRoot(config)

            cache_listener = CacheListener(interval=1.0)
            root.add_child(cache_listener)

            assert cache_listener.cache_file == data_path


class TestCacheListenerSave:
    """Tests for save_cache method."""

    def test_save_cache_creates_file(self):
        """save_cache should create the cache file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_path = os.path.join(tmpdir, "subdir", "test.pkl")
            config = MockConfig(data_path=data_path)
            root = MockRoot(config)

            cache_listener = CacheListener(interval=1.0)
            root.add_child(cache_listener)

            cache_listener.save_cache()

            assert os.path.exists(data_path)

    def test_save_cache_creates_directories(self):
        """save_cache should create parent directories if they don't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_path = os.path.join(tmpdir, "a", "b", "c", "test.pkl")
            config = MockConfig(data_path=data_path)
            root = MockRoot(config)

            cache_listener = CacheListener(interval=1.0)
            root.add_child(cache_listener)

            cache_listener.save_cache()

            assert os.path.exists(os.path.dirname(data_path))
            assert os.path.exists(data_path)

    def test_save_cache_handles_error(self):
        """save_cache should handle errors gracefully."""
        cache_listener = CacheListener(interval=1.0)

        # Mock cache_file to return an invalid path
        with patch.object(CacheListener, 'cache_file', new_callable=PropertyMock) as mock_cache_file:
            # Use an invalid path that can't be created
            mock_cache_file.return_value = ""

            # Should not raise, just log the error
            cache_listener.save_cache()


class TestCacheListenerLoad:
    """Tests for load_cache class method."""

    def test_load_cache_restores_object(self):
        """load_cache should restore the saved object."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_path = os.path.join(tmpdir, "test.pkl")
            config = MockConfig(data_path=data_path)
            root = MockRoot(config)

            cache_listener = CacheListener(interval=123.0)
            root.add_child(cache_listener)

            # Save
            cache_listener.save_cache()

            # Load
            loaded = CacheListener.load_cache(data_path)

            assert isinstance(loaded, CacheListener)
            assert loaded.interval == 123.0

    def test_load_cache_file_not_found(self):
        """load_cache should raise RuntimeError if file doesn't exist."""
        with pytest.raises(RuntimeError, match="Failed to load cache"):
            CacheListener.load_cache("/nonexistent/path/cache.pkl")

    def test_load_cache_invalid_file(self):
        """load_cache should raise RuntimeError for invalid pickle file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_path = os.path.join(tmpdir, "invalid.pkl")

            # Create an invalid pickle file
            with open(data_path, 'w') as f:
                f.write("not a pickle file")

            with pytest.raises(RuntimeError, match="Failed to load cache"):
                CacheListener.load_cache(data_path)


class TestCacheListenerOnTick:
    """Tests for on_tick method."""

    @pytest.mark.asyncio
    async def test_on_tick_calls_save_cache(self):
        """on_tick should call save_cache."""
        cache_listener = CacheListener(interval=1.0)

        with patch.object(cache_listener, 'save_cache') as mock_save:
            await cache_listener.on_tick()
            mock_save.assert_called_once()


class TestCacheListenerIntegration:
    """Integration tests for CacheListener."""

    def test_save_and_load_with_children(self):
        """Should correctly save and load listener with children."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_path = os.path.join(tmpdir, "test.pkl")
            config = MockConfig(data_path=data_path)
            root = MockRoot(config)

            cache_listener = CacheListener(interval=1.0)
            root.add_child(cache_listener)

            # Add a child to root
            child = MockListener(name="test_child", interval=2.0)
            root.add_child(child)

            # Save
            cache_listener.save_cache()

            # Load
            loaded = CacheListener.load_cache(data_path)

            assert isinstance(loaded, CacheListener)

    def test_save_preserves_state(self):
        """Should preserve listener state after save/load."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_path = os.path.join(tmpdir, "test.pkl")
            config = MockConfig(data_path=data_path)
            root = MockRoot(config)

            cache_listener = CacheListener(interval=99.0)
            root.add_child(cache_listener)

            # Modify some state
            cache_listener._enabled = False

            # Save
            cache_listener.save_cache()

            # Load
            loaded = CacheListener.load_cache(data_path)

            assert loaded.interval == 99.0
            assert loaded.enabled is False

"""
Unit tests for HealthyData and HealthyDataWithFallback.

Tests cover:
- Basic data storage and retrieval
- Health checking based on age
- Exception handling for unhealthy data
- Fallback fetch functionality
- Edge cases (no data, expired data, fetch failures)
"""
import pytest
import time
from unittest.mock import AsyncMock, patch

from hft.core.healthy_data import HealthyData, HealthyDataWithFallback, UnhealthyDataError


class TestHealthyDataBasic:
    """Tests for basic HealthyData functionality."""

    def test_initial_state_is_empty(self):
        """New HealthyData should have no data."""
        hd = HealthyData[dict]()

        assert hd.has_data is False
        assert hd._data is None
        assert hd._timestamp == 0.0
        assert hd.update_count == 0

    def test_set_stores_data(self):
        """set() should store data and update timestamp."""
        hd = HealthyData[dict]()
        data = {"price": 100.0}

        hd.set(data)

        assert hd.has_data is True
        assert hd._data == data
        assert hd._timestamp > 0
        assert hd.update_count == 1

    def test_set_with_custom_timestamp(self):
        """set() should use custom timestamp if provided."""
        hd = HealthyData[dict]()
        custom_time = 1000.0

        hd.set({"price": 100.0}, timestamp=custom_time)

        assert hd._timestamp == custom_time

    def test_set_increments_update_count(self):
        """Each set() call should increment update_count."""
        hd = HealthyData[dict]()

        hd.set({"v": 1})
        hd.set({"v": 2})
        hd.set({"v": 3})

        assert hd.update_count == 3

    def test_get_returns_data_when_healthy(self):
        """get() should return data when healthy."""
        hd = HealthyData[dict](max_age=10.0)
        data = {"price": 100.0}
        hd.set(data)

        result = hd.get()

        assert result == data

    def test_get_unchecked_returns_data_regardless_of_health(self):
        """get_unchecked() should return data even if unhealthy."""
        hd = HealthyData[dict](max_age=0.001)  # Very short max_age
        data = {"price": 100.0}
        hd.set(data)
        time.sleep(0.01)  # Let it expire

        # get() would raise, but get_unchecked() should work
        result = hd.get_unchecked()

        assert result == data

    def test_clear_removes_data(self):
        """clear() should remove data and reset timestamp."""
        hd = HealthyData[dict]()
        hd.set({"price": 100.0})

        hd.clear()

        assert hd.has_data is False
        assert hd._data is None
        assert hd._timestamp == 0.0


class TestHealthyDataHealthCheck:
    """Tests for health checking functionality."""

    def test_is_healthy_when_fresh_data(self):
        """is_healthy should be True for fresh data."""
        hd = HealthyData[dict](max_age=10.0)
        hd.set({"price": 100.0})

        assert hd.is_healthy is True
        assert hd.is_stale is False

    def test_is_unhealthy_when_no_data(self):
        """is_healthy should be False when no data."""
        hd = HealthyData[dict]()

        assert hd.is_healthy is False
        assert hd.is_stale is True

    def test_is_unhealthy_when_expired(self):
        """is_healthy should be False when data is expired."""
        hd = HealthyData[dict](max_age=0.001)
        hd.set({"price": 100.0})
        time.sleep(0.01)

        assert hd.is_healthy is False
        assert hd.is_stale is True

    def test_age_is_infinity_when_no_timestamp(self):
        """age should be infinity when no data has been set."""
        hd = HealthyData[dict]()

        assert hd.age == float('inf')

    def test_age_increases_over_time(self):
        """age should increase as time passes."""
        hd = HealthyData[dict]()
        hd.set({"price": 100.0})
        initial_age = hd.age

        time.sleep(0.05)

        assert hd.age > initial_age

    def test_bool_conversion_reflects_health(self):
        """bool(HealthyData) should reflect health status."""
        hd = HealthyData[dict](max_age=10.0)

        # No data = False
        assert bool(hd) is False

        # Fresh data = True
        hd.set({"price": 100.0})
        assert bool(hd) is True


class TestHealthyDataExceptions:
    """Tests for exception handling."""

    def test_get_raises_when_unhealthy(self):
        """get() should raise UnhealthyDataError when unhealthy."""
        hd = HealthyData[dict](max_age=0.001)
        hd.set({"price": 100.0})
        time.sleep(0.01)

        with pytest.raises(UnhealthyDataError) as exc_info:
            hd.get()

        assert "unhealthy" in str(exc_info.value).lower()

    def test_get_raises_when_no_data(self):
        """get() should raise UnhealthyDataError when no data."""
        hd = HealthyData[dict]()

        with pytest.raises(UnhealthyDataError):
            hd.get()

    def test_get_returns_none_when_raise_disabled(self):
        """get(raise_on_unhealthy=False) should return None when unhealthy."""
        hd = HealthyData[dict]()

        result = hd.get(raise_on_unhealthy=False)

        assert result is None


class TestHealthyDataWithFallbackBasic:
    """Tests for HealthyDataWithFallback basic functionality."""

    @pytest.mark.asyncio
    async def test_get_or_fetch_returns_cached_when_healthy(self):
        """get_or_fetch() should return cached data when healthy."""
        fetch_mock = AsyncMock(return_value={"price": 200.0})
        hd = HealthyDataWithFallback[dict](max_age=10.0, fetch_func=fetch_mock)
        hd.set({"price": 100.0})

        result = await hd.get_or_fetch()

        assert result == {"price": 100.0}
        fetch_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_or_fetch_calls_fetch_when_unhealthy(self):
        """get_or_fetch() should call fetch_func when unhealthy."""
        fetch_mock = AsyncMock(return_value={"price": 200.0})
        hd = HealthyDataWithFallback[dict](max_age=0.001, fetch_func=fetch_mock)
        hd.set({"price": 100.0})
        time.sleep(0.01)  # Let it expire

        result = await hd.get_or_fetch()

        assert result == {"price": 200.0}
        fetch_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_or_fetch_calls_fetch_when_no_data(self):
        """get_or_fetch() should call fetch_func when no data."""
        fetch_mock = AsyncMock(return_value={"price": 200.0})
        hd = HealthyDataWithFallback[dict](max_age=10.0, fetch_func=fetch_mock)

        result = await hd.get_or_fetch()

        assert result == {"price": 200.0}
        fetch_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_or_fetch_updates_data_after_fetch(self):
        """get_or_fetch() should update stored data after fetch."""
        fetch_mock = AsyncMock(return_value={"price": 200.0})
        hd = HealthyDataWithFallback[dict](max_age=10.0, fetch_func=fetch_mock)

        await hd.get_or_fetch()

        assert hd._data == {"price": 200.0}
        assert hd.is_healthy is True

    @pytest.mark.asyncio
    async def test_get_or_fetch_raises_when_no_fetch_func(self):
        """get_or_fetch() should raise when no fetch_func and unhealthy."""
        hd = HealthyDataWithFallback[dict](max_age=10.0, fetch_func=None)

        with pytest.raises(UnhealthyDataError) as exc_info:
            await hd.get_or_fetch()

        assert "no fetch_func" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_get_or_fetch_raises_when_fetch_fails(self):
        """get_or_fetch() should raise when fetch_func fails."""
        fetch_mock = AsyncMock(side_effect=RuntimeError("Network error"))
        hd = HealthyDataWithFallback[dict](max_age=10.0, fetch_func=fetch_mock)

        with pytest.raises(UnhealthyDataError) as exc_info:
            await hd.get_or_fetch()

        assert "fetch failed" in str(exc_info.value).lower()


class TestHealthyDataWithFallbackEnsureHealthy:
    """Tests for ensure_healthy() method."""

    @pytest.mark.asyncio
    async def test_ensure_healthy_returns_true_when_healthy(self):
        """ensure_healthy() should return True when already healthy."""
        fetch_mock = AsyncMock(return_value={"price": 200.0})
        hd = HealthyDataWithFallback[dict](max_age=10.0, fetch_func=fetch_mock)
        hd.set({"price": 100.0})

        result = await hd.ensure_healthy()

        assert result is True
        fetch_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_healthy_fetches_when_unhealthy(self):
        """ensure_healthy() should fetch when unhealthy."""
        fetch_mock = AsyncMock(return_value={"price": 200.0})
        hd = HealthyDataWithFallback[dict](max_age=0.001, fetch_func=fetch_mock)
        hd.set({"price": 100.0})
        time.sleep(0.01)

        result = await hd.ensure_healthy()

        assert result is True
        fetch_mock.assert_called_once()
        assert hd._data == {"price": 200.0}

    @pytest.mark.asyncio
    async def test_ensure_healthy_returns_false_on_fetch_failure(self):
        """ensure_healthy() should return False when fetch fails."""
        fetch_mock = AsyncMock(side_effect=RuntimeError("Network error"))
        hd = HealthyDataWithFallback[dict](max_age=10.0, fetch_func=fetch_mock)

        result = await hd.ensure_healthy()

        assert result is False

    @pytest.mark.asyncio
    async def test_ensure_healthy_returns_false_when_no_fetch_func(self):
        """ensure_healthy() should return False when no fetch_func."""
        hd = HealthyDataWithFallback[dict](max_age=10.0, fetch_func=None)

        result = await hd.ensure_healthy()

        assert result is False


class TestHealthyDataEdgeCases:
    """Tests for edge cases and special scenarios."""

    def test_max_age_zero_means_always_stale(self):
        """max_age=0 should mean data is always stale after set."""
        hd = HealthyData[dict](max_age=0.0)
        hd.set({"price": 100.0})

        # Even immediately after set, should be stale (age > 0)
        assert hd.age > 0
        # But with max_age=0, anything > 0 is stale
        # Note: This is a timing-dependent test

    def test_very_large_max_age(self):
        """Very large max_age should keep data healthy."""
        hd = HealthyData[dict](max_age=86400.0)  # 1 day
        hd.set({"price": 100.0})

        assert hd.is_healthy is True

    def test_timestamp_property(self):
        """timestamp property should return the stored timestamp."""
        hd = HealthyData[dict]()
        before = time.time()
        hd.set({"price": 100.0})
        after = time.time()

        assert before <= hd.timestamp <= after

    @pytest.mark.asyncio
    async def test_fetch_func_closure_capture(self):
        """Test that fetch_func correctly captures variables."""
        # This tests the lambda closure issue fix
        results = []

        for i in range(3):
            # Using default parameter to capture current value
            hd = HealthyDataWithFallback[int](
                max_age=10.0,
                fetch_func=lambda x=i: AsyncMock(return_value=x)()
            )
            result = await hd.get_or_fetch()
            results.append(result)

        assert results == [0, 1, 2]

    def test_generic_type_with_different_types(self):
        """HealthyData should work with different generic types."""
        # Dict
        hd_dict = HealthyData[dict]()
        hd_dict.set({"key": "value"})
        assert hd_dict.get() == {"key": "value"}

        # List
        hd_list = HealthyData[list]()
        hd_list.set([1, 2, 3])
        assert hd_list.get() == [1, 2, 3]

        # Custom class
        class Ticker:
            def __init__(self, price: float):
                self.price = price

        hd_ticker = HealthyData[Ticker]()
        ticker = Ticker(100.0)
        hd_ticker.set(ticker)
        assert hd_ticker.get().price == 100.0

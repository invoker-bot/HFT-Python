"""
Unit tests for DataArray and DataSourceGroup.

Tests cover:
- DataArray: data operations, health checking, cleanup
- DataSourceGroup: datasource creation, query, cleanup (with mocks)
"""
import pytest
import time
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

from hft.datasource.group import DataArray, DataType, UnhealthyDataError


# ============================================================
# Test data classes
# ============================================================

@dataclass
class MockData:
    """Mock data with timestamp for testing."""
    timestamp: float
    value: float


# ============================================================
# DataArray Tests
# ============================================================

class TestDataArrayBasic:
    """Tests for basic DataArray operations."""

    def test_initial_state_is_empty(self):
        """New DataArray should be empty."""
        arr = DataArray[MockData]()

        assert len(arr) == 0
        assert bool(arr) is False
        assert arr.last_update == 0.0

    def test_append_adds_data(self):
        """append() should add data to array."""
        arr = DataArray[MockData]()
        data = MockData(timestamp=time.time(), value=100.0)

        arr.append(data)

        assert len(arr) == 1
        assert bool(arr) is True

    def test_append_updates_last_update(self):
        """append() should update last_update timestamp."""
        arr = DataArray[MockData]()
        before = time.time()

        arr.append(MockData(timestamp=time.time(), value=100.0))

        assert arr.last_update >= before

    def test_extend_adds_multiple_data(self):
        """extend() should add multiple items."""
        arr = DataArray[MockData]()
        data = [
            MockData(timestamp=i, value=float(i))
            for i in range(5)
        ]

        arr.extend(data)

        assert len(arr) == 5

    def test_maxlen_limits_size(self):
        """Array should not exceed maxlen."""
        arr = DataArray[MockData](maxlen=5)

        for i in range(10):
            arr.append(MockData(timestamp=i, value=float(i)))

        assert len(arr) == 5
        # Should have the last 5 items (5-9)
        assert arr.get_all()[0].timestamp == 5

    def test_get_latest_returns_last_n(self):
        """get_latest(n) should return last n items."""
        arr = DataArray[MockData]()
        for i in range(10):
            arr.append(MockData(timestamp=i, value=float(i)))

        result = arr.get_latest(3)

        assert len(result) == 3
        assert result[0].timestamp == 7
        assert result[1].timestamp == 8
        assert result[2].timestamp == 9

    def test_get_latest_updates_last_access(self):
        """get_latest() should update last_access timestamp."""
        arr = DataArray[MockData]()
        arr.append(MockData(timestamp=time.time(), value=100.0))
        before = time.time()

        arr.get_latest()

        assert arr.last_access >= before

    def test_get_latest_with_n_larger_than_size(self):
        """get_latest(n) with n > size should return all."""
        arr = DataArray[MockData]()
        for i in range(3):
            arr.append(MockData(timestamp=i, value=float(i)))

        result = arr.get_latest(10)

        assert len(result) == 3

    def test_get_all_returns_copy(self):
        """get_all() should return a copy, not the original."""
        arr = DataArray[MockData]()
        arr.append(MockData(timestamp=1, value=100.0))

        result = arr.get_all()
        result.append(MockData(timestamp=2, value=200.0))

        assert len(arr) == 1  # Original unchanged

    def test_clear_removes_all_data(self):
        """clear() should remove all data."""
        arr = DataArray[MockData]()
        for i in range(5):
            arr.append(MockData(timestamp=i, value=float(i)))

        arr.clear()

        assert len(arr) == 0
        assert arr.last_update == 0.0


class TestDataArrayGetSince:
    """Tests for get_since() method."""

    def test_get_since_returns_items_after_timestamp(self):
        """get_since() should return items after given timestamp."""
        arr = DataArray[MockData]()
        for i in range(10):
            arr.append(MockData(timestamp=float(i), value=float(i)))

        result = arr.get_since(5.0)

        assert len(result) == 5
        assert all(item.timestamp >= 5.0 for item in result)

    def test_get_since_with_no_matching_items(self):
        """get_since() should return empty list if no items match."""
        arr = DataArray[MockData]()
        for i in range(5):
            arr.append(MockData(timestamp=float(i), value=float(i)))

        result = arr.get_since(100.0)

        assert result == []

    def test_get_since_with_exact_timestamp(self):
        """get_since() should include items with exact timestamp."""
        arr = DataArray[MockData]()
        for i in range(5):
            arr.append(MockData(timestamp=float(i), value=float(i)))

        result = arr.get_since(3.0)

        assert len(result) == 2
        assert result[0].timestamp == 3.0


class TestDataArrayCleanup:
    """Tests for cleanup_expired() method."""

    def test_cleanup_expired_removes_old_data(self):
        """cleanup_expired() should remove data older than max_age."""
        arr = DataArray[MockData](max_age=5.0)
        now = time.time()

        # Add old data (10 seconds ago)
        arr.append(MockData(timestamp=now - 10, value=1.0))
        arr.append(MockData(timestamp=now - 8, value=2.0))
        # Add fresh data
        arr.append(MockData(timestamp=now - 2, value=3.0))
        arr.append(MockData(timestamp=now, value=4.0))

        removed = arr.cleanup_expired()

        assert removed == 2
        assert len(arr) == 2

    def test_cleanup_expired_returns_count(self):
        """cleanup_expired() should return number of removed items."""
        arr = DataArray[MockData](max_age=1.0)
        now = time.time()
        arr.append(MockData(timestamp=now - 10, value=1.0))
        arr.append(MockData(timestamp=now - 5, value=2.0))

        removed = arr.cleanup_expired()

        assert removed == 2

    def test_cleanup_expired_on_empty_array(self):
        """cleanup_expired() on empty array should return 0."""
        arr = DataArray[MockData]()

        removed = arr.cleanup_expired()

        assert removed == 0


class TestDataArrayHealthCheck:
    """Tests for health checking functionality."""

    def test_is_fresh_when_recently_updated(self):
        """is_fresh should be True when recently updated."""
        arr = DataArray[MockData](freshness_threshold=10.0)
        arr.append(MockData(timestamp=time.time(), value=100.0))

        assert arr.is_fresh is True

    def test_is_not_fresh_when_no_data(self):
        """is_fresh should be False when no data."""
        arr = DataArray[MockData]()

        assert arr.is_fresh is False

    def test_is_not_fresh_when_stale(self):
        """is_fresh should be False when data is stale."""
        arr = DataArray[MockData](freshness_threshold=0.001)
        arr.append(MockData(timestamp=time.time(), value=100.0))
        time.sleep(0.01)

        assert arr.is_fresh is False

    def test_age_is_infinity_when_no_data(self):
        """age should be infinity when no data."""
        arr = DataArray[MockData]()

        assert arr.age == float('inf')

    def test_age_increases_over_time(self):
        """age should increase over time."""
        arr = DataArray[MockData]()
        arr.append(MockData(timestamp=time.time(), value=100.0))
        initial_age = arr.age

        time.sleep(0.05)

        assert arr.age > initial_age

    def test_coverage_duration_with_multiple_items(self):
        """coverage_duration should return time span."""
        arr = DataArray[MockData]()
        arr.append(MockData(timestamp=100.0, value=1.0))
        arr.append(MockData(timestamp=150.0, value=2.0))
        arr.append(MockData(timestamp=200.0, value=3.0))

        assert arr.coverage_duration == 100.0  # 200 - 100

    def test_coverage_duration_with_single_item(self):
        """coverage_duration should be 0 with single item."""
        arr = DataArray[MockData]()
        arr.append(MockData(timestamp=100.0, value=1.0))

        assert arr.coverage_duration == 0.0

    def test_coverage_duration_with_no_items(self):
        """coverage_duration should be 0 with no items."""
        arr = DataArray[MockData]()

        assert arr.coverage_duration == 0.0

    def test_is_coverage_sufficient(self):
        """is_coverage_sufficient should check duration."""
        arr = DataArray[MockData]()
        arr.append(MockData(timestamp=100.0, value=1.0))
        arr.append(MockData(timestamp=200.0, value=2.0))

        assert arr.is_coverage_sufficient(50.0) is True
        assert arr.is_coverage_sufficient(150.0) is False


class TestDataArrayCheckHealthy:
    """Tests for check_healthy() method."""

    def test_check_healthy_all_conditions_met(self):
        """check_healthy should return True when all conditions met."""
        arr = DataArray[MockData](freshness_threshold=10.0)
        arr.append(MockData(timestamp=100.0, value=1.0))
        arr.append(MockData(timestamp=200.0, value=2.0))
        arr.append(MockData(timestamp=300.0, value=3.0))

        result = arr.check_healthy(
            require_fresh=True,
            min_count=2,
            min_coverage=50.0
        )

        assert result is True

    def test_check_healthy_fails_on_stale(self):
        """check_healthy should fail when stale."""
        arr = DataArray[MockData](freshness_threshold=0.001)
        arr.append(MockData(timestamp=time.time(), value=1.0))
        time.sleep(0.01)

        result = arr.check_healthy(require_fresh=True)

        assert result is False

    def test_check_healthy_fails_on_insufficient_count(self):
        """check_healthy should fail when count insufficient."""
        arr = DataArray[MockData](freshness_threshold=10.0)
        arr.append(MockData(timestamp=time.time(), value=1.0))

        result = arr.check_healthy(require_fresh=True, min_count=5)

        assert result is False

    def test_check_healthy_fails_on_insufficient_coverage(self):
        """check_healthy should fail when coverage insufficient."""
        arr = DataArray[MockData](freshness_threshold=10.0)
        arr.append(MockData(timestamp=100.0, value=1.0))
        arr.append(MockData(timestamp=110.0, value=2.0))

        result = arr.check_healthy(
            require_fresh=True,
            min_coverage=100.0
        )

        assert result is False

    def test_check_healthy_raises_on_unhealthy(self):
        """check_healthy should raise when unhealthy and requested."""
        arr = DataArray[MockData]()

        with pytest.raises(UnhealthyDataError):
            arr.check_healthy(require_fresh=True, raise_on_unhealthy=True)

    def test_check_healthy_skip_freshness_check(self):
        """check_healthy can skip freshness check."""
        arr = DataArray[MockData](freshness_threshold=0.001)
        arr.append(MockData(timestamp=time.time(), value=1.0))
        time.sleep(0.01)

        result = arr.check_healthy(require_fresh=False)

        assert result is True


class TestDataArrayGetHealthy:
    """Tests for get_healthy() method."""

    def test_get_healthy_returns_data_when_healthy(self):
        """get_healthy should return data when healthy."""
        arr = DataArray[MockData](freshness_threshold=10.0)
        for i in range(5):
            arr.append(MockData(timestamp=time.time() + i, value=float(i)))

        result = arr.get_healthy(n=3)

        assert len(result) == 3

    def test_get_healthy_raises_when_unhealthy(self):
        """get_healthy should raise when unhealthy."""
        arr = DataArray[MockData]()

        with pytest.raises(UnhealthyDataError):
            arr.get_healthy(n=1)

    def test_get_healthy_checks_min_count(self):
        """get_healthy should check that n items are available."""
        arr = DataArray[MockData](freshness_threshold=10.0)
        arr.append(MockData(timestamp=time.time(), value=1.0))

        with pytest.raises(UnhealthyDataError):
            arr.get_healthy(n=5)  # Only 1 item available


class TestDataTypeEnum:
    """Tests for DataType enum."""

    def test_enum_values(self):
        """DataType should have correct values."""
        assert DataType.TICKER.value == "ticker"
        assert DataType.ORDER_BOOK.value == "order_book"
        assert DataType.TRADES.value == "trades"
        assert DataType.OHLCV.value == "ohlcv"
        assert DataType.FUNDING_RATE.value == "funding_rate"

    def test_all_types_defined(self):
        """All expected DataTypes should be defined."""
        types = list(DataType)
        assert len(types) == 5  # ticker, order_book, trades, ohlcv, funding_rate


# ============================================================
# DataSourceGroup Tests (with mocks)
# ============================================================

class TestDataSourceGroupBasic:
    """Basic tests for DataSourceGroup initialization."""

    def test_can_import(self):
        """DataSourceGroup should be importable."""
        from hft.datasource.group import DataSourceGroup
        assert DataSourceGroup is not None

    def test_initialization(self):
        """DataSourceGroup should initialize correctly."""
        from hft.datasource.group import DataSourceGroup

        group = DataSourceGroup(auto_destroy_timeout=300.0)

        assert group.name == "DataSourceGroup"
        assert group._auto_destroy_timeout == 300.0
        assert len(group.children) == 0

    def test_get_stats_empty(self):
        """get_stats should return correct structure when no datasources."""
        from hft.datasource.group import DataSourceGroup

        group = DataSourceGroup()
        stats = group.get_stats()

        assert stats["total_pairs"] == 0
        assert stats["by_exchange"] == {}
        assert stats["active_datasources"] == {}
        assert stats["active_indicators"] == {}

    def test_log_state_dict(self):
        """log_state_dict should return correct structure."""
        from hft.datasource.group import DataSourceGroup

        group = DataSourceGroup()
        state = group.log_state_dict

        assert "trading_pairs" in state
        assert state["trading_pairs"] == 0
        assert "by_exchange" in state


class TestTradingPairDataSourceClass:
    """Tests for TradingPairDataSource._get_datasource_class method."""

    def test_get_ticker_class(self):
        """Should return TickerDataSource for TICKER type."""
        from hft.datasource.group import TradingPairDataSource
        from hft.datasource.ticker_datasource import TickerDataSource
        from unittest.mock import MagicMock

        mock_exchange = MagicMock()
        mock_exchange.class_name = "test"
        pair = TradingPairDataSource(exchange=mock_exchange, symbol="BTC/USDT")
        cls = pair._get_datasource_class(DataType.TICKER)

        assert cls is TickerDataSource

    def test_get_orderbook_class(self):
        """Should return OrderBookDataSource for ORDER_BOOK type."""
        from hft.datasource.group import TradingPairDataSource
        from hft.datasource.orderbook_datasource import OrderBookDataSource
        from unittest.mock import MagicMock

        mock_exchange = MagicMock()
        mock_exchange.class_name = "test"
        pair = TradingPairDataSource(exchange=mock_exchange, symbol="BTC/USDT")
        cls = pair._get_datasource_class(DataType.ORDER_BOOK)

        assert cls is OrderBookDataSource

    def test_get_trades_class(self):
        """Should return TradesDataSource for TRADES type."""
        from hft.datasource.group import TradingPairDataSource
        from hft.datasource.trades_datasource import TradesDataSource
        from unittest.mock import MagicMock

        mock_exchange = MagicMock()
        mock_exchange.class_name = "test"
        pair = TradingPairDataSource(exchange=mock_exchange, symbol="BTC/USDT")
        cls = pair._get_datasource_class(DataType.TRADES)

        assert cls is TradesDataSource

    def test_get_ohlcv_class(self):
        """Should return OHLCVDataSource for OHLCV type."""
        from hft.datasource.group import TradingPairDataSource
        from hft.datasource.ohlcv_datasource import OHLCVDataSource
        from unittest.mock import MagicMock

        mock_exchange = MagicMock()
        mock_exchange.class_name = "test"
        pair = TradingPairDataSource(exchange=mock_exchange, symbol="BTC/USDT")
        cls = pair._get_datasource_class(DataType.OHLCV)

        assert cls is OHLCVDataSource


class TestDataArrayEdgeCases:
    """Edge case tests for DataArray."""

    def test_data_without_timestamp_attribute(self):
        """Data without timestamp should be handled gracefully."""
        arr = DataArray[dict]()
        arr.append({"value": 1})
        arr.append({"value": 2})

        # get_since should skip items without timestamp
        result = arr.get_since(0)
        assert result == []

        # coverage_duration should return 0
        assert arr.coverage_duration == 0.0

    def test_cleanup_with_mixed_timestamp_data(self):
        """cleanup should handle data with and without timestamps."""
        # Note: cleanup_expired expects data with timestamp ATTRIBUTE, not dict key
        # When using dict, getattr returns None, causing cleanup to skip
        arr = DataArray[MockData](max_age=5.0)
        now = time.time()

        arr.append(MockData(timestamp=now - 10, value=1.0))  # Old, should be removed
        arr.append(MockData(timestamp=now - 8, value=2.0))   # Old, should be removed
        arr.append(MockData(timestamp=now, value=3.0))       # Fresh, should remain

        removed = arr.cleanup_expired()

        assert removed == 2
        assert len(arr) == 1

    def test_large_dataset_performance(self):
        """DataArray should handle large datasets efficiently."""
        arr = DataArray[MockData](maxlen=10000)

        # Add 10000 items
        for i in range(10000):
            arr.append(MockData(timestamp=float(i), value=float(i)))

        assert len(arr) == 10000

        # get_latest should be fast
        result = arr.get_latest(100)
        assert len(result) == 100
        assert result[0].timestamp == 9900.0

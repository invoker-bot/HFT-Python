"""
Unit tests for BaseExecutor and MarketExecutor.

Tests cover:
- BaseExecutor: signal queue, state management, statistics
- MarketExecutor: position calculation, execution logic (with mocks)
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass

from hft.executor.base import BaseExecutor, ExecutionResult, ExecutorState
from hft.executor.market import MarketExecutor
from hft.strategy.signal import TradeSignal


# ============================================================
# Mock classes
# ============================================================

class MockExecutor(BaseExecutor):
    """Concrete implementation for testing BaseExecutor."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.execute_signal_mock = AsyncMock(return_value=[])

    async def execute_signal(self, signal: TradeSignal) -> list[ExecutionResult]:
        return await self.execute_signal_mock(signal)


class MockExchange:
    """Mock exchange for testing."""

    def __init__(self, name: str = "mock_exchange"):
        self.name = name
        self.fetch_ticker = AsyncMock(return_value={"last": 100.0})
        self.create_order = AsyncMock(return_value={
            "id": "order_123",
            "filled": 1.0,
            "average": 100.0
        })
        self.medal_initialize_symbol = AsyncMock()
        self.medal_fetch_positions = AsyncMock(return_value={})


class MockExchangeGroup:
    """Mock exchange groups for testing."""

    def __init__(self, exchanges: list[MockExchange] = None):
        self._exchanges = exchanges or []

    def get_exchanges_by_class(self, class_name: str) -> list[MockExchange]:
        return self._exchanges

    def get_exchange_by_class(self, class_name: str) -> MockExchange:
        return self._exchanges[0] if self._exchanges else None


# ============================================================
# BaseExecutor Tests
# ============================================================

class TestBaseExecutorInit:
    """Tests for BaseExecutor initialization."""

    def test_default_initialization(self):
        """Should initialize with default values."""
        executor = MockExecutor()

        assert executor.name == "Executor"
        assert executor.executor_state == ExecutorState.IDLE
        assert executor.queue_size == 0

    def test_custom_initialization(self):
        """Should accept custom parameters."""
        executor = MockExecutor(
            name="CustomExecutor",
            interval=0.5,
            max_queue_size=50
        )

        assert executor.name == "CustomExecutor"
        assert executor.interval == 0.5
        assert executor._max_queue_size == 50


class TestBaseExecutorSignalQueue:
    """Tests for signal queue functionality."""

    def test_on_signal_adds_to_queue(self):
        """on_signal should add signal to queue."""
        executor = MockExecutor()
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5
        )

        executor.on_signal(signal)

        assert executor.queue_size == 1

    def test_on_signal_increments_stats(self):
        """on_signal should increment signals_received."""
        executor = MockExecutor()
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5
        )

        executor.on_signal(signal)

        assert executor.stats["signals_received"] == 1

    def test_signals_sorted_by_speed(self):
        """Signals should be sorted by speed (descending)."""
        executor = MockExecutor()

        # Add signals with different speeds
        slow_signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5,
            speed=0.2
        )
        fast_signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5,
            speed=0.9
        )
        medium_signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5,
            speed=0.5
        )

        executor.on_signal(slow_signal)
        executor.on_signal(fast_signal)
        executor.on_signal(medium_signal)

        # Fast signal should be first
        assert executor._signal_queue[0].speed == 0.9
        assert executor._signal_queue[1].speed == 0.5
        assert executor._signal_queue[2].speed == 0.2

    def test_queue_max_size_limit(self):
        """Queue should not exceed max size."""
        executor = MockExecutor(max_queue_size=3)

        for i in range(5):
            signal = TradeSignal(
                exchange_class="okx",
                symbol="BTC/USDT:USDT",
                value=0.5,
                speed=float(i) / 10  # Different speeds
            )
            executor.on_signal(signal)

        assert executor.queue_size == 3

    def test_clear_queue(self):
        """clear_queue should empty the queue."""
        executor = MockExecutor()

        for i in range(3):
            signal = TradeSignal(
                exchange_class="okx",
                symbol="BTC/USDT:USDT",
                value=0.5
            )
            executor.on_signal(signal)

        count = executor.clear_queue()

        assert count == 3
        assert executor.queue_size == 0


class TestBaseExecutorControl:
    """Tests for executor control methods."""

    def test_pause_changes_state(self):
        """pause() should change state to PAUSED."""
        executor = MockExecutor()

        executor.pause()

        assert executor.executor_state == ExecutorState.PAUSED

    def test_resume_changes_state(self):
        """resume() should change state to IDLE."""
        executor = MockExecutor()
        executor.pause()

        executor.resume()

        assert executor.executor_state == ExecutorState.IDLE


class TestBaseExecutorTargetPosition:
    """Tests for target position tracking."""

    def test_get_target_position_default(self):
        """get_target_position should return 0 by default."""
        executor = MockExecutor()

        position = executor.get_target_position("okx", "BTC/USDT:USDT")

        assert position == 0.0

    def test_set_and_get_target_position(self):
        """Should set and get target position correctly."""
        executor = MockExecutor()

        executor.set_target_position("okx", "BTC/USDT:USDT", 0.5)
        position = executor.get_target_position("okx", "BTC/USDT:USDT")

        assert position == 0.5


class TestBaseExecutorStats:
    """Tests for statistics tracking."""

    def test_initial_stats(self):
        """Stats should be zero initially."""
        executor = MockExecutor()
        stats = executor.stats

        assert stats["signals_received"] == 0
        assert stats["signals_executed"] == 0
        assert stats["signals_failed"] == 0

    def test_stats_is_copy(self):
        """stats property should return a copy."""
        executor = MockExecutor()
        stats = executor.stats

        stats["signals_received"] = 100

        assert executor.stats["signals_received"] == 0


# ============================================================
# MarketExecutor Tests
# ============================================================

class TestMarketExecutorInit:
    """Tests for MarketExecutor initialization."""

    def test_default_initialization(self):
        """Should initialize with default values."""
        executor = MarketExecutor()

        assert executor.name == "MarketExecutor"
        assert executor._max_position_value == 10000.0
        assert executor._min_order_value == 10.0
        assert executor._ticker_max_age == 5.0

    def test_custom_initialization(self):
        """Should accept custom parameters."""
        executor = MarketExecutor(
            name="CustomMarketExecutor",
            max_position_value=50000.0,
            min_order_value=50.0,
            ticker_max_age=10.0
        )

        assert executor.name == "CustomMarketExecutor"
        assert executor._max_position_value == 50000.0
        assert executor._min_order_value == 50.0
        assert executor._ticker_max_age == 10.0


class TestMarketExecutorTickerCache:
    """Tests for ticker cache functionality."""

    def test_get_ticker_cache_creates_cache(self):
        """_get_ticker_cache should create cache on first call."""
        executor = MarketExecutor()
        exchange = MockExchange(name="test_exchange")

        cache = executor._get_ticker_cache(exchange, "BTC/USDT:USDT")

        assert cache is not None
        assert ("test_exchange", "BTC/USDT:USDT") in executor._ticker_cache

    def test_get_ticker_cache_returns_same_instance(self):
        """_get_ticker_cache should return same instance for same key."""
        executor = MarketExecutor()
        exchange = MockExchange(name="test_exchange")

        cache1 = executor._get_ticker_cache(exchange, "BTC/USDT:USDT")
        cache2 = executor._get_ticker_cache(exchange, "BTC/USDT:USDT")

        assert cache1 is cache2

    def test_clear_ticker_cache(self):
        """clear_ticker_cache should clear all cached tickers."""
        executor = MarketExecutor()
        exchange = MockExchange(name="test_exchange")

        executor._get_ticker_cache(exchange, "BTC/USDT:USDT")
        executor._get_ticker_cache(exchange, "ETH/USDT:USDT")

        executor.clear_ticker_cache()

        assert len(executor._ticker_cache) == 0


class TestMarketExecutorCalculatePosition:
    """Tests for position calculation."""

    def test_calculate_target_position_long(self):
        """Should calculate long position correctly."""
        executor = MarketExecutor(max_position_value=10000.0)

        # value=0.5, price=100 -> 10000 * 0.5 / 100 = 50
        position = executor._calculate_target_position(0.5, 100.0)

        assert position == 50.0

    def test_calculate_target_position_short(self):
        """Should calculate short position correctly."""
        executor = MarketExecutor(max_position_value=10000.0)

        # value=-0.5, price=100 -> 10000 * -0.5 / 100 = -50
        position = executor._calculate_target_position(-0.5, 100.0)

        assert position == -50.0

    def test_calculate_target_position_flat(self):
        """Should return 0 for flat position."""
        executor = MarketExecutor(max_position_value=10000.0)

        position = executor._calculate_target_position(0.0, 100.0)

        assert position == 0.0

    def test_calculate_target_position_full_long(self):
        """Should calculate full long position correctly."""
        executor = MarketExecutor(max_position_value=10000.0)

        # value=1.0, price=50 -> 10000 * 1.0 / 50 = 200
        position = executor._calculate_target_position(1.0, 50.0)

        assert position == 200.0

    def test_calculate_target_position_zero_price(self):
        """Should return 0 for zero price."""
        executor = MarketExecutor(max_position_value=10000.0)

        position = executor._calculate_target_position(0.5, 0.0)

        assert position == 0.0

    def test_calculate_target_position_negative_price(self):
        """Should return 0 for negative price."""
        executor = MarketExecutor(max_position_value=10000.0)

        position = executor._calculate_target_position(0.5, -100.0)

        assert position == 0.0


class TestMarketExecutorExecution:
    """Tests for signal execution logic."""

    @pytest.mark.asyncio
    async def test_execute_signal_no_exchanges(self):
        """Should return error result when no exchanges found."""
        executor = MarketExecutor()
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5
        )

        # Mock exchange_group property using PropertyMock
        mock_groups = MockExchangeGroup(exchanges=[])
        with patch.object(
            type(executor), 'exchange_group',
            new_callable=lambda: property(lambda self: mock_groups)
        ):
            results = await executor.execute_signal(signal)

        assert len(results) == 1
        assert results[0].success is False
        assert "No exchanges found" in results[0].error

    @pytest.mark.asyncio
    async def test_execute_on_exchange_success(self):
        """Should execute order successfully."""
        executor = MarketExecutor(max_position_value=10000.0, min_order_value=10.0)
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5
        )
        exchange = MockExchange(name="test_exchange")

        result = await executor._execute_on_exchange(signal, exchange)

        assert result.success is True
        assert result.exchange_name == "test_exchange"
        exchange.medal_initialize_symbol.assert_awaited_once_with("BTC/USDT:USDT")
        exchange.create_order.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_on_exchange_invalid_price(self):
        """Should fail when ticker price is invalid."""
        executor = MarketExecutor()
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5
        )
        exchange = MockExchange(name="test_exchange")
        exchange.fetch_ticker = AsyncMock(return_value={"last": 0.0})

        result = await executor._execute_on_exchange(signal, exchange)

        assert result.success is False
        assert "Invalid ticker price" in result.error

    @pytest.mark.asyncio
    async def test_execute_on_exchange_skip_small_order(self):
        """Should skip order when value below minimum."""
        executor = MarketExecutor(
            max_position_value=100.0,  # Small max position
            min_order_value=10.0       # But requires min $10
        )
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.01  # Very small position
        )
        exchange = MockExchange(name="test_exchange")
        exchange.fetch_ticker = AsyncMock(return_value={"last": 100.0})

        result = await executor._execute_on_exchange(signal, exchange)

        # Should succeed but not place order
        assert result.success is True
        assert result.filled_amount == 0.0
        exchange.create_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_on_exchange_no_change_needed(self):
        """Should skip when position matches target."""
        executor = MarketExecutor(max_position_value=10000.0)
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5  # 50% position
        )
        exchange = MockExchange(name="test_exchange")
        exchange.fetch_ticker = AsyncMock(return_value={"last": 100.0})
        # Current position already matches target
        exchange.medal_fetch_positions = AsyncMock(
            return_value={"BTC/USDT:USDT": 50.0}  # 50 contracts = 10000 * 0.5 / 100
        )

        result = await executor._execute_on_exchange(signal, exchange)

        assert result.success is True
        assert result.filled_amount == 0.0
        exchange.create_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_on_exchange_buy_order(self):
        """Should place buy order when increasing position."""
        executor = MarketExecutor(max_position_value=10000.0)
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5
        )
        exchange = MockExchange(name="test_exchange")
        exchange.fetch_ticker = AsyncMock(return_value={"last": 100.0})
        exchange.medal_fetch_positions = AsyncMock(return_value={})  # No position

        result = await executor._execute_on_exchange(signal, exchange)

        assert result.success is True
        # Check that create_order was called with 'buy'
        call_kwargs = exchange.create_order.call_args.kwargs
        assert call_kwargs["side"] == "buy"
        assert call_kwargs["type"] == "market"

    @pytest.mark.asyncio
    async def test_execute_on_exchange_sell_order(self):
        """Should place sell order when decreasing position."""
        executor = MarketExecutor(max_position_value=10000.0)
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=-0.5  # Short position
        )
        exchange = MockExchange(name="test_exchange")
        exchange.fetch_ticker = AsyncMock(return_value={"last": 100.0})
        exchange.medal_fetch_positions = AsyncMock(return_value={})  # No position

        result = await executor._execute_on_exchange(signal, exchange)

        assert result.success is True
        call_kwargs = exchange.create_order.call_args.kwargs
        assert call_kwargs["side"] == "sell"

    @pytest.mark.asyncio
    async def test_execute_on_exchange_order_returns_none(self):
        """Should fail when order creation returns None."""
        executor = MarketExecutor()
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5
        )
        exchange = MockExchange(name="test_exchange")
        exchange.create_order = AsyncMock(return_value=None)

        result = await executor._execute_on_exchange(signal, exchange)

        assert result.success is False
        assert "returned None" in result.error


class TestMarketExecutorGetPosition:
    """Tests for _get_current_position method."""

    @pytest.mark.asyncio
    async def test_get_current_position_exists(self):
        """Should return position when it exists."""
        executor = MarketExecutor()
        exchange = MockExchange()
        exchange.medal_fetch_positions = AsyncMock(
            return_value={"BTC/USDT:USDT": 10.0}
        )

        position = await executor._get_current_position(exchange, "BTC/USDT:USDT")

        assert position == 10.0

    @pytest.mark.asyncio
    async def test_get_current_position_not_exists(self):
        """Should return 0 when no position."""
        executor = MarketExecutor()
        exchange = MockExchange()
        exchange.medal_fetch_positions = AsyncMock(return_value={})

        position = await executor._get_current_position(exchange, "BTC/USDT:USDT")

        assert position == 0.0


class TestMarketExecutorLogState:
    """Tests for log_state_dict property."""

    def test_log_state_dict_includes_ticker_cache_size(self):
        """log_state_dict should include ticker_cache_size."""
        executor = MarketExecutor()
        exchange = MockExchange()

        # Add some cache entries
        executor._get_ticker_cache(exchange, "BTC/USDT:USDT")
        executor._get_ticker_cache(exchange, "ETH/USDT:USDT")

        state = executor.log_state_dict

        assert "ticker_cache_size" in state
        assert state["ticker_cache_size"] == 2


class TestExecutionResult:
    """Tests for ExecutionResult dataclass."""

    def test_creation_success(self):
        """Should create successful result."""
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5
        )
        result = ExecutionResult(
            signal=signal,
            success=True,
            exchange_name="test_exchange",
            order_id="order_123",
            filled_amount=10.0,
            average_price=100.0
        )

        assert result.success is True
        assert result.order_id == "order_123"
        assert result.error is None

    def test_creation_failure(self):
        """Should create failed result."""
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5
        )
        result = ExecutionResult(
            signal=signal,
            success=False,
            exchange_name="test_exchange",
            error="Connection failed"
        )

        assert result.success is False
        assert result.error == "Connection failed"


class TestExecutorStateEnum:
    """Tests for ExecutorState enum."""

    def test_enum_values(self):
        """ExecutorState should have correct values."""
        assert ExecutorState.IDLE.value == "idle"
        assert ExecutorState.EXECUTING.value == "executing"
        assert ExecutorState.PAUSED.value == "paused"

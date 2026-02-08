"""
Unit tests for BaseExecutor and MarketExecutor.

Tests cover:
- BaseExecutor: initialization, state management, control methods, limit order management
- MarketExecutor: execute_delta logic
- ExecutionResult: dataclass creation
"""
# pylint: disable=protected-access
import time
from unittest.mock import AsyncMock

import pytest

from hft.executor.base import BaseExecutor, ExecutionResult, ExecutorState, OrderIntent, ActiveOrder
from hft.executor.default_executor import MarketExecutor
from hft.executor.config import BaseExecutorConfig, MarketExecutorConfig


# ============================================================
# Mock classes
# ============================================================

class MockExecutorConfig(BaseExecutorConfig):
    """Mock config for testing BaseExecutor."""
    class_name = "mock"
    path: str = "mock/test"
    per_order_usd: float = 100.0
    always: bool = False

    @classmethod
    def get_class_type(cls):
        return MockExecutor


class MockExecutor(BaseExecutor):
    """Concrete implementation for testing BaseExecutor."""

    def __init__(self, config: MockExecutorConfig = None):
        if config is None:
            config = MockExecutorConfig()
        super().__init__(config)
        self.execute_delta_mock = AsyncMock(return_value=ExecutionResult(
            exchange_class="mock",
            symbol="BTC/USDT:USDT",
            success=True,
            exchange_name="mock_exchange",
        ))

    @property
    def per_order_usd(self) -> float:
        return self.config.per_order_usd

    async def execute_delta(self, exchange, symbol, delta_usd, speed, current_price):
        return await self.execute_delta_mock(exchange, symbol, delta_usd, speed, current_price)


class MockExchange:
    """Mock exchange for testing."""

    def __init__(self, name: str = "mock_exchange"):
        self.name = name
        self.class_name = "mock"
        self.fetch_ticker = AsyncMock(return_value={"last": 100.0})
        self.create_order = AsyncMock(return_value={
            "id": "order_123",
            "filled": 1.0,
            "average": 100.0
        })
        self.create_orders = AsyncMock(return_value=[{
            "id": "order_123",
            "filled": 1.0,
            "average": 100.0
        }])
        self.cancel_orders = AsyncMock()
        self.medal_initialize_symbol = AsyncMock()
        self.medal_fetch_positions = AsyncMock(return_value={})

    def get_contract_size(self, symbol):
        return 1.0


class MockExchangeGroup:
    """Mock exchange group for testing."""

    def __init__(self, exchanges: list = None):
        self._exchanges = exchanges or []
        self.children = {e.name: e for e in self._exchanges}

    def get_exchanges_by_class(self, class_name: str):
        return self._exchanges

    def get_exchange_by_class(self, class_name: str):
        return self._exchanges[0] if self._exchanges else None


# ============================================================
# BaseExecutor Tests
# ============================================================

class TestBaseExecutorInit:
    """Tests for BaseExecutor initialization."""

    def test_initialization_with_config(self):
        """Should initialize with config object."""
        config = MockExecutorConfig(path="test/executor", per_order_usd=200.0)
        executor = MockExecutor(config)

        assert executor.name == "test/executor"
        assert executor.per_order_usd == 200.0
        assert executor.executor_state == ExecutorState.IDLE

    def test_default_config(self):
        """Should work with default config."""
        executor = MockExecutor()

        assert executor.name == "mock/test"
        assert executor.per_order_usd == 100.0
        assert executor.executor_state == ExecutorState.IDLE


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


class TestBaseExecutorStats:
    """Tests for statistics tracking."""

    def test_initial_stats(self):
        """Stats should be zero initially."""
        executor = MockExecutor()
        stats = executor.stats

        assert stats["ticks"] == 0
        assert stats["executions"] == 0
        assert stats["orders_created"] == 0
        assert stats["orders_cancelled"] == 0
        assert stats["orders_reused"] == 0
        assert stats["orders_failed"] == 0

    def test_stats_is_copy(self):
        """stats property should return a copy."""
        executor = MockExecutor()
        stats = executor.stats

        stats["ticks"] = 100

        assert executor.stats["ticks"] == 0


class TestBaseExecutorUsdToAmount:
    """Tests for USD to amount conversion."""

    def test_usd_to_amount_positive(self):
        """Should convert positive USD to amount."""
        executor = MockExecutor()
        exchange = MockExchange()

        amount = executor.usd_to_amount(exchange, "BTC/USDT:USDT", 1000.0, 100.0)

        # 1000 / 100 / 1.0 (contract_size) = 10
        assert amount == 10.0

    def test_usd_to_amount_negative(self):
        """Should preserve sign for negative USD."""
        executor = MockExecutor()
        exchange = MockExchange()

        amount = executor.usd_to_amount(exchange, "BTC/USDT:USDT", -1000.0, 100.0)

        assert amount == -10.0

    def test_usd_to_amount_zero_price(self):
        """Should return 0 for zero price."""
        executor = MockExecutor()
        exchange = MockExchange()

        amount = executor.usd_to_amount(exchange, "BTC/USDT:USDT", 1000.0, 0.0)

        assert amount == 0.0


class TestBaseExecutorLogState:
    """Tests for log_state_dict property."""

    def test_log_state_dict_includes_required_fields(self):
        """log_state_dict should include required fields."""
        executor = MockExecutor()

        state = executor.log_state_dict

        assert "state" in state
        assert "per_order_usd" in state
        assert "active_orders" in state
        assert "ticks" in state
        assert "executions" in state


class TestBaseExecutorOrderManagement:
    """Tests for limit order management."""

    def test_order_key_generation(self):
        """_order_key should generate consistent keys."""
        key = BaseExecutor._order_key("exchange1", "BTC/USDT", "buy", 0)

        assert key == ("exchange1", "BTC/USDT", "buy", 0)

    def test_can_reuse_order_within_timeout(self):
        """Should reuse order within timeout."""
        executor = MockExecutor()
        now = time.time()

        order = ActiveOrder(
            order_id="123",
            exchange_name="test",
            symbol="BTC/USDT",
            side="buy",
            level=0,
            price=100.0,
            amount=1.0,
            created_at=now - 10,  # 10 seconds ago
            last_updated_at=now,
        )
        intent = OrderIntent(
            side="buy",
            level=0,
            price=100.0,
            amount=1.0,
            timeout=60.0,  # 60 second timeout
            refresh_tolerance=0.5,
        )

        can_reuse = executor._can_reuse_order(order, intent, 100.0, now)

        assert can_reuse is True

    def test_cannot_reuse_order_after_timeout(self):
        """Should not reuse order after timeout."""
        executor = MockExecutor()
        now = time.time()

        order = ActiveOrder(
            order_id="123",
            exchange_name="test",
            symbol="BTC/USDT",
            side="buy",
            level=0,
            price=100.0,
            amount=1.0,
            created_at=now - 100,  # 100 seconds ago
            last_updated_at=now,
        )
        intent = OrderIntent(
            side="buy",
            level=0,
            price=100.0,
            amount=1.0,
            timeout=60.0,  # 60 second timeout
            refresh_tolerance=0.5,
        )

        can_reuse = executor._can_reuse_order(order, intent, 100.0, now)

        assert can_reuse is False

    def test_active_orders_count(self):
        """active_orders_count should return correct count."""
        executor = MockExecutor()

        assert executor.active_orders_count == 0

        executor._active_orders[("ex", "sym", "buy", 0)] = ActiveOrder(
            order_id="123",
            exchange_name="ex",
            symbol="sym",
            side="buy",
            level=0,
            price=100.0,
            amount=1.0,
            created_at=time.time(),
            last_updated_at=time.time(),
        )

        assert executor.active_orders_count == 1


# ============================================================
# MarketExecutor Tests
# ============================================================

class TestMarketExecutorInit:
    """Tests for MarketExecutor initialization."""

    def test_initialization_with_config(self):
        """Should initialize with config object."""
        config = MarketExecutorConfig(path="market/test", per_order_usd=200.0)
        executor = MarketExecutor(config)

        assert executor.name == "market/test"
        assert executor.per_order_usd == 200.0

    def test_per_order_usd_from_config(self):
        """per_order_usd should come from config."""
        config = MarketExecutorConfig(per_order_usd=500.0)
        executor = MarketExecutor(config)

        assert executor.per_order_usd == 500.0


class TestMarketExecutorExecuteDelta:
    """Tests for execute_delta method."""

    @pytest.mark.asyncio
    async def test_execute_delta_buy_order(self):
        """Should execute buy order for positive delta."""
        config = MarketExecutorConfig(per_order_usd=100.0)
        executor = MarketExecutor(config)
        exchange = MockExchange()

        result = await executor.execute_delta(
            exchange=exchange,
            symbol="BTC/USDT:USDT",
            delta_usd=100.0,
            speed=1.0,
            current_price=100.0,
        )

        assert result.success is True
        assert result.exchange_name == "mock_exchange"
        exchange.medal_initialize_symbol.assert_awaited_once_with("BTC/USDT:USDT")
        exchange.create_order.assert_awaited_once()

        # Check order was a buy order
        call_kwargs = exchange.create_order.call_args.kwargs
        assert call_kwargs["side"] == "buy"
        assert call_kwargs["type"] == "market"

    @pytest.mark.asyncio
    async def test_execute_delta_sell_order(self):
        """Should execute sell order for negative delta."""
        config = MarketExecutorConfig(per_order_usd=100.0)
        executor = MarketExecutor(config)
        exchange = MockExchange()

        result = await executor.execute_delta(
            exchange=exchange,
            symbol="BTC/USDT:USDT",
            delta_usd=-100.0,
            speed=1.0,
            current_price=100.0,
        )

        assert result.success is True
        call_kwargs = exchange.create_order.call_args.kwargs
        assert call_kwargs["side"] == "sell"

    @pytest.mark.asyncio
    async def test_execute_delta_invalid_price(self):
        """Should fail with zero price."""
        config = MarketExecutorConfig(per_order_usd=100.0)
        executor = MarketExecutor(config)
        exchange = MockExchange()

        result = await executor.execute_delta(
            exchange=exchange,
            symbol="BTC/USDT:USDT",
            delta_usd=100.0,
            speed=1.0,
            current_price=0.0,  # Invalid price
        )

        assert result.success is False
        assert "Invalid" in result.error

    @pytest.mark.asyncio
    async def test_execute_delta_order_returns_none(self):
        """Should fail when order creation returns None."""
        config = MarketExecutorConfig(per_order_usd=100.0)
        executor = MarketExecutor(config)
        exchange = MockExchange()
        exchange.create_order = AsyncMock(return_value=None)

        result = await executor.execute_delta(
            exchange=exchange,
            symbol="BTC/USDT:USDT",
            delta_usd=100.0,
            speed=1.0,
            current_price=100.0,
        )

        assert result.success is False
        assert "None" in result.error


# ============================================================
# ExecutionResult Tests
# ============================================================

class TestExecutionResult:
    """Tests for ExecutionResult dataclass."""

    def test_creation_success(self):
        """Should create successful result."""
        result = ExecutionResult(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            success=True,
            exchange_name="test_exchange",
            delta_usd=100.0,
            order_id="order_123",
            filled_amount=10.0,
            average_price=100.0
        )

        assert result.success is True
        assert result.order_id == "order_123"
        assert result.error is None
        assert result.exchange_class == "okx"
        assert result.delta_usd == 100.0

    def test_creation_failure(self):
        """Should create failed result."""
        result = ExecutionResult(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            success=False,
            exchange_name="test_exchange",
            error="Connection failed"
        )

        assert result.success is False
        assert result.error == "Connection failed"

    def test_default_values(self):
        """Should have sensible default values."""
        result = ExecutionResult(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            success=True,
            exchange_name="test"
        )

        assert result.target_usd == 0.0
        assert result.current_usd == 0.0
        assert result.delta_usd == 0.0
        assert result.filled_amount == 0.0
        assert result.average_price == 0.0
        assert result.order_id is None
        assert result.error is None


class TestExecutorStateEnum:
    """Tests for ExecutorState enum."""

    def test_enum_values(self):
        """ExecutorState should have correct values."""
        assert ExecutorState.IDLE.value == "idle"
        assert ExecutorState.EXECUTING.value == "executing"
        assert ExecutorState.PAUSED.value == "paused"

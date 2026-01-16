"""
Unit tests for Feature 0005: Executor Dynamic Conditions and Variable Injection.

Tests cover:
- BaseExecutor: condition evaluation, parameter evaluation, context variable collection
- LimitExecutor: dynamic parameter support (spread, timeout, per_order_usd as expressions)
- MarketExecutor: dynamic per_order_usd support
- SmartExecutor: indicator variable injection in route context
"""
import pytest
from unittest.mock import MagicMock, patch

from hft.executor.base import BaseExecutor, ExecutionResult, ExecutorState
from hft.executor.config import BaseExecutorConfig
from hft.executor.market_executor import MarketExecutor
from hft.executor.market_executor.config import MarketExecutorConfig
from hft.executor.limit_executor import LimitExecutor
from hft.executor.limit_executor.config import LimitExecutorConfig, LimitOrderLevel


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

    @property
    def per_order_usd(self) -> float:
        return self.config.per_order_usd

    async def execute_delta(self, exchange, symbol, delta_usd, speed, current_price):
        return ExecutionResult(
            exchange_class="mock",
            symbol=symbol,
            success=True,
            exchange_name="mock_exchange",
        )


class MockExchange:
    """Mock exchange for testing."""

    def __init__(self, name: str = "mock_exchange"):
        self.name = name
        self.class_name = "mock"

    def get_contract_size(self, symbol):
        return 1.0


class MockIndicator:
    """Mock indicator for testing variable injection."""

    def __init__(self, vars_dict: dict = None, ready: bool = True):
        self._vars = vars_dict or {}
        self._ready = ready

    def is_ready(self) -> bool:
        return self._ready

    def calculate_vars(self, direction: int) -> dict:
        return self._vars.copy()


# ============================================================
# BaseExecutor Condition Evaluation Tests
# ============================================================

class TestBaseExecutorConditionEvaluation:
    """Tests for condition evaluation in BaseExecutor."""

    def test_evaluate_condition_none_returns_true(self):
        """condition=None should always return True."""
        config = MockExecutorConfig()
        config.condition = None
        executor = MockExecutor(config)

        context = {"speed": 0.5, "direction": 1}
        result = executor.evaluate_condition(context)

        assert result is True

    def test_evaluate_condition_simple_comparison(self):
        """Should evaluate simple comparison expressions."""
        config = MockExecutorConfig()
        config.condition = "speed > 0.5"
        executor = MockExecutor(config)

        # True case
        context = {"speed": 0.8}
        assert executor.evaluate_condition(context) is True

        # False case
        context = {"speed": 0.3}
        assert executor.evaluate_condition(context) is False

    def test_evaluate_condition_with_direction(self):
        """Should evaluate conditions with direction variable."""
        config = MockExecutorConfig()
        config.condition = "buy and speed > 0.5"
        executor = MockExecutor(config)

        # Buy direction, high speed
        context = {"buy": True, "sell": False, "speed": 0.8}
        assert executor.evaluate_condition(context) is True

        # Sell direction
        context = {"buy": False, "sell": True, "speed": 0.8}
        assert executor.evaluate_condition(context) is False

    def test_evaluate_condition_with_notional(self):
        """Should evaluate conditions with notional variable."""
        config = MockExecutorConfig()
        config.condition = "notional > 1000"
        executor = MockExecutor(config)

        context = {"notional": 5000}
        assert executor.evaluate_condition(context) is True

        context = {"notional": 500}
        assert executor.evaluate_condition(context) is False

    def test_evaluate_condition_complex_expression(self):
        """Should evaluate complex boolean expressions."""
        config = MockExecutorConfig()
        config.condition = "(speed > 0.8 or notional > 10000) and buy"
        executor = MockExecutor(config)

        # High speed, buy
        context = {"speed": 0.9, "notional": 1000, "buy": True, "sell": False}
        assert executor.evaluate_condition(context) is True

        # Low speed, high notional, buy
        context = {"speed": 0.3, "notional": 20000, "buy": True, "sell": False}
        assert executor.evaluate_condition(context) is True

        # High speed, sell
        context = {"speed": 0.9, "notional": 1000, "buy": False, "sell": True}
        assert executor.evaluate_condition(context) is False

    def test_evaluate_condition_invalid_expression_returns_false(self):
        """Invalid expressions should return False (fail-safe)."""
        config = MockExecutorConfig()
        config.condition = "undefined_var > 0"
        executor = MockExecutor(config)

        context = {"speed": 0.5}
        result = executor.evaluate_condition(context)

        assert result is False


# ============================================================
# BaseExecutor Parameter Evaluation Tests
# ============================================================

class TestBaseExecutorParameterEvaluation:
    """Tests for parameter evaluation in BaseExecutor."""

    def test_evaluate_param_literal_float(self):
        """Literal float should be returned as-is."""
        executor = MockExecutor()
        context = {"speed": 0.5}

        result = executor.evaluate_param(100.0, context)

        assert result == 100.0

    def test_evaluate_param_literal_int(self):
        """Literal int should be returned as-is."""
        executor = MockExecutor()
        context = {"speed": 0.5}

        result = executor.evaluate_param(100, context)

        assert result == 100

    def test_evaluate_param_literal_bool(self):
        """Literal bool should be returned as-is."""
        executor = MockExecutor()
        context = {"speed": 0.5}

        result = executor.evaluate_param(True, context)

        assert result is True

    def test_evaluate_param_expression_simple(self):
        """Should evaluate simple expression strings."""
        executor = MockExecutor()
        context = {"speed": 0.5, "notional": 1000}

        result = executor.evaluate_param("speed * 100", context)

        assert result == 50.0

    def test_evaluate_param_expression_with_functions(self):
        """Should support safe functions in expressions."""
        executor = MockExecutor()
        context = {"values": [1, 2, 3, 4, 5]}

        # len()
        result = executor.evaluate_param("len(values)", context)
        assert result == 5

        # sum()
        result = executor.evaluate_param("sum(values)", context)
        assert result == 15

        # min/max
        result = executor.evaluate_param("min(values)", context)
        assert result == 1

        result = executor.evaluate_param("max(values)", context)
        assert result == 5

        # abs()
        context = {"x": -10}
        result = executor.evaluate_param("abs(x)", context)
        assert result == 10

    def test_evaluate_param_expression_conditional(self):
        """Should evaluate conditional expressions."""
        executor = MockExecutor()

        # Python ternary: value_if_true if condition else value_if_false
        context = {"speed": 0.9}
        result = executor.evaluate_param("100 if speed > 0.5 else 50", context)
        assert result == 100

        context = {"speed": 0.3}
        result = executor.evaluate_param("100 if speed > 0.5 else 50", context)
        assert result == 50

    def test_evaluate_param_invalid_expression_returns_none(self):
        """Invalid expressions should return None."""
        executor = MockExecutor()
        context = {"speed": 0.5}

        result = executor.evaluate_param("undefined_var * 2", context)

        assert result is None


# ============================================================
# BaseExecutor Context Variable Collection Tests
# ============================================================

class TestBaseExecutorContextCollection:
    """Tests for context variable collection in BaseExecutor."""

    def test_collect_context_vars_builtin_variables(self):
        """Should include all built-in variables."""
        executor = MockExecutor()

        context = executor.collect_context_vars(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            direction=1,
            speed=0.8,
            notional=5000.0,
        )

        assert context["direction"] == 1
        assert context["buy"] is True
        assert context["sell"] is False
        assert context["speed"] == 0.8
        assert context["notional"] == 5000.0

    def test_collect_context_vars_sell_direction(self):
        """Should set buy/sell correctly for sell direction."""
        executor = MockExecutor()

        context = executor.collect_context_vars(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            direction=-1,
            speed=0.5,
            notional=1000.0,
        )

        assert context["direction"] == -1
        assert context["buy"] is False
        assert context["sell"] is True

    def test_collect_context_vars_with_indicator(self):
        """Should include variables from indicators."""
        config = MockExecutorConfig()
        config.requires = ["test_indicator"]
        executor = MockExecutor(config)

        # Mock the indicator lookup
        mock_indicator = MockIndicator(vars_dict={
            "medal_edge": 0.002,
            "volume": 10000.0,
        })

        with patch.object(executor, '_get_indicator', return_value=mock_indicator):
            context = executor.collect_context_vars(
                exchange_class="okx",
                symbol="BTC/USDT:USDT",
                direction=1,
                speed=0.5,
                notional=1000.0,
            )

        assert context["medal_edge"] == 0.002
        assert context["volume"] == 10000.0

    def test_collect_context_vars_indicator_not_ready(self):
        """Should skip indicators that are not ready."""
        config = MockExecutorConfig()
        config.requires = ["test_indicator"]
        executor = MockExecutor(config)

        mock_indicator = MockIndicator(
            vars_dict={"medal_edge": 0.002},
            ready=False,
        )

        with patch.object(executor, '_get_indicator', return_value=mock_indicator):
            context = executor.collect_context_vars(
                exchange_class="okx",
                symbol="BTC/USDT:USDT",
                direction=1,
                speed=0.5,
                notional=1000.0,
            )

        assert "medal_edge" not in context

    def test_collect_context_vars_indicator_not_found(self):
        """Should handle missing indicators gracefully."""
        config = MockExecutorConfig()
        config.requires = ["missing_indicator"]
        executor = MockExecutor(config)

        with patch.object(executor, '_get_indicator', return_value=None):
            context = executor.collect_context_vars(
                exchange_class="okx",
                symbol="BTC/USDT:USDT",
                direction=1,
                speed=0.5,
                notional=1000.0,
            )

        # Should still have built-in variables
        assert context["direction"] == 1
        assert context["speed"] == 0.5


# ============================================================
# MarketExecutor Dynamic Parameter Tests
# ============================================================

class TestMarketExecutorDynamicParams:
    """Tests for dynamic parameter support in MarketExecutor."""

    def test_per_order_usd_literal(self):
        """Literal per_order_usd should work."""
        config = MarketExecutorConfig(per_order_usd=200.0)
        executor = MarketExecutor(config)

        assert executor.per_order_usd == 200.0

    def test_per_order_usd_expression_static_property(self):
        """per_order_usd property should return default for expressions."""
        config = MarketExecutorConfig(per_order_usd="notional * 0.1")
        executor = MarketExecutor(config)

        # Static property returns default when expression is used
        assert executor.per_order_usd == 100.0

    def test_get_dynamic_per_order_usd_literal(self):
        """get_dynamic_per_order_usd should return literal value."""
        config = MarketExecutorConfig(per_order_usd=200.0)
        executor = MarketExecutor(config)

        result = executor.get_dynamic_per_order_usd(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            direction=1,
            speed=0.5,
            notional=1000.0,
        )

        assert result == 200.0

    def test_get_dynamic_per_order_usd_expression(self):
        """get_dynamic_per_order_usd should evaluate expressions."""
        config = MarketExecutorConfig(per_order_usd="notional * 0.1")
        executor = MarketExecutor(config)

        result = executor.get_dynamic_per_order_usd(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            direction=1,
            speed=0.5,
            notional=5000.0,
        )

        assert result == 500.0

    def test_get_dynamic_per_order_usd_with_speed(self):
        """get_dynamic_per_order_usd should support speed-based expressions."""
        config = MarketExecutorConfig(per_order_usd="100 if speed > 0.5 else 50")
        executor = MarketExecutor(config)

        # High speed
        result = executor.get_dynamic_per_order_usd(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            direction=1,
            speed=0.8,
            notional=1000.0,
        )
        assert result == 100.0

        # Low speed
        result = executor.get_dynamic_per_order_usd(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            direction=1,
            speed=0.3,
            notional=1000.0,
        )
        assert result == 50.0


# ============================================================
# LimitExecutor Dynamic Parameter Tests
# ============================================================

class TestLimitExecutorDynamicParams:
    """Tests for dynamic parameter support in LimitExecutor."""

    def test_limit_order_level_literal_values(self):
        """LimitOrderLevel should accept literal values."""
        level = LimitOrderLevel(
            spread=0.001,
            timeout=60.0,
            per_order_usd=100.0,
            refresh_tolerance=0.5,
            reverse=False,
        )

        assert level.spread == 0.001
        assert level.timeout == 60.0
        assert level.per_order_usd == 100.0
        assert level.refresh_tolerance == 0.5
        assert level.reverse is False

    def test_limit_order_level_expression_values(self):
        """LimitOrderLevel should accept expression strings."""
        level = LimitOrderLevel(
            spread="mid_price * 0.001",
            timeout="60 if speed > 0.5 else 120",
            per_order_usd="notional * 0.1",
            refresh_tolerance="0.3 if buy else 0.7",
            reverse="sell",
        )

        assert level.spread == "mid_price * 0.001"
        assert level.timeout == "60 if speed > 0.5 else 120"
        assert level.per_order_usd == "notional * 0.1"
        assert level.refresh_tolerance == "0.3 if buy else 0.7"
        assert level.reverse == "sell"

    def test_calculate_intents_literal_params(self):
        """_calculate_intents should work with literal parameters."""
        config = LimitExecutorConfig(
            path="limit/test",
            orders=[
                LimitOrderLevel(
                    spread=10.0,  # Absolute price difference
                    timeout=60.0,
                    per_order_usd=100.0,
                    refresh_tolerance=0.5,
                    reverse=False,
                ),
            ],
        )
        executor = LimitExecutor(config)
        exchange = MockExchange()

        intents = executor._calculate_intents(
            exchange=exchange,
            symbol="BTC/USDT:USDT",
            delta_usd=1000.0,  # Buy
            current_price=50000.0,
            speed=0.5,
        )

        assert len(intents) == 1
        intent = intents[0]
        assert intent.side == "buy"
        assert intent.price == 50000.0 - 10.0  # Buy below mid price
        assert intent.timeout == 60.0

    def test_calculate_intents_expression_params(self):
        """_calculate_intents should evaluate expression parameters."""
        config = LimitExecutorConfig(
            path="limit/test",
            orders=[
                LimitOrderLevel(
                    spread="mid_price * 0.001",  # 0.1% of mid price
                    timeout="30 if speed > 0.5 else 60",
                    per_order_usd="notional * 0.1",
                    refresh_tolerance=0.5,
                    reverse=False,
                ),
            ],
        )
        executor = LimitExecutor(config)
        exchange = MockExchange()

        # High speed case
        intents = executor._calculate_intents(
            exchange=exchange,
            symbol="BTC/USDT:USDT",
            delta_usd=1000.0,
            current_price=50000.0,
            speed=0.8,
        )

        assert len(intents) == 1
        intent = intents[0]
        assert intent.timeout == 30.0  # High speed -> 30s timeout
        # spread = 50000 * 0.001 = 50
        assert intent.price == 50000.0 - 50.0

    def test_calculate_intents_reverse_order(self):
        """_calculate_intents should handle reverse orders."""
        config = LimitExecutorConfig(
            path="limit/test",
            orders=[
                LimitOrderLevel(
                    spread=10.0,
                    timeout=60.0,
                    per_order_usd=100.0,
                    refresh_tolerance=0.5,
                    reverse=True,  # Reverse order
                ),
            ],
        )
        executor = LimitExecutor(config)
        exchange = MockExchange()

        # Buy direction with reverse -> should create sell order
        intents = executor._calculate_intents(
            exchange=exchange,
            symbol="BTC/USDT:USDT",
            delta_usd=1000.0,  # Buy direction
            current_price=50000.0,
            speed=0.5,
        )

        assert len(intents) == 1
        intent = intents[0]
        assert intent.side == "sell"  # Reversed

    def test_calculate_intents_dynamic_reverse(self):
        """_calculate_intents should evaluate dynamic reverse expression."""
        config = LimitExecutorConfig(
            path="limit/test",
            orders=[
                LimitOrderLevel(
                    spread=10.0,
                    timeout=60.0,
                    per_order_usd=100.0,
                    refresh_tolerance=0.5,
                    reverse="sell",  # Reverse only for sell direction
                ),
            ],
        )
        executor = LimitExecutor(config)
        exchange = MockExchange()

        # Buy direction, reverse="sell" evaluates to False
        intents = executor._calculate_intents(
            exchange=exchange,
            symbol="BTC/USDT:USDT",
            delta_usd=1000.0,  # Buy
            current_price=50000.0,
            speed=0.5,
        )
        assert intents[0].side == "buy"  # Not reversed

        # Sell direction, reverse="sell" evaluates to True
        intents = executor._calculate_intents(
            exchange=exchange,
            symbol="BTC/USDT:USDT",
            delta_usd=-1000.0,  # Sell
            current_price=50000.0,
            speed=0.5,
        )
        assert intents[0].side == "buy"  # Reversed from sell to buy


# ============================================================
# Safe Eval Security Tests
# ============================================================

class TestSafeEvalSecurity:
    """Tests for expression evaluation security."""

    def test_no_builtin_access(self):
        """Should not allow access to __builtins__."""
        executor = MockExecutor()
        context = {"x": 1}

        # Attempting to access __builtins__ should fail
        result = executor.evaluate_param("__builtins__", context)
        assert result is None

    def test_no_import(self):
        """Should not allow import statements."""
        executor = MockExecutor()
        context = {"x": 1}

        result = executor.evaluate_param("__import__('os')", context)
        assert result is None

    def test_only_whitelisted_functions(self):
        """Should only allow whitelisted functions."""
        executor = MockExecutor()
        context = {"x": [1, 2, 3]}

        # Allowed functions
        assert executor.evaluate_param("len(x)", context) == 3
        assert executor.evaluate_param("sum(x)", context) == 6
        assert executor.evaluate_param("min(x)", context) == 1
        assert executor.evaluate_param("max(x)", context) == 3
        assert executor.evaluate_param("abs(-5)", context) == 5
        assert executor.evaluate_param("round(3.7)", context) == 4

        # Disallowed functions should fail
        result = executor.evaluate_param("eval('1+1')", context)
        assert result is None

        result = executor.evaluate_param("exec('x=1')", context)
        assert result is None


# ============================================================
# BaseExecutor Requires Ready Gate Tests (Feature 0005)
# ============================================================

class TestBaseExecutorRequiresReadyGate:
    """Tests for requires ready gate in BaseExecutor."""

    def test_check_requires_ready_no_requires(self):
        """Should return True when no requires defined."""
        config = MockExecutorConfig()
        config.requires = []
        executor = MockExecutor(config)

        result = executor.check_requires_ready("okx", "BTC/USDT:USDT")

        assert result is True

    def test_check_requires_ready_all_ready(self):
        """Should return True when all required indicators are ready."""
        config = MockExecutorConfig()
        config.requires = ["indicator1", "indicator2"]
        executor = MockExecutor(config)

        mock_indicator = MockIndicator(ready=True)

        with patch.object(executor, '_get_indicator', return_value=mock_indicator):
            result = executor.check_requires_ready("okx", "BTC/USDT:USDT")

        assert result is True

    def test_check_requires_ready_one_not_ready(self):
        """Should return False when any required indicator is not ready."""
        config = MockExecutorConfig()
        config.requires = ["indicator1", "indicator2"]
        executor = MockExecutor(config)

        def mock_get_indicator(indicator_id, exchange_class, symbol):
            if indicator_id == "indicator1":
                return MockIndicator(ready=True)
            else:
                return MockIndicator(ready=False)

        with patch.object(executor, '_get_indicator', side_effect=mock_get_indicator):
            result = executor.check_requires_ready("okx", "BTC/USDT:USDT")

        assert result is False

    def test_check_requires_ready_indicator_not_found(self):
        """Should return False when required indicator is not found."""
        config = MockExecutorConfig()
        config.requires = ["missing_indicator"]
        executor = MockExecutor(config)

        with patch.object(executor, '_get_indicator', return_value=None):
            result = executor.check_requires_ready("okx", "BTC/USDT:USDT")

        assert result is False

    def test_check_requires_ready_none_requires(self):
        """Should return True when requires is None."""
        config = MockExecutorConfig()
        config.requires = None
        executor = MockExecutor(config)

        result = executor.check_requires_ready("okx", "BTC/USDT:USDT")

        assert result is True

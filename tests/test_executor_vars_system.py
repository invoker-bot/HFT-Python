"""
Feature 0010: Executor vars 系统 - 单元测试

测试内容：
1. ExecutorVarDefinition / ExecutorConditionalVarDefinition 配置类
2. vars 列表语义（按顺序计算）
3. conditional_vars 条件触发
4. duration 变量
5. 状态持久化
"""
import pytest
import time
from unittest.mock import MagicMock, patch
from hft.executor.config import (
    ExecutorVarDefinition,
    ExecutorConditionalVarDefinition,
)
from hft.executor.market_executor.config import MarketExecutorConfig


class TestExecutorVarDefinition:
    """ExecutorVarDefinition 配置类测试"""

    def test_basic_creation(self):
        """测试基本创建"""
        var = ExecutorVarDefinition(name="test_var", value="mid_price * 0.5")
        assert var.name == "test_var"
        assert var.value == "mid_price * 0.5"

    def test_required_fields(self):
        """测试必填字段"""
        with pytest.raises(Exception):
            ExecutorVarDefinition(name="test")  # 缺少 value

        with pytest.raises(Exception):
            ExecutorVarDefinition(value="expr")  # 缺少 name


class TestExecutorConditionalVarDefinition:
    """ExecutorConditionalVarDefinition 配置类测试"""

    def test_basic_creation(self):
        """测试基本创建"""
        var = ExecutorConditionalVarDefinition(
            value="mid_price",
            on="speed > 0.5",
            default=100.0,
        )
        assert var.value == "mid_price"
        assert var.on == "speed > 0.5"
        assert var.default == 100.0

    def test_default_value_none(self):
        """测试默认值为 None"""
        var = ExecutorConditionalVarDefinition(
            value="mid_price",
            on="speed > 0.5",
        )
        assert var.default is None


class TestExecutorConfigWithVars:
    """Executor 配置中的 vars 测试"""

    def test_vars_dict_format(self):
        """测试 vars dict 格式（旧格式）"""
        config = MarketExecutorConfig(
            per_order_usd=100,
            vars={
                "ratio": "0.5",
                "target": "ratio * notional",
            },
        )
        assert isinstance(config.vars, dict)
        assert config.vars["ratio"] == "0.5"

    def test_vars_list_format(self):
        """测试 vars list 格式（新格式）"""
        config = MarketExecutorConfig(
            per_order_usd=100,
            vars=[
                ExecutorVarDefinition(name="ratio", value="0.5"),
                ExecutorVarDefinition(name="target", value="ratio * notional"),
            ],
        )
        assert isinstance(config.vars, list)
        assert len(config.vars) == 2
        assert config.vars[0].name == "ratio"

    def test_conditional_vars(self):
        """测试 conditional_vars"""
        config = MarketExecutorConfig(
            per_order_usd=100,
            conditional_vars={
                "center_price": ExecutorConditionalVarDefinition(
                    value="mid_price",
                    on="speed > 0.8",
                    default=100.0,
                ),
            },
        )
        assert "center_price" in config.conditional_vars
        assert config.conditional_vars["center_price"].on == "speed > 0.8"


class TestExecutorCollectContextVars:
    """Executor collect_context_vars 测试"""

    @pytest.fixture
    def mock_executor(self):
        """创建 mock executor"""
        from hft.executor.market_executor import MarketExecutor

        config = MarketExecutorConfig(per_order_usd=100)
        executor = MarketExecutor(config)

        # Mock root 以避免依赖
        executor._root = MagicMock()
        executor._root.indicator_group = None

        return executor

    def test_basic_context_vars(self, mock_executor):
        """测试基本上下文变量"""
        context = mock_executor.collect_context_vars(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            direction=1,
            speed=0.5,
            notional=1000.0,
        )

        assert context["direction"] == 1
        assert context["buy"] == True
        assert context["sell"] == False
        assert context["speed"] == 0.5
        assert context["notional"] == 1000.0

    def test_vars_dict_computation(self):
        """测试 vars dict 计算"""
        from hft.executor.market_executor import MarketExecutor

        config = MarketExecutorConfig(
            per_order_usd=100,
            vars={
                "ratio": "0.5",
                "target": "ratio * notional",
            },
        )
        executor = MarketExecutor(config)
        executor._root = MagicMock()
        executor._root.indicator_group = None

        context = executor.collect_context_vars(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            direction=1,
            speed=0.5,
            notional=1000.0,
        )

        assert context["ratio"] == 0.5
        assert context["target"] == 500.0  # 0.5 * 1000

    def test_vars_list_computation(self):
        """测试 vars list 计算（按顺序）"""
        from hft.executor.market_executor import MarketExecutor

        config = MarketExecutorConfig(
            per_order_usd=100,
            vars=[
                ExecutorVarDefinition(name="a", value="10"),
                ExecutorVarDefinition(name="b", value="a * 2"),  # 引用前面的 a
                ExecutorVarDefinition(name="c", value="b + a"),  # 引用前面的 a 和 b
            ],
        )
        executor = MarketExecutor(config)
        executor._root = MagicMock()
        executor._root.indicator_group = None

        context = executor.collect_context_vars(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            direction=1,
            speed=0.5,
            notional=1000.0,
        )

        assert context["a"] == 10
        assert context["b"] == 20  # 10 * 2
        assert context["c"] == 30  # 20 + 10

    def test_conditional_vars_triggered(self):
        """测试 conditional_vars 触发"""
        from hft.executor.market_executor import MarketExecutor

        config = MarketExecutorConfig(
            per_order_usd=100,
            conditional_vars={
                "signal": ExecutorConditionalVarDefinition(
                    value="1",
                    on="speed > 0.5",  # 条件满足
                    default=0,
                ),
            },
        )
        executor = MarketExecutor(config)
        executor._root = MagicMock()
        executor._root.indicator_group = None

        context = executor.collect_context_vars(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            direction=1,
            speed=0.8,  # > 0.5, 触发条件
            notional=1000.0,
        )

        assert context["signal"] == 1

    def test_conditional_vars_not_triggered(self):
        """测试 conditional_vars 未触发"""
        from hft.executor.market_executor import MarketExecutor

        config = MarketExecutorConfig(
            per_order_usd=100,
            conditional_vars={
                "signal": ExecutorConditionalVarDefinition(
                    value="1",
                    on="speed > 0.5",  # 条件不满足
                    default=0,
                ),
            },
        )
        executor = MarketExecutor(config)
        executor._root = MagicMock()
        executor._root.indicator_group = None

        context = executor.collect_context_vars(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            direction=1,
            speed=0.3,  # <= 0.5, 不触发
            notional=1000.0,
        )

        assert context["signal"] == 0  # 使用默认值

    def test_conditional_vars_state_persistence(self):
        """测试 conditional_vars 状态持久化"""
        from hft.executor.market_executor import MarketExecutor

        config = MarketExecutorConfig(
            per_order_usd=100,
            conditional_vars={
                "center_price": ExecutorConditionalVarDefinition(
                    value="notional",  # 使用 notional 作为值
                    on="speed > 0.5",
                    default=None,
                ),
            },
        )
        executor = MarketExecutor(config)
        executor._root = MagicMock()
        executor._root.indicator_group = None

        # 第一次调用，条件满足，更新值
        context1 = executor.collect_context_vars(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            direction=1,
            speed=0.8,  # 触发
            notional=1000.0,
        )
        assert context1["center_price"] == 1000.0

        # 第二次调用，条件不满足，保持上次值
        context2 = executor.collect_context_vars(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            direction=1,
            speed=0.3,  # 不触发
            notional=2000.0,  # 不同的 notional
        )
        # 应该保持上次的值 1000.0，而不是使用 default 或新值
        assert context2["center_price"] == 1000.0

    def test_conditional_vars_duration(self):
        """测试 conditional_vars 中的 duration 变量"""
        from hft.executor.market_executor import MarketExecutor

        config = MarketExecutorConfig(
            per_order_usd=100,
            conditional_vars={
                "reset_signal": ExecutorConditionalVarDefinition(
                    value="1",
                    on="duration > 1",  # 使用 duration
                    default=0,
                ),
            },
        )
        executor = MarketExecutor(config)
        executor._root = MagicMock()
        executor._root.indicator_group = None

        # 第一次调用，duration = inf（从未更新过）
        context1 = executor.collect_context_vars(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            direction=1,
            speed=0.5,
            notional=1000.0,
        )
        # duration > 1 为 True（inf > 1）
        assert context1["reset_signal"] == 1

        # 立即第二次调用，duration < 1
        context2 = executor.collect_context_vars(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            direction=1,
            speed=0.5,
            notional=1000.0,
        )
        # duration < 1，条件不满足，保持上次值
        assert context2["reset_signal"] == 1

    def test_strategies_namespace(self):
        """测试 strategies namespace"""
        from hft.executor.market_executor import MarketExecutor

        config = MarketExecutorConfig(
            per_order_usd=100,
            vars={
                "total_position": "sum(strategies['position_usd'])",
            },
        )
        executor = MarketExecutor(config)
        executor._root = MagicMock()
        executor._root.indicator_group = None

        strategies_data = {
            "position_usd": [100.0, 200.0, 300.0],
        }

        context = executor.collect_context_vars(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            direction=1,
            speed=0.5,
            notional=1000.0,
            strategies_data=strategies_data,
        )

        assert context["strategies"] == strategies_data
        assert context["total_position"] == 600.0  # sum([100, 200, 300])

    def test_vars_and_conditional_vars_combined(self):
        """测试 vars 和 conditional_vars 组合使用"""
        from hft.executor.market_executor import MarketExecutor

        config = MarketExecutorConfig(
            per_order_usd=100,
            vars=[
                ExecutorVarDefinition(name="threshold", value="0.5"),
            ],
            conditional_vars={
                "signal": ExecutorConditionalVarDefinition(
                    value="direction",
                    on="speed > threshold",  # 引用 vars 中的 threshold
                    default=0,
                ),
            },
        )
        executor = MarketExecutor(config)
        executor._root = MagicMock()
        executor._root.indicator_group = None

        # speed > threshold, 触发
        context1 = executor.collect_context_vars(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            direction=1,
            speed=0.8,
            notional=1000.0,
        )
        assert context1["threshold"] == 0.5
        assert context1["signal"] == 1

        # speed <= threshold, 不触发
        context2 = executor.collect_context_vars(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            direction=-1,  # 改变 direction
            speed=0.3,
            notional=1000.0,
        )
        assert context2["signal"] == 1  # 保持上次值

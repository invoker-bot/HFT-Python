"""
Unit tests for SmartExecutor Phase 2: 简单条件路由

Tests cover:
- 基于 config.routes 的规则匹配（speed 条件）
- 路由优先级：显式路由 > 规则匹配 > 默认
- executor=None 的不执行模式（取消现有订单）
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from hft.executor.base import ExecutionResult
from hft.executor.smart_executor.executor import SmartExecutor
from hft.executor.smart_executor.config import SmartExecutorConfig, RouteConfig


# ============================================================
# Mock classes (复用 Phase 1 的 Mock)
# ============================================================

class MockChildExecutor:
    """Mock 子执行器"""

    def __init__(self, name: str):
        self.name = name
        self.logger = MagicMock()
        self.lazy_start = False
        self.enabled = True
        self._active_orders = {}
        self.execute_delta_result = None
        self.cancel_orders_for_symbol = AsyncMock(return_value=0)
        self._execute_delta_mock = AsyncMock()

    @property
    def per_order_usd(self) -> float:
        return 100.0

    async def execute_delta(self, exchange, symbol, delta_usd, speed, current_price):
        """Mock execute_delta"""
        await self._execute_delta_mock(exchange, symbol, delta_usd, speed, current_price)

        if self.execute_delta_result:
            return self.execute_delta_result
        return ExecutionResult(
            exchange_class=exchange.class_name,
            symbol=symbol,
            success=True,
            exchange_name=exchange.name,
            delta_usd=delta_usd,
        )


class MockExchange:
    """Mock 交易所"""

    def __init__(self, name: str = "binance"):
        self.name = name
        self.class_name = "Binance"
        self.config = MagicMock()
        self.config.executor_map = {}
        self.config.swap_taker_fee = 0.0005


# ============================================================
# Tests
# ============================================================

@pytest.mark.asyncio
async def test_route_matching_with_speed_condition():
    """测试基于 speed 条件的规则匹配"""
    # 准备
    config = SmartExecutorConfig(
        default_executor="limit",
        children={"market": "market/default", "limit": "limit/default"},
        routes=[
            RouteConfig(condition="speed > 0.9", executor="market", priority=1),
            RouteConfig(condition=None, executor="limit", priority=999),
        ],
    )
    smart = SmartExecutor(config)
    smart.logger = MagicMock()

    # Mock 子执行器
    market_executor = MockChildExecutor("market")
    limit_executor = MockChildExecutor("limit")
    smart._child_executors = {
        "market": market_executor,
        "limit": limit_executor,
    }

    # Mock exchange
    exchange = MockExchange()

    # 测试：speed=0.95 应该选择 market
    result = await smart.execute_delta(
        exchange=exchange,
        symbol="BTC/USDT",
        delta_usd=1000.0,
        speed=0.95,  # 触发 speed > 0.9
        current_price=50000.0,
    )

    # 验证
    assert result.success is True
    assert market_executor._execute_delta_mock.called  # market 被调用
    assert not limit_executor._execute_delta_mock.called  # limit 未被调用


@pytest.mark.asyncio
async def test_route_priority_explicit_over_rules():
    """测试路由优先级：显式路由 > 规则匹配"""
    # 准备
    config = SmartExecutorConfig(
        default_executor="limit",
        children={"market": "market/default", "limit": "limit/default"},
        routes=[
            RouteConfig(condition="speed > 0.9", executor="market", priority=1),
        ],
    )
    smart = SmartExecutor(config)
    smart.logger = MagicMock()

    # Mock 子执行器
    market_executor = MockChildExecutor("market")
    limit_executor = MockChildExecutor("limit")
    smart._child_executors = {
        "market": market_executor,
        "limit": limit_executor,
    }

    # Mock exchange with explicit routing
    exchange = MockExchange()
    exchange.config.executor_map = {"BTC/USDT": "limit"}  # 显式指定 limit

    # 测试：即使 speed=0.95 触发规则，显式路由应该优先
    result = await smart.execute_delta(
        exchange=exchange,
        symbol="BTC/USDT",
        delta_usd=1000.0,
        speed=0.95,  # 触发 speed > 0.9，但被显式路由覆盖
        current_price=50000.0,
    )

    # 验证
    assert result.success is True
    assert limit_executor._execute_delta_mock.called  # limit 被调用（显式路由）
    assert not market_executor._execute_delta_mock.called  # market 未被调用


@pytest.mark.asyncio
async def test_route_priority_rules_over_default():
    """测试路由优先级：规则匹配 > 默认回退"""
    # 准备
    config = SmartExecutorConfig(
        default_executor="limit",
        children={"market": "market/default", "limit": "limit/default"},
        routes=[
            RouteConfig(condition="speed > 0.9", executor="market", priority=1),
            # 没有默认规则（condition=None）
        ],
        speed_threshold=1.5,  # 设置高阈值，禁用向后兼容的速度阈值
    )
    smart = SmartExecutor(config)
    smart.logger = MagicMock()

    # Mock 子执行器
    market_executor = MockChildExecutor("market")
    limit_executor = MockChildExecutor("limit")
    smart._child_executors = {
        "market": market_executor,
        "limit": limit_executor,
    }

    # Mock exchange
    exchange = MockExchange()

    # 测试：speed=0.5 不触发规则，应该使用 default_executor
    result = await smart.execute_delta(
        exchange=exchange,
        symbol="BTC/USDT",
        delta_usd=1000.0,
        speed=0.5,  # 不触发 speed > 0.9
        current_price=50000.0,
    )

    # 验证
    assert result.success is True
    assert limit_executor._execute_delta_mock.called  # limit 被调用（default）
    assert not market_executor._execute_delta_mock.called  # market 未被调用


@pytest.mark.asyncio
async def test_executor_none_cancels_existing_orders():
    """测试 executor=None 取消现有订单"""
    # 准备
    config = SmartExecutorConfig(
        default_executor="limit",
        children={"market": "market/default", "limit": "limit/default"},
        routes=[
            RouteConfig(condition="speed < 0.1", executor=None, priority=1),  # 低速不执行
            RouteConfig(condition=None, executor="limit", priority=999),
        ],
    )
    smart = SmartExecutor(config)
    smart.logger = MagicMock()

    # Mock 子执行器
    market_executor = MockChildExecutor("market")
    limit_executor = MockChildExecutor("limit")
    smart._child_executors = {
        "market": market_executor,
        "limit": limit_executor,
    }

    # Mock exchange
    exchange = MockExchange()

    # 先建立一个有订单的状态
    await smart._track_order(exchange.name, "BTC/USDT", "limit", [])

    # 测试：speed=0.05 触发 executor=None
    result = await smart.execute_delta(
        exchange=exchange,
        symbol="BTC/USDT",
        delta_usd=1000.0,
        speed=0.05,  # 触发 speed < 0.1
        current_price=50000.0,
    )

    # 验证
    assert result.success is True
    assert result.delta_usd == 0.0  # 没有实际执行
    assert limit_executor.cancel_orders_for_symbol.called  # 旧订单被取消
    assert await smart._get_tracked_executor(exchange.name, "BTC/USDT") is None  # 追踪已清理


@pytest.mark.asyncio
async def test_route_default_rule_no_condition():
    """测试默认规则（condition=None）"""
    # 准备
    config = SmartExecutorConfig(
        default_executor="market",  # default_executor 作为最终回退
        children={"market": "market/default", "limit": "limit/default"},
        routes=[
            RouteConfig(condition="speed > 0.9", executor="market", priority=1),
            RouteConfig(condition=None, executor="limit", priority=999),  # 默认规则
        ],
        speed_threshold=1.5,  # 设置高阈值
    )
    smart = SmartExecutor(config)
    smart.logger = MagicMock()

    # Mock 子执行器
    market_executor = MockChildExecutor("market")
    limit_executor = MockChildExecutor("limit")
    smart._child_executors = {
        "market": market_executor,
        "limit": limit_executor,
    }

    # Mock exchange
    exchange = MockExchange()

    # 测试：speed=0.5 不触发第一条规则，应该匹配默认规则
    result = await smart.execute_delta(
        exchange=exchange,
        symbol="BTC/USDT",
        delta_usd=1000.0,
        speed=0.5,  # 不触发 speed > 0.9
        current_price=50000.0,
    )

    # 验证
    assert result.success is True
    assert limit_executor._execute_delta_mock.called  # limit 被调用（默认规则）
    assert not market_executor._execute_delta_mock.called


@pytest.mark.asyncio
async def test_route_priority_sorting():
    """测试规则按 priority 排序"""
    # 准备
    config = SmartExecutorConfig(
        default_executor="limit",
        children={"market": "market/default", "limit": "limit/default"},
        routes=[
            RouteConfig(condition="speed > 0.5", executor="limit", priority=10),  # 低优先级
            RouteConfig(condition="speed > 0.8", executor="market", priority=1),  # 高优先级
        ],
        speed_threshold=1.5,
    )
    smart = SmartExecutor(config)
    smart.logger = MagicMock()

    # Mock 子执行器
    market_executor = MockChildExecutor("market")
    limit_executor = MockChildExecutor("limit")
    smart._child_executors = {
        "market": market_executor,
        "limit": limit_executor,
    }

    # Mock exchange
    exchange = MockExchange()

    # 测试：speed=0.85 两条规则都匹配，应该选择 priority 更高（数字更小）的
    result = await smart.execute_delta(
        exchange=exchange,
        symbol="BTC/USDT",
        delta_usd=1000.0,
        speed=0.85,  # 触发两条规则
        current_price=50000.0,
    )

    # 验证
    assert result.success is True
    assert market_executor._execute_delta_mock.called  # market 被调用（priority=1）
    assert not limit_executor._execute_delta_mock.called


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

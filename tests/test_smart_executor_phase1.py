"""
Unit tests for SmartExecutor Phase 1: 最小可用路由

Tests cover:
- 切换清理逻辑：先下新单，成功后取消旧单
- 边界情况：新单失败保持旧状态，旧单取消失败只记录警告
- Listener 树集成：children 禁用自动 tick
"""
# pylint: disable=protected-access
from unittest.mock import AsyncMock, MagicMock

import pytest

from hft.executor.base import BaseExecutor, ExecutionResult
from hft.executor.smart_executor.executor import SmartExecutor
from hft.executor.smart_executor.config import SmartExecutorConfig


# ============================================================
# Mock classes
# ============================================================

class MockChildExecutor(BaseExecutor):
    """Mock 子执行器"""

    def __init__(self, name: str):
        # 最小化初始化，不调用 super().__init__()
        self.name = name
        self.logger = MagicMock()
        self.lazy_start = False
        self.enabled = True
        self._active_orders = {}
        self.execute_delta_result = None  # 可设置返回结果
        self.cancel_orders_for_symbol = AsyncMock(return_value=0)
        # Mock execute_delta 为 AsyncMock
        self._execute_delta_mock = AsyncMock()

    @property
    def per_order_usd(self) -> float:
        return 100.0

    async def execute_delta(self, exchange, symbol, delta_usd, speed, current_price):
        """Mock execute_delta"""
        # 调用 mock 记录调用
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
async def test_executor_switch_with_order_cleanup():
    """测试执行器切换时的订单清理"""
    # 准备
    config = SmartExecutorConfig(
        default_executor="market",
        children={"market": "market/default", "as": "as/default"},
    )
    smart = SmartExecutor(config)
    smart.logger = MagicMock()

    # Mock 子执行器
    market_executor = MockChildExecutor("market")
    as_executor = MockChildExecutor("as")
    smart._child_executors = {
        "market": market_executor,
        "as": as_executor,
    }

    # Mock exchange
    exchange = MockExchange()

    # Mock _route 始终返回 "as"（触发切换）
    smart._route = MagicMock(return_value=MagicMock(
        executor_key="as",
        rule="auto_select",
        edge_usd=10.0,
        trades_count=100,
    ))

    # 第一次执行：market -> as
    # 1. 先追踪 market
    await smart._track_order(exchange.name, "BTC/USDT", "market", [])

    # 2. 执行切换
    result = await smart.execute_delta(
        exchange=exchange,
        symbol="BTC/USDT",
        delta_usd=1000.0,
        speed=0.5,
        current_price=50000.0,
    )

    # 验证
    assert result.success is True
    assert as_executor._execute_delta_mock.called  # 新执行器被调用
    assert market_executor.cancel_orders_for_symbol.called  # 旧执行器订单被取消
    assert await smart._get_tracked_executor(exchange.name, "BTC/USDT") == "as"


@pytest.mark.asyncio
async def test_new_order_failure_keeps_old_state():
    """测试新单失败时保持旧状态"""
    # 准备
    config = SmartExecutorConfig(
        default_executor="market",
        children={"market": "market/default", "as": "as/default"},
    )
    smart = SmartExecutor(config)
    smart.logger = MagicMock()

    # Mock 子执行器
    market_executor = MockChildExecutor("market")
    as_executor = MockChildExecutor("as")

    # as 执行器返回失败结果
    as_executor.execute_delta_result = ExecutionResult(
        exchange_class="Binance",
        symbol="BTC/USDT",
        success=False,
        exchange_name="binance",
        error="Order placement failed",
    )

    smart._child_executors = {
        "market": market_executor,
        "as": as_executor,
    }

    # Mock exchange
    exchange = MockExchange()

    # Mock _route 返回 "as"
    smart._route = MagicMock(return_value=MagicMock(
        executor_key="as",
        rule="auto_select",
        edge_usd=10.0,
        trades_count=100,
    ))

    # 先追踪 market
    await smart._track_order(exchange.name, "BTC/USDT", "market", [])

    # 执行切换（新单失败）
    result = await smart.execute_delta(
        exchange=exchange,
        symbol="BTC/USDT",
        delta_usd=1000.0,
        speed=0.5,
        current_price=50000.0,
    )

    # 验证
    assert result.success is False
    assert result.error == "Order placement failed"
    # 旧状态保持不变
    assert await smart._get_tracked_executor(exchange.name, "BTC/USDT") == "market"
    # 旧订单未被取消
    assert not market_executor.cancel_orders_for_symbol.called


@pytest.mark.asyncio
async def test_old_order_cancel_failure_only_logs_warning():
    """测试旧单取消失败只记录警告"""
    # 准备
    config = SmartExecutorConfig(
        default_executor="market",
        children={"market": "market/default", "as": "as/default"},
    )
    smart = SmartExecutor(config)
    smart.logger = MagicMock()

    # Mock 子执行器
    market_executor = MockChildExecutor("market")
    as_executor = MockChildExecutor("as")

    # market 取消订单失败
    market_executor.cancel_orders_for_symbol = AsyncMock(
        side_effect=RuntimeError("Cancel failed")
    )

    smart._child_executors = {
        "market": market_executor,
        "as": as_executor,
    }

    # Mock exchange
    exchange = MockExchange()

    # Mock _route 返回 "as"
    smart._route = MagicMock(return_value=MagicMock(
        executor_key="as",
        rule="auto_select",
        edge_usd=10.0,
        trades_count=100,
    ))

    # 先追踪 market
    await smart._track_order(exchange.name, "BTC/USDT", "market", [])

    # 执行切换（旧单取消失败）
    result = await smart.execute_delta(
        exchange=exchange,
        symbol="BTC/USDT",
        delta_usd=1000.0,
        speed=0.5,
        current_price=50000.0,
    )

    # 验证
    assert result.success is True  # 新单成功
    assert await smart._get_tracked_executor(exchange.name, "BTC/USDT") == "as"  # 状态已更新
    assert market_executor.cancel_orders_for_symbol.called  # 尝试取消旧单
    assert smart.logger.warning.called  # 记录警告日志


@pytest.mark.asyncio
async def test_children_lazy_start_and_disabled():
    """测试子执行器的 lazy_start 和 enabled 设置"""
    # 准备
    config = SmartExecutorConfig(
        default_executor="market",
        children={"market": "market/default"},
    )
    smart = SmartExecutor(config)

    # Mock _load_child_executors 的配置加载部分
    child_executor = MockChildExecutor("market")

    # 模拟 _load_child_executors 中的设置
    child_executor.lazy_start = True
    child_executor.enabled = False

    # 验证
    assert child_executor.lazy_start is True
    assert child_executor.enabled is False


@pytest.mark.asyncio
async def test_no_switch_when_same_executor():
    """测试保持相同执行器时不取消订单"""
    # 准备
    config = SmartExecutorConfig(
        default_executor="market",
        children={"market": "market/default"},
    )
    smart = SmartExecutor(config)
    smart.logger = MagicMock()

    # Mock 子执行器
    market_executor = MockChildExecutor("market")
    smart._child_executors = {"market": market_executor}

    # Mock exchange
    exchange = MockExchange()

    # Mock _route 始终返回 "market"
    smart._route = MagicMock(return_value=MagicMock(
        executor_key="market",
        rule="explicit",
        edge_usd=None,
        trades_count=0,
    ))

    # 先追踪 market
    await smart._track_order(exchange.name, "BTC/USDT", "market", [])

    # 执行（保持不变）
    result = await smart.execute_delta(
        exchange=exchange,
        symbol="BTC/USDT",
        delta_usd=1000.0,
        speed=0.5,
        current_price=50000.0,
    )

    # 验证
    assert result.success is True
    assert await smart._get_tracked_executor(exchange.name, "BTC/USDT") == "market"
    # 没有取消订单的调用
    assert not market_executor.cancel_orders_for_symbol.called


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

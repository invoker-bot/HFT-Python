"""
Unit tests for SmartExecutor Phase 3: 高级条件扩展

Tests cover:
- trades/edge/notional 变量在路由条件中的使用
- 复杂条件表达式（如 len(trades) > 50 and notional > 10000）
- 缓存机制验证（同一 tick 使用缓存，新 tick 重新计算）
- trades 数据缺失时的 fail-safe 行为
- edge 和 notional 计算正确性
"""
# pylint: disable=protected-access
from dataclasses import dataclass
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hft.executor.base import ExecutionResult
from hft.executor.smart_executor.executor import SmartExecutor
from hft.executor.smart_executor.config import SmartExecutorConfig, RouteConfig


# ============================================================
# Mock classes
# ============================================================

@dataclass
class MockTradeData:
    """Mock TradeData"""
    id: str
    symbol: str
    timestamp: int
    side: str  # 'buy' or 'sell'
    price: float
    amount: float
    cost: float  # price * amount


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


def create_mock_trades(count: int, side: str = "buy", price: float = 50000.0, amount: float = 0.1) -> list:
    """创建 mock trades 数据"""
    now = int(time.time() * 1000)
    trades = []
    for i in range(count):
        trades.append(MockTradeData(
            id=str(i),
            symbol="BTC/USDT",
            timestamp=now - i * 100,  # 每个 trade 间隔 100ms
            side=side,
            price=price,
            amount=amount,
            cost=price * amount,
        ))
    return trades


# ============================================================
# Tests
# ============================================================

@pytest.mark.asyncio
async def test_route_matching_with_trades_condition():
    """测试基于 trades 数量的条件路由"""
    # 准备
    config = SmartExecutorConfig(
        default_executor="limit",
        children={"market": "market/default", "limit": "limit/default"},
        routes=[
            RouteConfig(condition="len(trades) > 50", executor="market", priority=1),
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

    # Mock trades 数据（60 个 trades）
    mock_trades = create_mock_trades(60, side="buy")

    with patch.object(smart, '_get_recent_trades', return_value=mock_trades):
        # 测试：len(trades)=60 > 50，应该选择 market
        result = await smart.execute_delta(
            exchange=exchange,
            symbol="BTC/USDT",
            delta_usd=1000.0,
            speed=0.5,
            current_price=50000.0,
        )

    # 验证
    assert result.success is True
    assert market_executor._execute_delta_mock.called  # market 被调用
    assert not limit_executor._execute_delta_mock.called  # limit 未被调用


@pytest.mark.asyncio
async def test_route_matching_with_notional_condition():
    """测试基于 notional（成交额）的条件路由"""
    # 准备
    config = SmartExecutorConfig(
        default_executor="limit",
        children={"market": "market/default", "limit": "limit/default"},
        routes=[
            RouteConfig(condition="notional > 10000", executor="market", priority=1),
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

    # Mock trades 数据：10 个 buy trades，每个 cost=5000，总 notional=50000
    mock_trades = create_mock_trades(10, side="buy", price=50000.0, amount=0.1)  # cost=5000 each

    with patch.object(smart, '_get_recent_trades', return_value=mock_trades):
        # 测试：notional=50000 > 10000，应该选择 market
        result = await smart.execute_delta(
            exchange=exchange,
            symbol="BTC/USDT",
            delta_usd=1000.0,  # 正数=买入，计算 buy side notional
            speed=0.5,
            current_price=50000.0,
        )

    # 验证
    assert result.success is True
    assert market_executor._execute_delta_mock.called
    assert not limit_executor._execute_delta_mock.called


@pytest.mark.asyncio
async def test_route_matching_with_edge_condition():
    """测试基于 edge（taker 优势，相对值）的条件路由"""
    # 准备
    config = SmartExecutorConfig(
        default_executor="limit",
        children={"market": "market/default", "limit": "limit/default"},
        routes=[
            # edge 现在是相对值（比例），如 0.01 表示 1%
            RouteConfig(condition="edge > 0.01", executor="market", priority=1),
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
    exchange.config.swap_taker_fee = 0.0001  # 0.01% 手续费

    # Mock trades：buy at 49000, current price 50000 -> positive edge
    mock_trades = [
        MockTradeData(
            id="1",
            symbol="BTC/USDT",
            timestamp=int(time.time() * 1000),
            side="buy",
            price=49000.0,
            amount=1.0,
            cost=49000.0,
        )
    ]

    with patch.object(smart, '_get_recent_trades', return_value=mock_trades):
        # 新公式：edge = (50000 - 49000) / 50000 - 0.0001 = 0.02 - 0.0001 = 0.0199
        result = await smart.execute_delta(
            exchange=exchange,
            symbol="BTC/USDT",
            delta_usd=1000.0,  # 正数=买入
            speed=0.5,
            current_price=50000.0,
        )

    # 验证
    assert result.success is True
    assert market_executor._execute_delta_mock.called  # edge ≈ 0.0199 > 0.01，选择 market
    assert not limit_executor._execute_delta_mock.called


@pytest.mark.asyncio
async def test_route_matching_with_complex_condition():
    """测试复杂条件表达式（多条件组合）"""
    # 准备
    config = SmartExecutorConfig(
        default_executor="limit",
        children={"market": "market/default", "limit": "limit/default"},
        routes=[
            # 复杂条件：trades 数量 > 30 且 notional > 5000 且 speed > 0.5
            RouteConfig(
                condition="len(trades) > 30 and notional > 5000 and speed > 0.5",
                executor="market",
                priority=1
            ),
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

    # Mock trades：40 个 buy trades，每个 cost=500，总 notional=20000
    mock_trades = create_mock_trades(40, side="buy", price=5000.0, amount=0.1)  # cost=500 each

    with patch.object(smart, '_get_recent_trades', return_value=mock_trades):
        # 测试：len(trades)=40 > 30 ✓, notional=20000 > 5000 ✓, speed=0.7 > 0.5 ✓
        result = await smart.execute_delta(
            exchange=exchange,
            symbol="BTC/USDT",
            delta_usd=1000.0,
            speed=0.7,
            current_price=5000.0,
        )

    # 验证
    assert result.success is True
    assert market_executor._execute_delta_mock.called
    assert not limit_executor._execute_delta_mock.called


@pytest.mark.asyncio
async def test_route_matching_complex_condition_partial_fail():
    """测试复杂条件部分不满足时回退到默认"""
    # 准备
    config = SmartExecutorConfig(
        default_executor="limit",
        children={"market": "market/default", "limit": "limit/default"},
        routes=[
            RouteConfig(
                condition="len(trades) > 30 and notional > 5000 and speed > 0.8",
                executor="market",
                priority=1
            ),
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

    # Mock trades：40 个 buy trades
    mock_trades = create_mock_trades(40, side="buy", price=5000.0, amount=0.1)

    with patch.object(smart, '_get_recent_trades', return_value=mock_trades):
        # 测试：len(trades)=40 > 30 ✓, notional=20000 > 5000 ✓, speed=0.5 < 0.8 ✗
        result = await smart.execute_delta(
            exchange=exchange,
            symbol="BTC/USDT",
            delta_usd=1000.0,
            speed=0.5,  # 不满足 speed > 0.8
            current_price=5000.0,
        )

    # 验证：条件不满足，使用默认规则 limit
    assert result.success is True
    assert limit_executor._execute_delta_mock.called
    assert not market_executor._execute_delta_mock.called


@pytest.mark.asyncio
async def test_failsafe_when_trades_missing():
    """测试 trades 数据缺失时的 fail-safe 行为"""
    # 准备
    config = SmartExecutorConfig(
        default_executor="limit",
        children={"market": "market/default", "limit": "limit/default"},
        routes=[
            # 依赖 trades 数据的条件
            RouteConfig(condition="len(trades) > 10", executor="market", priority=1),
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

    # Mock trades 返回空列表（数据缺失）
    with patch.object(smart, '_get_recent_trades', return_value=[]):
        result = await smart.execute_delta(
            exchange=exchange,
            symbol="BTC/USDT",
            delta_usd=1000.0,
            speed=0.5,
            current_price=50000.0,
        )

    # 验证：trades=[], len(trades)=0 不满足 > 10，使用默认规则 limit
    assert result.success is True
    assert limit_executor._execute_delta_mock.called
    assert not market_executor._execute_delta_mock.called


@pytest.mark.asyncio
async def test_failsafe_edge_and_notional_default_values():
    """测试 trades 缺失时 edge 和 notional 使用默认值 0"""
    # 准备
    config = SmartExecutorConfig(
        default_executor="limit",
        children={"market": "market/default", "limit": "limit/default"},
        routes=[
            # 这些条件在 trades 缺失时都应该 fail-safe
            RouteConfig(condition="edge > 0", executor="market", priority=1),
            RouteConfig(condition="notional > 0", executor="market", priority=2),
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

    # Mock trades 返回空列表
    with patch.object(smart, '_get_recent_trades', return_value=[]):
        result = await smart.execute_delta(
            exchange=exchange,
            symbol="BTC/USDT",
            delta_usd=1000.0,
            speed=0.5,
            current_price=50000.0,
        )

    # 验证：edge=0, notional=0，都不满足 > 0，使用默认规则 limit
    assert result.success is True
    assert limit_executor._execute_delta_mock.called
    assert not market_executor._execute_delta_mock.called


@pytest.mark.asyncio
async def test_cache_mechanism_same_tick():
    """测试缓存机制：同一 tick 周期内使用缓存"""
    # 准备
    config = SmartExecutorConfig(
        default_executor="limit",
        children={"market": "market/default", "limit": "limit/default"},
        routes=[
            RouteConfig(condition="len(trades) > 10", executor="market", priority=1),
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

    # 使用 Mock 追踪调用次数
    mock_trades = create_mock_trades(20, side="buy")
    get_trades_mock = MagicMock(return_value=mock_trades)

    with patch.object(smart, '_get_recent_trades', get_trades_mock):
        # 第一次调用
        await smart.execute_delta(
            exchange=exchange,
            symbol="BTC/USDT",
            delta_usd=1000.0,
            speed=0.5,
            current_price=50000.0,
        )

        # 第二次调用（同一 tick，应该使用缓存）
        await smart.execute_delta(
            exchange=exchange,
            symbol="BTC/USDT",
            delta_usd=1000.0,
            speed=0.5,
            current_price=50000.0,
        )

    # 验证：_get_recent_trades 只被调用一次（第二次使用缓存）
    assert get_trades_mock.call_count == 1


@pytest.mark.asyncio
async def test_cache_mechanism_new_tick():
    """测试缓存机制：新 tick 周期重新计算"""
    # 准备
    config = SmartExecutorConfig(
        default_executor="limit",
        children={"market": "market/default", "limit": "limit/default"},
        routes=[
            RouteConfig(condition="len(trades) > 10", executor="market", priority=1),
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

    mock_trades = create_mock_trades(20, side="buy")
    get_trades_mock = MagicMock(return_value=mock_trades)

    with patch.object(smart, '_get_recent_trades', get_trades_mock):
        # 第一次调用
        await smart.execute_delta(
            exchange=exchange,
            symbol="BTC/USDT",
            delta_usd=1000.0,
            speed=0.5,
            current_price=50000.0,
        )

        # 模拟该 symbol 的缓存时间过去 1.5 秒（超过缓存有效期 1 秒）
        smart._route_context_cache[(exchange.name, "BTC/USDT")]["timestamp"] -= 1.5

        # 第二次调用（新 tick，应该重新计算）
        await smart.execute_delta(
            exchange=exchange,
            symbol="BTC/USDT",
            delta_usd=1000.0,
            speed=0.5,
            current_price=50000.0,
        )

    # 验证：_get_recent_trades 被调用两次（缓存过期后重新计算）
    assert get_trades_mock.call_count == 2


@pytest.mark.asyncio
async def test_cache_is_per_symbol_not_extended_by_other_symbol():
    """测试缓存按 symbol 独立过期，不会被其他 symbol 的更新延长。"""
    config = SmartExecutorConfig(
        default_executor="limit",
        children={"market": "market/default", "limit": "limit/default"},
        routes=[
            RouteConfig(condition="len(trades) > 10", executor="market", priority=1),
            RouteConfig(condition=None, executor="limit", priority=999),
        ],
    )
    smart = SmartExecutor(config)
    smart.logger = MagicMock()

    market_executor = MockChildExecutor("market")
    limit_executor = MockChildExecutor("limit")
    smart._child_executors = {"market": market_executor, "limit": limit_executor}

    exchange = MockExchange()

    # A 与 B 使用不同的 trades 数据，便于观察是否被重新计算
    trades_a_v1 = create_mock_trades(20, side="buy")
    trades_a_v2 = create_mock_trades(5, side="buy")   # 第二次应重新计算为较少 trades
    trades_b = create_mock_trades(20, side="buy")

    def _get_trades_side_effect(_exchange, symbol: str):
        if symbol == "A":
            # 第一次返回 v1，之后返回 v2
            if not hasattr(_get_trades_side_effect, "_a_called"):
                _get_trades_side_effect._a_called = True
                return trades_a_v1
            return trades_a_v2
        if symbol == "B":
            return trades_b
        return []

    get_trades_mock = MagicMock(side_effect=_get_trades_side_effect)

    with patch.object(smart, "_get_recent_trades", get_trades_mock):
        # 1) 计算 A，满足规则 -> market
        await smart.execute_delta(
            exchange=exchange,
            symbol="A",
            delta_usd=1000.0,
            speed=0.5,
            current_price=50000.0,
        )

        # 2) 计算 B，写入 B 的缓存（不应影响 A 的过期判断）
        await smart.execute_delta(
            exchange=exchange,
            symbol="B",
            delta_usd=1000.0,
            speed=0.5,
            current_price=50000.0,
        )

        # 3) 让 A 的缓存单独过期
        smart._route_context_cache[(exchange.name, "A")]["timestamp"] -= 1.5

        # 4) 再次执行 A，应重新拉 trades（使用 v2，len=5，不满足规则 -> 默认 limit）
        await smart.execute_delta(
            exchange=exchange,
            symbol="A",
            delta_usd=1000.0,
            speed=0.5,
            current_price=50000.0,
        )

    # A 至少被取 trades 两次（第一次 v1 + 过期后 v2）
    a_calls = [c for c in get_trades_mock.call_args_list if c.args[1] == "A"]
    assert len(a_calls) == 2
    assert limit_executor._execute_delta_mock.called


@pytest.mark.asyncio
async def test_notional_calculation_buy_side():
    """测试 notional 计算：买入方向只计算 buy side"""
    # 准备
    config = SmartExecutorConfig(
        default_executor="limit",
        children={"market": "market/default", "limit": "limit/default"},
        routes=[
            RouteConfig(condition="notional > 1000", executor="market", priority=1),
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

    # Mock trades：buy trades 总 cost=500，sell trades 总 cost=10000
    mock_trades = [
        MockTradeData(id="1", symbol="BTC/USDT", timestamp=int(time.time() * 1000),
                      side="buy", price=100.0, amount=5.0, cost=500.0),
        MockTradeData(id="2", symbol="BTC/USDT", timestamp=int(time.time() * 1000),
                      side="sell", price=100.0, amount=100.0, cost=10000.0),
    ]

    with patch.object(smart, '_get_recent_trades', return_value=mock_trades):
        # delta_usd > 0 表示买入，应该只计算 buy side notional=500
        result = await smart.execute_delta(
            exchange=exchange,
            symbol="BTC/USDT",
            delta_usd=1000.0,  # 正数=买入
            speed=0.5,
            current_price=100.0,
        )

    # 验证：buy notional=500 < 1000，使用默认规则 limit
    assert result.success is True
    assert limit_executor._execute_delta_mock.called
    assert not market_executor._execute_delta_mock.called


@pytest.mark.asyncio
async def test_notional_calculation_sell_side():
    """测试 notional 计算：卖出方向只计算 sell side"""
    # 准备
    config = SmartExecutorConfig(
        default_executor="limit",
        children={"market": "market/default", "limit": "limit/default"},
        routes=[
            RouteConfig(condition="notional > 1000", executor="market", priority=1),
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

    # Mock trades：buy trades 总 cost=500，sell trades 总 cost=2000
    mock_trades = [
        MockTradeData(id="1", symbol="BTC/USDT", timestamp=int(time.time() * 1000),
                      side="buy", price=100.0, amount=5.0, cost=500.0),
        MockTradeData(id="2", symbol="BTC/USDT", timestamp=int(time.time() * 1000),
                      side="sell", price=100.0, amount=20.0, cost=2000.0),
    ]

    with patch.object(smart, '_get_recent_trades', return_value=mock_trades):
        # delta_usd < 0 表示卖出，应该只计算 sell side notional=2000
        result = await smart.execute_delta(
            exchange=exchange,
            symbol="BTC/USDT",
            delta_usd=-1000.0,  # 负数=卖出
            speed=0.5,
            current_price=100.0,
        )

    # 验证：sell notional=2000 > 1000，使用 market
    assert result.success is True
    assert market_executor._execute_delta_mock.called
    assert not limit_executor._execute_delta_mock.called


@pytest.mark.asyncio
async def test_edge_calculation_accuracy():
    """测试 edge 计算准确性（相对值）"""
    # 准备
    config = SmartExecutorConfig(
        default_executor="limit",
        children={"market": "market/default", "limit": "limit/default"},
        routes=[
            # edge 是相对值，0.01 表示 1%
            RouteConfig(condition="edge > 0.01", executor="market", priority=1),
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
    exchange.config.swap_taker_fee = 0.001  # 0.1% 手续费

    # Mock trades：buy at 49000, current price 50000
    # 新公式：edge = (p_final - vwap_buy) / p_final - taker_fee
    # edge = (50000 - 49000) / 50000 - 0.001 = 0.02 - 0.001 = 0.019
    mock_trades = [
        MockTradeData(
            id="1",
            symbol="BTC/USDT",
            timestamp=int(time.time() * 1000),
            side="buy",
            price=49000.0,
            amount=1.0,
            cost=49000.0,
        )
    ]

    with patch.object(smart, '_get_recent_trades', return_value=mock_trades):
        result = await smart.execute_delta(
            exchange=exchange,
            symbol="BTC/USDT",
            delta_usd=1000.0,  # 买入
            speed=0.5,
            current_price=50000.0,
        )

    # 验证：edge ≈ 0.019 > 0.01，使用 market
    assert result.success is True
    assert market_executor._execute_delta_mock.called
    assert not limit_executor._execute_delta_mock.called


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

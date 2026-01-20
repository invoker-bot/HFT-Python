"""
Unit tests for SmartExecutor Phase 4: 测试与文档

Tests cover:
- 配置验证：执行器引用、条件表达式语法、变量名检查
- 表达式求值边界：除零、类型错误、未定义变量
- 多 symbol 并发执行
- 性能测试：缓存效果验证
"""
# pylint: disable=protected-access
import asyncio
import time
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hft.executor.base import ExecutionResult
from hft.executor.smart_executor.executor import SmartExecutor
from hft.executor.smart_executor.config import SmartExecutorConfig, RouteConfig


# ============================================================
# Mock classes (复用)
# ============================================================

@dataclass
class MockTradeData:
    """Mock TradeData"""
    id: str
    symbol: str
    timestamp: int
    side: str
    price: float
    amount: float
    cost: float


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
        self._call_count = 0

    @property
    def per_order_usd(self) -> float:
        return 100.0

    async def execute_delta(self, exchange, symbol, delta_usd, speed, current_price):
        """Mock execute_delta"""
        self._call_count += 1
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
            timestamp=now - i * 100,
            side=side,
            price=price,
            amount=amount,
            cost=price * amount,
        ))
    return trades


# ============================================================
# 配置验证测试
# ============================================================

class TestConfigValidation:
    """配置验证测试"""

    def test_validate_routes_invalid_executor_reference(self):
        """测试引用不存在的执行器时抛出错误"""
        config = SmartExecutorConfig(
            default_executor="limit",
            children={"market": "market/default", "limit": "limit/default"},
            routes=[
                RouteConfig(condition="speed > 0.9", executor="nonexistent", priority=1),
            ],
        )
        smart = SmartExecutor(config)
        smart.logger = MagicMock()

        # 只加载实际存在的执行器
        smart._child_executors = {
            "market": MockChildExecutor("market"),
            "limit": MockChildExecutor("limit"),
        }

        # 验证应该抛出 ValueError
        with pytest.raises(ValueError, match="not found in children"):
            smart._validate_routes()

    def test_validate_routes_invalid_condition_syntax(self):
        """测试条件表达式语法错误时抛出错误"""
        config = SmartExecutorConfig(
            default_executor="limit",
            children={"market": "market/default", "limit": "limit/default"},
            routes=[
                RouteConfig(condition="speed >", executor="market", priority=1),  # 语法错误
            ],
        )
        smart = SmartExecutor(config)
        smart.logger = MagicMock()

        smart._child_executors = {
            "market": MockChildExecutor("market"),
            "limit": MockChildExecutor("limit"),
        }

        with pytest.raises(ValueError, match="condition"):
            smart._validate_routes()

    def test_validate_routes_undefined_variable(self):
        """测试使用未定义变量时抛出错误"""
        config = SmartExecutorConfig(
            default_executor="limit",
            children={"market": "market/default", "limit": "limit/default"},
            routes=[
                RouteConfig(condition="undefined_var > 0.9", executor="market", priority=1),
            ],
        )
        smart = SmartExecutor(config)
        smart.logger = MagicMock()

        smart._child_executors = {
            "market": MockChildExecutor("market"),
            "limit": MockChildExecutor("limit"),
        }

        with pytest.raises(ValueError, match="Undefined variable"):
            smart._validate_routes()

    def test_validate_routes_default_executor_not_found(self):
        """测试 default_executor 不存在时抛出错误"""
        config = SmartExecutorConfig(
            default_executor="nonexistent",
            children={"market": "market/default", "limit": "limit/default"},
        )
        smart = SmartExecutor(config)
        smart.logger = MagicMock()

        smart._child_executors = {
            "market": MockChildExecutor("market"),
            "limit": MockChildExecutor("limit"),
        }

        with pytest.raises(ValueError, match="Default executor"):
            smart._validate_routes()

    def test_validate_routes_duplicate_priority_warning(self):
        """测试重复 priority 时记录警告"""
        config = SmartExecutorConfig(
            default_executor="limit",
            children={"market": "market/default", "limit": "limit/default"},
            routes=[
                RouteConfig(condition="speed > 0.9", executor="market", priority=1),
                RouteConfig(condition="speed > 0.8", executor="limit", priority=1),  # 重复
            ],
        )
        smart = SmartExecutor(config)
        smart.logger = MagicMock()

        smart._child_executors = {
            "market": MockChildExecutor("market"),
            "limit": MockChildExecutor("limit"),
        }

        # 不应抛出错误，但应记录警告
        smart._validate_routes()
        smart.logger.warning.assert_called()

    def test_validate_routes_no_default_rule_info(self):
        """测试没有默认规则时记录 info"""
        config = SmartExecutorConfig(
            default_executor="limit",
            children={"market": "market/default", "limit": "limit/default"},
            routes=[
                RouteConfig(condition="speed > 0.9", executor="market", priority=1),
                # 没有 condition=None 的默认规则
            ],
        )
        smart = SmartExecutor(config)
        smart.logger = MagicMock()

        smart._child_executors = {
            "market": MockChildExecutor("market"),
            "limit": MockChildExecutor("limit"),
        }

        smart._validate_routes()
        # 应记录 info（缺少默认规则）
        info_calls = [call for call in smart.logger.info.call_args_list
                      if "default" in str(call).lower() or "fallback" in str(call).lower()]
        assert len(info_calls) > 0


# ============================================================
# 表达式求值边界测试
# ============================================================

class TestExpressionEvaluation:
    """表达式求值边界测试"""

    def test_evaluate_condition_division_by_zero(self):
        """测试除零错误时返回 False"""
        config = SmartExecutorConfig(
            default_executor="limit",
            children={"market": "market/default", "limit": "limit/default"},
        )
        smart = SmartExecutor(config)
        smart.logger = MagicMock()

        # 除零应该返回 False，不抛出异常
        result = smart._evaluate_condition("1 / 0 > 0", {"speed": 0.5})
        assert result is False
        smart.logger.warning.assert_called()

    def test_evaluate_condition_type_error(self):
        """测试类型错误时返回 False"""
        config = SmartExecutorConfig(
            default_executor="limit",
            children={"market": "market/default", "limit": "limit/default"},
        )
        smart = SmartExecutor(config)
        smart.logger = MagicMock()

        # 类型错误应该返回 False
        result = smart._evaluate_condition("'string' > 0.5", {"speed": 0.5})
        assert result is False

    def test_evaluate_condition_undefined_variable_returns_false(self):
        """测试未定义变量时返回 False"""
        config = SmartExecutorConfig(
            default_executor="limit",
            children={"market": "market/default", "limit": "limit/default"},
        )
        smart = SmartExecutor(config)
        smart.logger = MagicMock()

        # 未定义变量应该返回 False 并记录错误
        result = smart._evaluate_condition("undefined > 0.5", {"speed": 0.5})
        assert result is False
        smart.logger.error.assert_called()

    def test_evaluate_condition_complex_math(self):
        """测试复杂数学表达式"""
        config = SmartExecutorConfig(
            default_executor="limit",
            children={"market": "market/default", "limit": "limit/default"},
        )
        smart = SmartExecutor(config)
        smart.logger = MagicMock()

        # 复杂数学表达式
        result = smart._evaluate_condition(
            "abs(edge) > 0.01 and min(speed, 1.0) > 0.5",
            {"speed": 0.8, "edge": -0.02}
        )
        assert result is True

    def test_evaluate_condition_len_function(self):
        """测试 len 函数"""
        config = SmartExecutorConfig(
            default_executor="limit",
            children={"market": "market/default", "limit": "limit/default"},
        )
        smart = SmartExecutor(config)
        smart.logger = MagicMock()

        trades = [1, 2, 3, 4, 5]
        result = smart._evaluate_condition("len(trades) > 3", {"trades": trades})
        assert result is True

        result = smart._evaluate_condition("len(trades) > 10", {"trades": trades})
        assert result is False

    def test_evaluate_condition_sum_function(self):
        """测试 sum 函数"""
        config = SmartExecutorConfig(
            default_executor="limit",
            children={"market": "market/default", "limit": "limit/default"},
        )
        smart = SmartExecutor(config)
        smart.logger = MagicMock()

        values = [1, 2, 3, 4, 5]
        result = smart._evaluate_condition("sum(values) > 10", {"values": values})
        assert result is True


# ============================================================
# 多 symbol 并发测试
# ============================================================

class TestConcurrency:
    """多 symbol 并发测试"""

    @pytest.mark.asyncio
    async def test_concurrent_multi_symbol_execution(self):
        """测试多 symbol 并发执行"""
        config = SmartExecutorConfig(
            default_executor="limit",
            children={"market": "market/default", "limit": "limit/default"},
            routes=[
                RouteConfig(condition="speed > 0.8", executor="market", priority=1),
                RouteConfig(condition=None, executor="limit", priority=999),
            ],
        )
        smart = SmartExecutor(config)
        smart.logger = MagicMock()

        market_executor = MockChildExecutor("market")
        limit_executor = MockChildExecutor("limit")
        smart._child_executors = {
            "market": market_executor,
            "limit": limit_executor,
        }

        exchange = MockExchange()

        # 并发执行 5 个不同的 symbol
        symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT", "XRP/USDT"]
        speeds = [0.9, 0.5, 0.85, 0.3, 0.95]  # 3 个选 market，2 个选 limit

        with patch.object(smart, '_get_recent_trades', return_value=[]):
            tasks = [
                smart.execute_delta(
                    exchange=exchange,
                    symbol=symbol,
                    delta_usd=1000.0,
                    speed=speed,
                    current_price=50000.0,
                )
                for symbol, speed in zip(symbols, speeds)
            ]

            results = await asyncio.gather(*tasks)

        # 验证所有结果都成功
        assert all(r.success for r in results)

        # 验证执行器被正确调用
        # speed > 0.8: BTC(0.9), SOL(0.85), XRP(0.95) -> market (3 次)
        # speed <= 0.8: ETH(0.5), DOGE(0.3) -> limit (2 次)
        assert market_executor._call_count == 3
        assert limit_executor._call_count == 2

    @pytest.mark.asyncio
    async def test_concurrent_same_symbol_tracking(self):
        """测试同一 symbol 并发时的追踪一致性"""
        config = SmartExecutorConfig(
            default_executor="limit",
            children={"market": "market/default", "limit": "limit/default"},
        )
        smart = SmartExecutor(config)
        smart.logger = MagicMock()

        market_executor = MockChildExecutor("market")
        limit_executor = MockChildExecutor("limit")
        smart._child_executors = {
            "market": market_executor,
            "limit": limit_executor,
        }

        exchange = MockExchange()

        with patch.object(smart, '_get_recent_trades', return_value=[]):
            # 并发执行同一 symbol 多次
            tasks = [
                smart.execute_delta(
                    exchange=exchange,
                    symbol="BTC/USDT",
                    delta_usd=1000.0,
                    speed=0.5,
                    current_price=50000.0,
                )
                for _ in range(10)
            ]

            results = await asyncio.gather(*tasks)

        # 所有结果都应该成功
        assert all(r.success for r in results)

        # 最终应该只有一个追踪记录
        tracked = await smart._get_tracked_executor(exchange.name, "BTC/USDT")
        assert tracked == "limit"


# ============================================================
# 性能测试
# ============================================================

class TestPerformance:
    """性能测试"""

    @pytest.mark.asyncio
    async def test_cache_reduces_trades_fetch(self):
        """测试缓存减少 trades 获取次数"""
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
        smart._child_executors = {
            "market": market_executor,
            "limit": limit_executor,
        }

        exchange = MockExchange()
        mock_trades = create_mock_trades(20, side="buy")
        get_trades_mock = MagicMock(return_value=mock_trades)

        with patch.object(smart, '_get_recent_trades', get_trades_mock):
            # 连续执行 10 次（同一 tick 周期内）
            for _ in range(10):
                await smart.execute_delta(
                    exchange=exchange,
                    symbol="BTC/USDT",
                    delta_usd=1000.0,
                    speed=0.5,
                    current_price=50000.0,
                )

        # 由于缓存，trades 只应该被获取 1 次
        assert get_trades_mock.call_count == 1

    @pytest.mark.asyncio
    async def test_route_decision_performance(self):
        """测试路由决策性能（大量规则）"""
        # 创建 100 条路由规则
        routes = [
            RouteConfig(condition=f"speed > {0.01 * i}", executor="market", priority=i)
            for i in range(1, 100)
        ]
        routes.append(RouteConfig(condition=None, executor="limit", priority=999))

        config = SmartExecutorConfig(
            default_executor="limit",
            children={"market": "market/default", "limit": "limit/default"},
            routes=routes,
        )
        smart = SmartExecutor(config)
        smart.logger = MagicMock()

        market_executor = MockChildExecutor("market")
        limit_executor = MockChildExecutor("limit")
        smart._child_executors = {
            "market": market_executor,
            "limit": limit_executor,
        }

        exchange = MockExchange()

        with patch.object(smart, '_get_recent_trades', return_value=[]):
            start_time = time.time()

            # 执行 100 次路由决策
            for _ in range(100):
                await smart.execute_delta(
                    exchange=exchange,
                    symbol="BTC/USDT",
                    delta_usd=1000.0,
                    speed=0.5,
                    current_price=50000.0,
                )

            elapsed = time.time() - start_time

        # 100 次路由决策应该在 1 秒内完成
        assert elapsed < 1.0, f"Performance issue: {elapsed:.2f}s for 100 route decisions"


# ============================================================
# 边界情况测试（补充）
# ============================================================

class TestEdgeCases:
    """边界情况测试"""

    @pytest.mark.asyncio
    async def test_empty_routes_uses_default(self):
        """测试空路由列表时使用默认执行器"""
        config = SmartExecutorConfig(
            default_executor="limit",
            children={"market": "market/default", "limit": "limit/default"},
            routes=[],  # 空路由
        )
        smart = SmartExecutor(config)
        smart.logger = MagicMock()

        market_executor = MockChildExecutor("market")
        limit_executor = MockChildExecutor("limit")
        smart._child_executors = {
            "market": market_executor,
            "limit": limit_executor,
        }

        exchange = MockExchange()

        with patch.object(smart, '_get_recent_trades', return_value=[]):
            result = await smart.execute_delta(
                exchange=exchange,
                symbol="BTC/USDT",
                delta_usd=1000.0,
                speed=0.9,
                current_price=50000.0,
            )

        assert result.success is True
        assert limit_executor._execute_delta_mock.called

    @pytest.mark.asyncio
    async def test_zero_delta_usd(self):
        """测试 delta_usd 为 0 时的处理"""
        config = SmartExecutorConfig(
            default_executor="limit",
            children={"market": "market/default", "limit": "limit/default"},
        )
        smart = SmartExecutor(config)
        smart.logger = MagicMock()

        market_executor = MockChildExecutor("market")
        limit_executor = MockChildExecutor("limit")
        smart._child_executors = {
            "market": market_executor,
            "limit": limit_executor,
        }

        exchange = MockExchange()

        with patch.object(smart, '_get_recent_trades', return_value=[]):
            result = await smart.execute_delta(
                exchange=exchange,
                symbol="BTC/USDT",
                delta_usd=0.0,  # 零 delta
                speed=0.5,
                current_price=50000.0,
            )

        # 应该仍然成功执行（子执行器决定如何处理零 delta）
        assert result.success is True

    @pytest.mark.asyncio
    async def test_negative_delta_usd_sell_direction(self):
        """测试负 delta_usd（卖出方向）的 edge/notional 计算"""
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

        market_executor = MockChildExecutor("market")
        limit_executor = MockChildExecutor("limit")
        smart._child_executors = {
            "market": market_executor,
            "limit": limit_executor,
        }

        exchange = MockExchange()

        # sell trades 的 notional=5000
        mock_trades = [
            MockTradeData(id="1", symbol="BTC/USDT", timestamp=int(time.time() * 1000),
                          side="sell", price=100.0, amount=50.0, cost=5000.0),
        ]

        with patch.object(smart, '_get_recent_trades', return_value=mock_trades):
            result = await smart.execute_delta(
                exchange=exchange,
                symbol="BTC/USDT",
                delta_usd=-1000.0,  # 负数=卖出
                speed=0.5,
                current_price=100.0,
            )

        # sell notional=5000 > 1000，应选择 market
        assert result.success is True
        assert market_executor._execute_delta_mock.called

    @pytest.mark.asyncio
    async def test_very_small_edge_value(self):
        """测试非常小的 edge 值"""
        config = SmartExecutorConfig(
            default_executor="limit",
            children={"market": "market/default", "limit": "limit/default"},
            routes=[
                RouteConfig(condition="edge > 0.0001", executor="market", priority=1),
                RouteConfig(condition=None, executor="limit", priority=999),
            ],
        )
        smart = SmartExecutor(config)
        smart.logger = MagicMock()

        market_executor = MockChildExecutor("market")
        limit_executor = MockChildExecutor("limit")
        smart._child_executors = {
            "market": market_executor,
            "limit": limit_executor,
        }

        exchange = MockExchange()
        exchange.config.swap_taker_fee = 0.0001

        # buy at 49999, current price 50000 -> 非常小的 edge
        # edge = (50000 - 49999) / 50000 - 0.0001 = 0.00002 - 0.0001 = -0.00008
        mock_trades = [
            MockTradeData(id="1", symbol="BTC/USDT", timestamp=int(time.time() * 1000),
                          side="buy", price=49999.0, amount=1.0, cost=49999.0),
        ]

        with patch.object(smart, '_get_recent_trades', return_value=mock_trades):
            result = await smart.execute_delta(
                exchange=exchange,
                symbol="BTC/USDT",
                delta_usd=1000.0,
                speed=0.5,
                current_price=50000.0,
            )

        # edge ≈ -0.00008 < 0.0001，使用默认 limit
        assert result.success is True
        assert limit_executor._execute_delta_mock.called
        assert not market_executor._execute_delta_mock.called


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

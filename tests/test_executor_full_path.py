"""
DefaultExecutor 完整执行路径测试

测试从 OrderDefinition 配置到最终下单的完整路径：
1. OrderDefinition / BaseExecutorConfig 配置解析
2. 表达式求值生成 OrderIntent
3. ActiveOrdersTracker.calculate_changed_orders 增量计算
4. process_intents 集成测试（使用 mock exchange）
5. create_orders_by_intents 行为验证
"""
import asyncio
import copy
import time
import logging
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from hft.executor.base import (
    ActiveOrder,
    ActiveOrdersTracker,
    BaseExecutor,
    OrderIntent,
)
from hft.executor.config import BaseExecutorConfig, OrderDefinition
from hft.executor.default_executor.config import DefaultExecutorConfig
from hft.core.scope.vm import VirtualMachine
from hft.core.scope.base import FlowScopeNode, BaseScope


# ============================================================
# 辅助：轻量级 Scope（不依赖 AppCore）
# ============================================================

class _MinimalScope(BaseScope):
    """不依赖 AppCore 的最小 Scope，用于纯表达式求值测试"""

    def initialize(self, **kwargs):
        # 跳过 BaseScope.initialize 中对 app_core 等字段的依赖
        self._instance_id = kwargs.get("instance_id", ("test",))
        self._functions: dict = {
            # 注入常用函数（正常流程中由 GlobalScope 提供）
            "abs": abs,
            "min": min,
            "max": max,
            "round": round,
        }
        self._vars: dict = kwargs.get("vars", {})
        self._conditional_vars_update_times: dict = {}

    @classmethod
    def get_all_instance_ids(cls, app_core):
        return set()


def _make_node(variables: dict | None = None) -> FlowScopeNode:
    """创建一个携带预设变量的 FlowScopeNode"""
    scope = _MinimalScope(instance_id=("test",), vars=dict(variables or {}))
    return FlowScopeNode(scope=scope, prev=[])


# ============================================================
# 辅助：mock exchange 和 executor stub
# ============================================================

def _make_mock_exchange(contract_size=1.0):
    """创建一个轻量 mock exchange 用于 process_intents 测试"""
    exchange = AsyncMock()
    exchange.get_contract_size_async = AsyncMock(return_value=contract_size)
    # create_orders 返回带 id 的结果
    order_counter = {"n": 0}

    async def _create_orders(order_params_list):
        results = []
        for _ in order_params_list:
            order_counter["n"] += 1
            results.append({"id": f"sim-{order_counter['n']}", "status": "open"})
        return results
    exchange.create_orders = AsyncMock(side_effect=_create_orders)
    exchange.cancel_orders = AsyncMock()
    exchange.fetch_order = AsyncMock(return_value={"status": "open"})
    return exchange


def _make_executor_stub(exchange, exchange_path="sim/swap"):
    """
    创建一个可直接调用 process_intents / create_orders_by_intents 的
    BaseExecutor 实例，绕过完整的 Listener 树初始化。
    """
    executor = object.__new__(BaseExecutor)
    executor.active_orders_tracker = ActiveOrdersTracker()
    executor._refresh_timestamps = {}
    executor._name = "test_executor"
    executor.logger = logging.getLogger("test_executor")

    # mock exchange_group（避免通过 self.root.exchange_group 递归）
    eg = MagicMock()
    eg.exchange_instances = {exchange_path: exchange}
    eg.event = MagicMock()
    eg.event.on = MagicMock()

    # 直接在实例上覆盖 exchange_group 属性，跳过 property 的 self.root 链路
    # 使用 __dict__ 赋值会被 property descriptor 拦截，所以用 type 动态子类
    # 更简单的方式：直接 patch 对象
    executor.__class__ = type(
        "_StubExecutor",
        (BaseExecutor,),
        {"exchange_group": property(lambda self: eg)},
    )
    return executor


# ============================================================
# 1. OrderDefinition / BaseExecutorConfig 配置测试
# ============================================================

class TestOrderDefinitionConfig:

    def test_total_order_definitions_with_order_levels(self):
        """order_levels=3 + order 应生成 6 个订单定义（level: -3,-2,-1,1,2,3）"""
        config = DefaultExecutorConfig(
            order=OrderDefinition(
                order_usd="100 * abs(level)",
                spread="0.5 * abs(level)",
                timeout=30,
            ),
            order_levels=3,
        )
        defs = config.total_order_definitions
        assert len(defs) == 6
        levels = sorted([d.level for d in defs])
        assert levels == [-3, -2, -1, 1, 2, 3]

    def test_total_order_definitions_orders_only(self):
        """仅设置 orders 列表（无 order_levels）时直接使用"""
        config = DefaultExecutorConfig(
            orders=[
                OrderDefinition(order_amount="1.0", spread="0.1", timeout=10),
                OrderDefinition(order_amount="-1.0", spread="0.2", timeout=10),
            ],
        )
        defs = config.total_order_definitions
        assert len(defs) == 2
        assert defs[0].order_amount == "1.0"
        assert defs[1].order_amount == "-1.0"

    def test_total_order_definitions_combined(self):
        """同时设置 orders 和 order+order_levels 时应合并"""
        config = DefaultExecutorConfig(
            orders=[
                OrderDefinition(order_amount="0.5", spread="0.01", timeout=10),
            ],
            order=OrderDefinition(order_usd="50", spread="0.05", timeout=20),
            order_levels=2,
        )
        defs = config.total_order_definitions
        # 1 (orders) + 4 (2 levels * 2 directions)
        assert len(defs) == 5

    def test_order_levels_each_is_independent_copy(self):
        """order_levels 生成的订单定义之间应互相独立"""
        order_template = OrderDefinition(order_usd="100", spread="1.0", timeout=60)
        config = DefaultExecutorConfig(
            order=order_template,
            order_levels=2,
        )
        defs = config.total_order_definitions
        # 修改其中一个不影响其他
        defs[0].timeout = 999
        assert defs[1].timeout != 999

    def test_order_definition_vars(self):
        """OrderDefinition 的 vars 应能正常解析为 standard_vars_definition"""
        od = OrderDefinition(
            vars=[
                {"name": "my_spread", "value": "0.01 * level"},
            ],
            order_usd="100",
            spread="my_spread",
            timeout=30,
        )
        svd = od.standard_vars_definition
        assert len(svd) == 1
        assert svd[0].name == "my_spread"
        assert svd[0].value == "0.01 * level"

    def test_order_levels_zero_produces_empty(self):
        """order_levels=0 时不生成额外订单（但需要 order 不为 None 会被跳过）"""
        config = DefaultExecutorConfig(
            orders=[OrderDefinition(order_amount="1", spread="0.1", timeout=10)],
        )
        defs = config.total_order_definitions
        assert len(defs) == 1


# ============================================================
# 2. 表达式求值生成 OrderIntent
# ============================================================

class TestOrderIntentExpressions:

    def setup_method(self):
        self.vm = VirtualMachine()

    # ---- order_usd -> amount 转换 ----

    def test_order_usd_to_amount(self):
        """order_usd 表达式求值后应除以 last_price 得到 amount"""
        node = _make_node({"last_price": 50000.0})
        usd = self.vm.eval("200", node)
        amount = usd / node.get_var("last_price")
        assert abs(amount - 0.004) < 1e-9

    def test_order_usd_expression_with_level(self):
        """order_usd 中引用 level 变量和 abs 函数"""
        node = _make_node({"level": 2, "last_price": 100.0})
        usd = self.vm.eval("50 * abs(level)", node)
        assert usd == 100.0
        amount = usd / node.get_var("last_price")
        assert abs(amount - 1.0) < 1e-9

    # ---- order_amount -> 直接数量 ----

    def test_order_amount_direct(self):
        """order_amount 表达式直接作为数量"""
        node = _make_node({"level": -1})
        amount = self.vm.eval("-0.5 * abs(level)", node)
        assert abs(amount - (-0.5)) < 1e-9

    # ---- spread -> 买卖价格 ----

    def test_spread_buy_price(self):
        """买单价格 = bid_price - spread"""
        node = _make_node({
            "bid_price": 49990.0,
            "ask_price": 50010.0,
            "mid_price": 50000.0,
        })
        spread = self.vm.eval("5.0", node)
        price = node.get_var("bid_price") - spread
        assert abs(price - 49985.0) < 1e-9

    def test_spread_sell_price(self):
        """卖单价格 = ask_price + spread"""
        node = _make_node({
            "bid_price": 49990.0,
            "ask_price": 50010.0,
            "mid_price": 50000.0,
        })
        spread = self.vm.eval("5.0", node)
        price = node.get_var("ask_price") + spread
        assert abs(price - 50015.0) < 1e-9

    def test_spread_expression_with_mid_price(self):
        """spread 表达式引用 mid_price"""
        node = _make_node({"mid_price": 50000.0, "bid_price": 49990.0})
        spread = self.vm.eval("mid_price * 0.001", node)
        assert abs(spread - 50.0) < 1e-9
        price = node.get_var("bid_price") - spread
        assert abs(price - 49940.0) < 1e-9

    # ---- price -> 绝对价格 ----

    def test_absolute_price_expression(self):
        """price 表达式直接作为绝对价格"""
        node = _make_node({"mid_price": 50000.0})
        price = self.vm.eval("mid_price - 100", node)
        assert abs(price - 49900.0) < 1e-9

    # ---- condition -> 跳过 ----

    def test_condition_true_proceeds(self):
        """condition 为 True 时不跳过"""
        node = _make_node({"level": 1, "max_level": 3})
        assert self.vm.eval_condition("abs(level) <= max_level", node) is True

    def test_condition_false_skips(self):
        """condition 为 False 时跳过"""
        node = _make_node({"level": 4, "max_level": 3})
        assert self.vm.eval_condition("abs(level) <= max_level", node) is False

    def test_condition_none_always_true(self):
        """condition 为 None 时始终执行"""
        node = _make_node({})
        assert self.vm.eval_condition(None, node) is True

    # ---- timeout / refresh_tolerance ----

    def test_timeout_expression(self):
        """timeout 表达式求值"""
        node = _make_node({"base_timeout": 30})
        timeout = self.vm.eval("base_timeout * 2", node)
        assert timeout == 60

    def test_refresh_tolerance_literal(self):
        """refresh_tolerance 字面量直接返回"""
        node = _make_node({})
        val = self.vm.eval(0.5, node)
        assert val == 0.5

    def test_vars_execution_before_order_eval(self):
        """vars 中定义的变量可在后续表达式中使用"""
        node = _make_node({"mid_price": 50000.0})
        od = OrderDefinition(
            vars=[{"name": "my_spread", "value": "mid_price * 0.001"}],
            order_amount="1.0",
            spread="my_spread",
            timeout=60,
        )
        self.vm.execute_vars(od.standard_vars_definition, node)
        my_spread = node.get_var("my_spread")
        assert abs(my_spread - 50.0) < 1e-9
        spread = self.vm.eval(od.spread, node)
        assert abs(spread - 50.0) < 1e-9


# ============================================================
# 3. ActiveOrdersTracker.calculate_changed_orders 执行路径
#    (补充执行器路径特有的场景，基本测试在 test_active_orders_tracker.py)
# ============================================================

class TestCalculateChangedOrdersExecutorPath:

    def setup_method(self):
        self.tracker = ActiveOrdersTracker()
        self.ep = "sim/swap"
        self.sym = "ETH/USDT:USDT"

    def _add(self, order_id, price, timeout=60.0, created_at=None):
        o = ActiveOrder(
            order_id=order_id,
            exchange_path=self.ep,
            symbol=self.sym,
            price=price,
            amount=0.1,
            created_at=created_at or time.time(),
            timeout_refresh_tolerance=timeout,
        )
        self.tracker.add_active_orders(self.ep, self.sym, [o])
        return o

    def _intent(self, price, tol=5.0, timeout=60.0):
        return OrderIntent(
            price=price, amount=0.1,
            timeout_refresh_tolerance=timeout,
            price_refresh_tolerance=tol,
        )

    def test_new_intents_all_placed(self):
        """没有已有订单时所有意图全部放置"""
        intents = [self._intent(3000.0), self._intent(2990.0)]
        to_place, to_remove = self.tracker.calculate_changed_orders(self.ep, self.sym, intents)
        assert len(to_place) == 2
        assert len(to_remove) == 0

    def test_same_intents_within_tolerance_no_changes(self):
        """意图价格在容忍范围内，不产生变更"""
        self._add("o1", 3000.0)
        intents = [self._intent(3003.0, tol=5.0)]
        to_place, to_remove = self.tracker.calculate_changed_orders(self.ep, self.sym, intents)
        assert len(to_place) == 0
        assert len(to_remove) == 0

    def test_price_out_of_tolerance_cancel_and_replace(self):
        """价格偏移超出容忍度 -> 取消旧单 + 放置新单"""
        self._add("o1", 3000.0)
        intents = [self._intent(3020.0, tol=5.0)]
        to_place, to_remove = self.tracker.calculate_changed_orders(self.ep, self.sym, intents)
        assert len(to_remove) == 1
        assert to_remove[0].order_id == "o1"
        assert len(to_place) == 1
        assert to_place[0].price == 3020.0

    def test_timeout_exceeded_cancel_and_replace(self):
        """过期订单 -> 取消 + 重新放置"""
        self._add("o1", 3000.0, timeout=10.0, created_at=time.time() - 20.0)
        intents = [self._intent(3000.0, tol=5.0)]
        to_place, to_remove = self.tracker.calculate_changed_orders(self.ep, self.sym, intents)
        assert len(to_remove) == 1
        assert len(to_place) == 1

    def test_fewer_intents_than_existing_cancel_extras(self):
        """意图数量少于已有订单 -> 多余订单被取消"""
        self._add("o1", 3000.0)
        self._add("o2", 3100.0)
        intents = [self._intent(3000.0, tol=5.0)]
        to_place, to_remove = self.tracker.calculate_changed_orders(self.ep, self.sym, intents)
        remove_ids = {o.order_id for o in to_remove}
        assert "o2" in remove_ids
        assert "o1" not in remove_ids
        assert len(to_place) == 0


# ============================================================
# 4. process_intents 集成测试
# ============================================================

class TestProcessIntents:

    async def test_process_intents_creates_orders(self):
        """process_intents 应创建新订单并添加到 tracker"""
        exchange = _make_mock_exchange()
        executor = _make_executor_stub(exchange)
        ep = "sim/swap"
        sym = "BTC/USDT:USDT"

        intents = [
            OrderIntent(price=50000.0, amount=0.01, timeout_refresh_tolerance=60,
                        price_refresh_tolerance=10.0, post_only=True),
            OrderIntent(price=49000.0, amount=0.02, timeout_refresh_tolerance=60,
                        price_refresh_tolerance=10.0, post_only=True),
        ]
        await executor.process_intents(ep, sym, intents)
        exchange.create_orders.assert_called_once()
        # 应在 tracker 中注册了 2 个活跃订单
        tracked = executor.active_orders_tracker.orders[ep][sym]
        assert len(tracked) == 2

    async def test_process_intents_cancels_outdated(self):
        """已过期的活跃订单应被取消"""
        exchange = _make_mock_exchange()
        executor = _make_executor_stub(exchange)
        ep = "sim/swap"
        sym = "BTC/USDT:USDT"

        # 添加一个已过期的订单
        old_order = ActiveOrder(
            order_id="old-1", exchange_path=ep, symbol=sym,
            price=50000.0, amount=0.01,
            created_at=time.time() - 120.0,
            timeout_refresh_tolerance=60.0,
        )
        executor.active_orders_tracker.add_active_orders(ep, sym, [old_order])

        # 新意图与旧订单相同价格
        intents = [
            OrderIntent(price=50000.0, amount=0.01, timeout_refresh_tolerance=60,
                        price_refresh_tolerance=10.0),
        ]
        await executor.process_intents(ep, sym, intents)
        # cancel_orders 应被调用
        exchange.cancel_orders.assert_called()

    async def test_process_intents_replaces_drifted_price(self):
        """价格偏移超出容忍度的订单应被替换"""
        exchange = _make_mock_exchange()
        executor = _make_executor_stub(exchange)
        ep = "sim/swap"
        sym = "BTC/USDT:USDT"

        existing = ActiveOrder(
            order_id="exist-1", exchange_path=ep, symbol=sym,
            price=50000.0, amount=0.01,
            created_at=time.time(),
            timeout_refresh_tolerance=60.0,
        )
        executor.active_orders_tracker.add_active_orders(ep, sym, [existing])

        # 新意图价格偏移较大
        intents = [
            OrderIntent(price=50200.0, amount=0.01, timeout_refresh_tolerance=60,
                        price_refresh_tolerance=10.0),
        ]
        await executor.process_intents(ep, sym, intents)
        exchange.create_orders.assert_called_once()
        exchange.cancel_orders.assert_called()

    async def test_process_intents_empty_list_noop(self):
        """空意图列表应直接返回，不做任何操作"""
        exchange = _make_mock_exchange()
        executor = _make_executor_stub(exchange)
        await executor.process_intents("sim/swap", "BTC/USDT:USDT", [])
        exchange.create_orders.assert_not_called()
        exchange.cancel_orders.assert_not_called()

    async def test_process_intents_uses_lock(self):
        """process_intents 应通过 lock 保护 tracker 操作"""
        exchange = _make_mock_exchange()
        executor = _make_executor_stub(exchange)
        ep = "sim/swap"
        sym = "BTC/USDT:USDT"

        lock = executor.active_orders_tracker._lock
        assert isinstance(lock, asyncio.Lock)

        intents = [
            OrderIntent(price=50000.0, amount=0.01, timeout_refresh_tolerance=60,
                        price_refresh_tolerance=10.0),
        ]
        await executor.process_intents(ep, sym, intents)
        tracked = executor.active_orders_tracker.orders[ep][sym]
        assert len(tracked) == 1

    async def test_process_intents_new_then_stable(self):
        """第一次调用创建订单，第二次相同意图不变更"""
        exchange = _make_mock_exchange()
        executor = _make_executor_stub(exchange)
        ep = "sim/swap"
        sym = "BTC/USDT:USDT"

        intents = [
            OrderIntent(price=50000.0, amount=0.01, timeout_refresh_tolerance=60,
                        price_refresh_tolerance=10.0, post_only=True),
        ]
        await executor.process_intents(ep, sym, intents)
        assert exchange.create_orders.call_count == 1

        # 第二次调用相同意图 - 不应再创建
        await executor.process_intents(ep, sym, intents)
        assert exchange.create_orders.call_count == 1  # 仍然只有 1 次


# ============================================================
# 5. create_orders_by_intents 行为测试
# ============================================================

class TestCreateOrdersByIntents:

    async def test_market_order_not_tracked(self):
        """市价单（price=None）不应被跟踪在 active orders 中"""
        exchange = _make_mock_exchange()
        executor = _make_executor_stub(exchange)

        intents = [
            OrderIntent(price=None, amount=0.01, timeout_refresh_tolerance=60,
                        price_refresh_tolerance=0, post_only=False),
        ]
        created = await executor.create_orders_by_intents(
            "sim/swap", "BTC/USDT:USDT", intents
        )
        assert len(created) == 0

    async def test_limit_order_tracked(self):
        """限价单应返回 ActiveOrder 并可被跟踪"""
        exchange = _make_mock_exchange()
        executor = _make_executor_stub(exchange)

        intents = [
            OrderIntent(price=50000.0, amount=0.01, timeout_refresh_tolerance=60,
                        price_refresh_tolerance=10.0, post_only=True),
        ]
        created = await executor.create_orders_by_intents(
            "sim/swap", "BTC/USDT:USDT", intents
        )
        assert len(created) == 1
        assert created[0].price == 50000.0
        assert created[0].amount == 0.01

    async def test_post_only_flag_propagation(self):
        """post_only=True 应传递到 order request 的 params 中"""
        exchange = _make_mock_exchange()
        executor = _make_executor_stub(exchange)

        intents = [
            OrderIntent(price=50000.0, amount=0.01, timeout_refresh_tolerance=60,
                        price_refresh_tolerance=10.0, post_only=True),
        ]
        await executor.create_orders_by_intents(
            "sim/swap", "BTC/USDT:USDT", intents
        )
        call_args = exchange.create_orders.call_args[0][0]
        assert call_args[0]["params"] == {"postOnly": True}

    async def test_post_only_false_no_params(self):
        """post_only=False 时 params 应为空"""
        exchange = _make_mock_exchange()
        executor = _make_executor_stub(exchange)

        intents = [
            OrderIntent(price=50000.0, amount=0.01, timeout_refresh_tolerance=60,
                        price_refresh_tolerance=10.0, post_only=False),
        ]
        await executor.create_orders_by_intents(
            "sim/swap", "BTC/USDT:USDT", intents
        )
        call_args = exchange.create_orders.call_args[0][0]
        assert call_args[0]["params"] == {}

    async def test_contract_size_applied(self):
        """amount 应除以 contract_size 后传给交易所"""
        exchange = _make_mock_exchange(contract_size=0.01)
        executor = _make_executor_stub(exchange)

        intents = [
            OrderIntent(price=50000.0, amount=0.1, timeout_refresh_tolerance=60,
                        price_refresh_tolerance=10.0, post_only=True),
        ]
        await executor.create_orders_by_intents(
            "sim/swap", "BTC/USDT:USDT", intents
        )
        call_args = exchange.create_orders.call_args[0][0]
        # 0.1 / 0.01 = 10 contracts
        assert abs(call_args[0]["amount"] - 10.0) < 1e-9

    async def test_buy_sell_side_mapping(self):
        """正数 amount -> buy, 负数 amount -> sell"""
        exchange = _make_mock_exchange()
        executor = _make_executor_stub(exchange)

        intents = [
            OrderIntent(price=50000.0, amount=0.01, timeout_refresh_tolerance=60,
                        price_refresh_tolerance=10.0),
            OrderIntent(price=49000.0, amount=-0.02, timeout_refresh_tolerance=60,
                        price_refresh_tolerance=10.0),
        ]
        await executor.create_orders_by_intents(
            "sim/swap", "BTC/USDT:USDT", intents
        )
        call_args = exchange.create_orders.call_args[0][0]
        assert call_args[0]["side"] == "buy"
        assert call_args[1]["side"] == "sell"

    async def test_limit_vs_market_type(self):
        """price 非 None -> limit, price 为 None -> market"""
        exchange = _make_mock_exchange()
        executor = _make_executor_stub(exchange)

        intents = [
            OrderIntent(price=50000.0, amount=0.01, timeout_refresh_tolerance=60,
                        price_refresh_tolerance=10.0),
            OrderIntent(price=None, amount=0.01, timeout_refresh_tolerance=60,
                        price_refresh_tolerance=0, post_only=False),
        ]
        await executor.create_orders_by_intents(
            "sim/swap", "BTC/USDT:USDT", intents
        )
        call_args = exchange.create_orders.call_args[0][0]
        assert call_args[0]["type"] == "limit"
        assert call_args[1]["type"] == "market"

    async def test_multiple_intents_returns_only_limit_active_orders(self):
        """混合限价/市价意图只返回限价单的 ActiveOrder"""
        exchange = _make_mock_exchange()
        executor = _make_executor_stub(exchange)

        intents = [
            OrderIntent(price=50000.0, amount=0.01, timeout_refresh_tolerance=60,
                        price_refresh_tolerance=10.0, post_only=True),
            OrderIntent(price=None, amount=0.02, timeout_refresh_tolerance=60,
                        price_refresh_tolerance=0, post_only=False),
            OrderIntent(price=49000.0, amount=-0.01, timeout_refresh_tolerance=60,
                        price_refresh_tolerance=10.0, post_only=True),
        ]
        created = await executor.create_orders_by_intents(
            "sim/swap", "BTC/USDT:USDT", intents
        )
        # 只有两个限价单被跟踪
        assert len(created) == 2
        prices = {o.price for o in created}
        assert 50000.0 in prices
        assert 49000.0 in prices

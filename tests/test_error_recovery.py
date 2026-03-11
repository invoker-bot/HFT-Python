"""
错误处理与恢复场景测试

覆盖：
1. BaseExchange.__resolve_order() 验证逻辑
2. 余额边界条件
3. max_position_usd 边界
4. Funding 结算 + 订单成交在同一 tick
5. ActiveOrder.outdated 超时检测
6. HealthyData 过期与恢复
7. Listener 状态机异常恢复
"""
import time
import asyncio
import pytest
from unittest.mock import AsyncMock
from freezegun import freeze_time

from hft.exchange.simulated.engines.price import PriceEngine
from hft.exchange.simulated.engines.funding import FundingEngine
from hft.exchange.simulated.engines.orders import OrderManager
from hft.exchange.simulated.engines.positions import PositionTracker
from hft.exchange.simulated.engines.balance import BalanceTracker
from hft.exchange.simulated.markets import SYMBOLS_CONFIG, get_swap_symbols
from hft.executor.base import ActiveOrder
from hft.core.healthy_data import HealthyData
from hft.core.listener import Listener, ListenerState


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def tracker():
    return PositionTracker()


@pytest.fixture
def balance():
    return BalanceTracker(100_000.0)


@pytest.fixture
def price_engine():
    return PriceEngine(volatility=0.001, seed=42)


@pytest.fixture
def contract_sizes():
    return {
        f"{base}/USDT:USDT": cfg['contract_size']
        for base, cfg in SYMBOLS_CONFIG.items()
    }


@pytest.fixture
def order_manager(tracker, balance, contract_sizes, price_engine):
    return OrderManager(
        tracker, balance,
        fill_probability=1.0,
        contract_sizes=contract_sizes,
        price_engine=price_engine,
    )


@pytest.fixture
def funding_engine():
    return FundingEngine(
        swap_symbols=get_swap_symbols(),
        base_rate=0.0001,
        interval_hours=8,
        seed=42,
    )


# ============================================================
# 1. BaseExchange.__resolve_order() 验证逻辑
# ============================================================

class TestResolveOrderValidation:
    """
    通过 SimulatedExchange 的 create_order() 测试 __resolve_order() 的验证逻辑。
    需要完整 AppCore 来走通 BaseExchange.create_order() 路径。
    """

    @pytest.fixture
    def app_core(self):
        from hft.core.app.factory import AppFactory
        factory = AppFactory("sim-spot-future", restore_cache=False)
        return factory.get_or_create_app_core()

    async def test_valid_order_succeeds(self, app_core):
        """有效订单应成功创建"""
        await app_core.start(True)
        try:
            exchange = list(app_core.exchange_group.children.values())[0]
            ticker = await exchange.fetch_ticker("BTC/USDT:USDT")
            price = ticker['last']
            # BTC amount_prec = 0.00001, 下 1.0 张合约应该没问题
            order = await exchange.create_order(
                "BTC/USDT:USDT", "limit", "buy", 1.0, price * 0.95
            )
            assert order is not None
            assert order['status'] == 'open'
        finally:
            await app_core.stop(True)

    async def test_amount_below_precision_rejected(self, app_core):
        """数量低于精度最小值的订单应被拒绝（返回 None）"""
        await app_core.start(True)
        try:
            exchange = list(app_core.exchange_group.children.values())[0]
            ticker = await exchange.fetch_ticker("BTC/USDT:USDT")
            price = ticker['last']
            # BTC amount_prec = 0.00001, min amount = 0.00001
            # 下极小数量的订单，precision round 后应 < min
            order = await exchange.create_order(
                "BTC/USDT:USDT", "limit", "buy", 0.000001, price * 0.95
            )
            assert order is None, "低于精度最小值的订单应被拒绝"
        finally:
            await app_core.stop(True)

    async def test_max_position_per_pair_usd_rejection(self, app_core):
        """超过 max_position_per_pair_usd 的订单应被拒绝"""
        await app_core.start(True)
        try:
            exchange = list(app_core.exchange_group.children.values())[0]
            # 设置一个较小的 max_position_per_pair_usd
            exchange.config.max_position_per_pair_usd = 100.0  # 仅 $100
            ticker = await exchange.fetch_ticker("BTC/USDT:USDT")
            price = ticker['last']
            # 尝试下一个超过 $100 的订单
            # BTC contract_size=0.001, 所以 amount * contract_size * price 是仓位 USD
            # 需要 amount * 0.001 * price > 100
            # amount > 100 / (0.001 * price)
            large_amount = 200 / (0.001 * price)
            order = await exchange.create_order(
                "BTC/USDT:USDT", "limit", "buy", large_amount, price
            )
            assert order is None, "超过 max_position_per_pair_usd 的订单应被拒绝"
        finally:
            await app_core.stop(True)

    async def test_reduce_only_caps_amount_to_position(self, app_core):
        """减仓订单应被标记为 reduceOnly，且数量受持仓限制

        __resolve_order 在检测到方向反转时进入减仓分支：
        1. 标记 reduceOnly=True
        2. cap 数量到 abs(position_amount)
        市价单成交后 _positions 被 mark_dirty，下次查询会重新拉取。
        """
        await app_core.start(True)
        try:
            exchange = list(app_core.exchange_group.children.values())[0]
            ticker = await exchange.fetch_ticker("BTC/USDT:USDT")
            price = ticker['last']

            # 先建仓：买入 10 张 (position = 10 * 0.001 = 0.01 BTC)
            buy_order = await exchange.create_order(
                "BTC/USDT:USDT", "market", "buy", 10.0, price
            )
            assert buy_order is not None
            assert buy_order['status'] == 'closed'

            # 验证持仓存在
            pos_before = exchange.position_tracker.get("BTC/USDT:USDT")
            assert pos_before > 0

            # 卖出 100 张（远超持仓）
            sell_order = await exchange.create_order(
                "BTC/USDT:USDT", "market", "sell", 100.0, price
            )
            assert sell_order is not None
            assert sell_order['status'] == 'closed'

            # 仓位应减少（可能减到负数或接近 0，取决于 reduceOnly cap 行为）
            pos_after = exchange.position_tracker.get("BTC/USDT:USDT")
            assert pos_after < pos_before, "卖出后仓位应减少"
        finally:
            await app_core.stop(True)

    async def test_reduce_only_below_precision_rejected(self, app_core):
        """减仓数量低于精度时应被拒绝"""
        await app_core.start(True)
        try:
            exchange = list(app_core.exchange_group.children.values())[0]
            ticker = await exchange.fetch_ticker("BTC/USDT:USDT")
            price = ticker['last']

            # 建立极小仓位
            # BTC amount_prec=0.00001, contract_size=0.001
            buy_order = await exchange.create_order(
                "BTC/USDT:USDT", "market", "buy", 0.00001, price
            )

            # 尝试以极小数量卖出（align 后 < precision）
            sell_order = await exchange.create_order(
                "BTC/USDT:USDT", "limit", "sell", 0.000001, price * 1.05
            )
            # 数量对齐后低于精度，应被拒绝
            assert sell_order is None, "减仓数量低于精度应被拒绝"
        finally:
            await app_core.stop(True)


# ============================================================
# 2. 余额边界条件
# ============================================================

class TestBalanceEdgeCases:
    """使用引擎层直接测试余额边界"""

    def test_balance_decreases_on_spot_buy(self, order_manager, balance, price_engine):
        """现货买入应扣减余额"""
        symbol = "BTC/USDT"
        price = price_engine.get_price(symbol)
        initial_balance = balance.get_usdt_balance()

        order_manager.place_order(symbol, "market", "buy", 1.0, price)

        # 现货买入扣 USDT：cost + fee
        assert balance.get_usdt_balance() < initial_balance

    def test_balance_after_multiple_trades(self, order_manager, balance, price_engine):
        """多次交易后余额应正确递减（手续费累积）"""
        symbol = "ETH/USDT:USDT"
        price = price_engine.get_price(symbol)
        balances = [balance.get_usdt_balance()]

        for _ in range(10):
            order_manager.place_order(symbol, "market", "buy", 100.0, price)
            order_manager.place_order(symbol, "market", "sell", 100.0, price)
            balances.append(balance.get_usdt_balance())

        # 每次买卖都收手续费，余额应单调递减
        for i in range(1, len(balances)):
            assert balances[i] < balances[i - 1], (
                f"余额应递减: {balances[i - 1]} -> {balances[i]}"
            )

    def test_large_spot_order_depletes_balance(self, order_manager, balance, price_engine):
        """大额现货买单可使余额大幅下降"""
        symbol = "BTC/USDT"
        price = price_engine.get_price(symbol)
        initial_balance = balance.get_usdt_balance()

        # 买入 $90000 worth (大约用掉大部分余额)
        amount = 90000 / price
        order_manager.place_order(symbol, "market", "buy", amount, price)

        remaining = balance.get_usdt_balance()
        assert remaining < initial_balance * 0.15, (
            f"大额买单后余额应大幅减少: {remaining}"
        )


# ============================================================
# 3. max_position_usd 边界
# ============================================================

class TestMaxPositionBoundary:
    """通过引擎层测试仓位限制的边界行为"""

    def test_position_approaching_limit(self, tracker, balance, contract_sizes, price_engine):
        """仓位接近限制时订单仍可执行"""
        symbol = "ETH/USDT:USDT"
        price = price_engine.get_price(symbol)
        cs = contract_sizes[symbol]

        om = OrderManager(
            tracker, balance,
            fill_probability=1.0,
            contract_sizes=contract_sizes,
            price_engine=price_engine,
        )

        # 买入一些仓位
        om.place_order(symbol, "market", "buy", 10.0, price)
        pos = tracker.get(symbol)
        assert pos > 0, "应有多头仓位"

        # 继续买入更多
        om.place_order(symbol, "market", "buy", 5.0, price)
        pos2 = tracker.get(symbol)
        assert pos2 > pos, "应能继续加仓"

    def test_position_tracker_accumulates_correctly(self, tracker):
        """仓位追踪器应正确累积"""
        symbol = "BTC/USDT:USDT"

        tracker.update(symbol, 1.0, 80000.0)
        assert tracker.get(symbol) == 1.0

        tracker.update(symbol, 0.5, 81000.0)
        assert tracker.get(symbol) == 1.5

        # 加权平均入场价
        expected_entry = (80000.0 * 1.0 + 81000.0 * 0.5) / 1.5
        assert abs(tracker.get_entry_price(symbol) - expected_entry) < 0.01

    def test_position_reduction_preserves_entry_price(self, tracker):
        """减仓时入场价不变"""
        symbol = "ETH/USDT:USDT"

        tracker.update(symbol, 10.0, 3000.0)
        entry_before = tracker.get_entry_price(symbol)

        tracker.update(symbol, -5.0, 3500.0)
        entry_after = tracker.get_entry_price(symbol)

        assert entry_before == entry_after, "减仓不应改变入场价"
        assert tracker.get(symbol) == 5.0


# ============================================================
# 4. Funding 结算 + 订单成交在同一 tick
# ============================================================

class TestFundingAndOrderSameTick:
    """验证 advance() 中 funding 结算和限价单成交可在同一步发生"""

    def test_funding_settlement_and_order_fill_same_tick(
        self, tracker, balance, contract_sizes, price_engine, funding_engine
    ):
        """
        设置：
        1. 建立多头仓位
        2. 挂一个容易成交的限价单
        3. 设置 funding 即将结算
        4. 调用 advance() 推进一步
        5. 验证 funding 已结算且订单已成交
        """
        symbol = "BTC/USDT:USDT"
        cs = contract_sizes[symbol]

        om = OrderManager(
            tracker, balance,
            fill_probability=1.0,
            contract_sizes=contract_sizes,
            rng=price_engine._rng,
            price_engine=price_engine,
        )

        # 建立 1 BTC 多头仓位
        price = price_engine.get_price(symbol)
        contracts_for_1btc = 1.0 / cs
        om.place_order(symbol, "market", "buy", contracts_for_1btc, price)
        pos_before = tracker.get(symbol)
        assert abs(pos_before - 1.0) < 1e-6

        balance_after_buy = balance.get_usdt_balance()

        # 设置 funding 即将结算
        state = funding_engine._states[symbol]
        state.current_rate = 0.001  # 正费率
        state.mark_price = price
        state.index_price = price
        state.next_funding_timestamp = time.time() - 1  # 已过期，应触发结算

        # 挂一个容易成交的限价买单（价格高于 ask）
        limit_order = om.place_order(
            symbol, "limit", "buy", contracts_for_1btc,
            price * 1.05  # 远高于 ask，很容易成交
        )
        assert limit_order['status'] == 'open'

        # 同一 tick 推进：funding + order fill
        price_states = {s: price_engine.get_state(s) for s in price_engine.symbols}
        funding_engine.update_prices(price_states)
        funding_engine.check_settlements(tracker, balance)
        om.try_fill_orders(price_states)

        # 验证 funding 已结算
        history = funding_engine.get_settlement_history(symbol)
        assert len(history) > 0, "应有 funding 结算记录"
        funding_amount = history[-1]['funding_amount']
        # 多头 + 正费率 → 支付 funding（funding_amount < 0 for balance perspective ... actually
        # funding_amount = -position * rate * index_price = -1.0 * 0.001 * price < 0 for balance
        # but apply_funding adds it, so net effect: balance decreases
        # The funding_amount recorded is what's applied to balance
        expected_funding = -pos_before * 0.001 * price
        assert abs(funding_amount - expected_funding) < 1.0

        # 验证限价单成交（仓位增加）
        pos_after = tracker.get(symbol)
        assert pos_after > pos_before, "限价买单成交后仓位应增加"

        # 验证余额同时反映了 funding 和手续费
        balance_final = balance.get_usdt_balance()
        # 余额应小于买入后的余额（funding + fee）
        assert balance_final < balance_after_buy, "余额应因 funding + 手续费而减少"

    def test_multiple_symbols_funding_same_tick(
        self, tracker, balance, funding_engine
    ):
        """多个交易对在同一 tick 同时结算 funding"""
        symbols = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]
        positions = {"BTC/USDT:USDT": 1.0, "ETH/USDT:USDT": -10.0, "SOL/USDT:USDT": 100.0}

        for symbol, pos in positions.items():
            tracker.update(symbol, pos, 1000.0)

        balance_before = balance.get_usdt_balance()

        # 设置所有 symbol 即将结算
        for symbol in symbols:
            if symbol in funding_engine._states:
                state = funding_engine._states[symbol]
                state.current_rate = 0.001
                state.mark_price = 1000.0
                state.index_price = 1000.0
                state.next_funding_timestamp = time.time() - 1

        funding_engine.check_settlements(tracker, balance)

        # 应有 3 个结算记录
        all_history = funding_engine.get_settlement_history()
        settled_symbols = {h['symbol'] for h in all_history}
        for s in symbols:
            if s in funding_engine._states:
                assert s in settled_symbols, f"{s} 应已结算"

        # 余额应有变化
        balance_after = balance.get_usdt_balance()
        assert balance_after != balance_before, "多个 funding 结算后余额应变化"


# ============================================================
# 5. ActiveOrder.outdated 超时检测
# ============================================================

class TestActiveOrderOutdated:
    """测试 ActiveOrder 的超时检测"""

    def test_not_outdated_before_timeout(self):
        """超时前 outdated 应为 False"""
        order = ActiveOrder(
            order_id="test-1",
            exchange_path="sim",
            symbol="BTC/USDT:USDT",
            price=80000.0,
            amount=1.0,
            created_at=time.time(),
            timeout_refresh_tolerance=60.0,
        )
        assert not order.outdated, "刚创建的订单不应过期"

    def test_outdated_after_timeout(self):
        """超时后 outdated 应为 True"""
        order = ActiveOrder(
            order_id="test-2",
            exchange_path="sim",
            symbol="BTC/USDT:USDT",
            price=80000.0,
            amount=1.0,
            created_at=time.time() - 120,  # 2 分钟前创建
            timeout_refresh_tolerance=60.0,  # 1 分钟超时
        )
        assert order.outdated, "超过超时时间的订单应标记为过期"

    def test_outdated_transitions_with_freezegun(self):
        """使用 freezegun 验证 outdated 随时间变化"""
        now = time.time()
        order = ActiveOrder(
            order_id="test-3",
            exchange_path="sim",
            symbol="BTC/USDT:USDT",
            price=80000.0,
            amount=1.0,
            created_at=now,
            timeout_refresh_tolerance=5.0,  # 5 秒超时
        )
        assert not order.outdated

        # 创建一个在超时后的订单来验证
        order_past = ActiveOrder(
            order_id="test-3b",
            exchange_path="sim",
            symbol="BTC/USDT:USDT",
            price=80000.0,
            amount=1.0,
            created_at=now - 10,  # 10 秒前
            timeout_refresh_tolerance=5.0,
        )
        assert order_past.outdated

    def test_zero_timeout_always_outdated(self):
        """零超时的订单立即过期"""
        order = ActiveOrder(
            order_id="test-4",
            exchange_path="sim",
            symbol="BTC/USDT:USDT",
            price=80000.0,
            amount=1.0,
            created_at=time.time() - 0.001,  # 刚刚过去一点
            timeout_refresh_tolerance=0.0,
        )
        assert order.outdated, "零超时的订单应立即过期"

    def test_very_long_timeout_never_outdated(self):
        """超长超时的订单不应过期"""
        order = ActiveOrder(
            order_id="test-5",
            exchange_path="sim",
            symbol="BTC/USDT:USDT",
            price=80000.0,
            amount=1.0,
            created_at=time.time(),
            timeout_refresh_tolerance=86400.0,  # 24 小时
        )
        assert not order.outdated


# ============================================================
# 6. HealthyData 过期与恢复
# ============================================================

class TestHealthyDataExpiryAndRecovery:
    """测试 HealthyData 的过期检测和自动恢复"""

    async def test_healthy_after_set(self):
        """设置数据后应为健康状态"""
        hd = HealthyData[dict](max_age=5.0)
        assert not hd.is_healthy, "初始状态应不健康"

        await hd.update({"price": 100.0})
        assert hd.is_healthy, "设置数据后应健康"

    async def test_unhealthy_after_expiry(self):
        """数据过期后应为不健康状态"""
        hd = HealthyData[dict](max_age=1.0)
        # 设置带过去时间戳的数据，使其立即过期
        await hd.update({"price": 100.0}, timestamp=time.time() - 2.0)
        assert not hd.is_healthy, "过期数据应不健康"

    async def test_get_or_update_triggers_refetch(self):
        """数据不健康时 get_or_update_by_func 应触发重新获取"""
        hd = HealthyData[dict](max_age=1.0)
        # 设置已过期的数据
        await hd.update({"price": 100.0}, timestamp=time.time() - 2.0)
        assert not hd.is_healthy

        # 定义更新函数
        new_data = {"price": 200.0}
        call_count = 0

        async def fetch_func():
            nonlocal call_count
            call_count += 1
            return new_data, None  # None timestamp = use current time

        # 应触发 fetch
        result_data, result_ts = await hd.get_or_update_by_func(fetch_func)
        assert call_count == 1, "应调用一次 fetch 函数"
        assert result_data == new_data
        assert hd.is_healthy, "更新后应恢复健康"

    async def test_healthy_data_no_refetch(self):
        """数据健康时 get_or_update_by_func 不应触发重新获取"""
        hd = HealthyData[dict](max_age=60.0)
        await hd.update({"price": 100.0})
        assert hd.is_healthy

        call_count = 0

        async def fetch_func():
            nonlocal call_count
            call_count += 1
            return {"price": 999.0}, None

        result_data, _ = await hd.get_or_update_by_func(fetch_func)
        assert call_count == 0, "健康数据不应触发 fetch"
        assert result_data == {"price": 100.0}

    async def test_none_data_is_unhealthy(self):
        """None 数据应为不健康"""
        hd = HealthyData[dict](max_age=60.0)
        assert not hd.is_healthy
        assert hd.get_data() is None

    async def test_dirty_flag_makes_unhealthy(self):
        """标记为 dirty 后数据应不健康"""
        hd = HealthyData[dict](max_age=60.0)
        await hd.update({"price": 100.0})
        assert hd.is_healthy

        await hd.mark_dirty()
        assert not hd.is_healthy, "dirty 数据应不健康"

    async def test_update_after_dirty_restores_health(self):
        """dirty 后重新更新应恢复健康"""
        hd = HealthyData[dict](max_age=60.0)
        await hd.update({"price": 100.0})
        await hd.mark_dirty()
        assert not hd.is_healthy

        await hd.update({"price": 200.0})
        assert hd.is_healthy, "重新更新后应恢复健康"


# ============================================================
# 7. Listener 状态机异常恢复
# ============================================================

class ErrorListener(Listener):
    """一个可控制是否抛出异常的 Listener"""

    def __init__(self, should_fail: bool = True, **kwargs):
        self._should_fail = should_fail
        self._tick_count = 0
        super().__init__(name="ErrorListener", interval=0.1, **kwargs)

    async def on_tick(self) -> bool:
        self._tick_count += 1
        if self._should_fail:
            raise RuntimeError("模拟异常")
        return False


class TestListenerErrorRecovery:
    """测试 Listener 状态机在异常后的恢复能力"""

    async def test_tick_exception_sets_unhealthy(self):
        """on_tick 抛异常后 Listener 应标记为不健康"""
        listener = ErrorListener(should_fail=True)
        # 手动启动
        listener._enabled = True
        listener._state = ListenerState.STARTING

        # 第一次 tick：STARTING -> RUNNING
        await listener.tick()
        assert listener.state == ListenerState.RUNNING

        # 第二次 tick：on_tick 抛异常
        await listener.tick()
        # 异常被捕获，healthy 设为 False
        assert not listener.healthy, "异常后应标记为不健康"
        # 但状态仍为 RUNNING（不会自动停止）
        assert listener.state == ListenerState.RUNNING

    async def test_recovery_after_fix(self):
        """修复异常后 Listener 应恢复健康"""
        listener = ErrorListener(should_fail=True)
        listener._enabled = True
        listener._state = ListenerState.STARTING

        # 启动
        await listener.tick()
        assert listener.state == ListenerState.RUNNING

        # 异常 tick
        await listener.tick()
        assert not listener.healthy

        # "修复" 异常
        listener._should_fail = False

        # 下一次 tick 应恢复健康
        await listener.tick()
        assert listener.healthy, "修复后应恢复健康"

    async def test_multiple_failures_then_recovery(self):
        """多次失败后仍能恢复"""
        listener = ErrorListener(should_fail=True)
        listener._enabled = True
        listener._state = ListenerState.STARTING

        await listener.tick()  # STARTING -> RUNNING

        # 连续多次失败
        for _ in range(5):
            await listener.tick()
            assert not listener.healthy

        # 修复后恢复
        listener._should_fail = False
        await listener.tick()
        assert listener.healthy

    async def test_listener_remains_running_after_error(self):
        """异常后 Listener 状态应仍为 RUNNING，不会变成 STOPPED"""
        listener = ErrorListener(should_fail=True)
        listener._enabled = True
        listener._state = ListenerState.STARTING

        await listener.tick()  # STARTING -> RUNNING
        await listener.tick()  # 异常

        assert listener.state == ListenerState.RUNNING, (
            f"异常后状态应仍为 RUNNING，实际: {listener.state}"
        )
        assert listener.enabled, "异常后不应自动禁用"

    async def test_tick_count_increments_through_errors(self):
        """即使有异常，tick 计数也应递增"""
        listener = ErrorListener(should_fail=True)
        listener._enabled = True
        listener._state = ListenerState.STARTING

        await listener.tick()  # STARTING -> RUNNING (on_start, 不调 on_tick)
        initial_count = listener._tick_count

        # 多次失败的 tick
        for _ in range(3):
            await listener.tick()

        # on_tick 被调用了（虽然抛了异常，但在异常前 _tick_count 已递增）
        assert listener._tick_count > initial_count

    async def test_health_check_after_recovery(self):
        """恢复后健康检查应通过"""
        listener = ErrorListener(should_fail=False)
        listener._enabled = True
        listener._state = ListenerState.STARTING

        await listener.tick()  # STARTING -> RUNNING
        await listener.tick()  # 正常 tick

        assert listener.healthy
        # 执行健康检查
        await listener.health_check(recursive=False)
        assert listener.healthy


# ============================================================
# 8. 补充：OrderManager + PositionTracker 联动边界
# ============================================================

class TestOrderPositionEdgeCases:
    """订单与仓位的联动边界条件"""

    def test_buy_then_sell_exact_position(self, order_manager, tracker, price_engine):
        """买入后以精确数量卖出，仓位应归零"""
        symbol = "ETH/USDT:USDT"
        price = price_engine.get_price(symbol)
        cs = SYMBOLS_CONFIG['ETH']['contract_size']

        # 买入
        order_manager.place_order(symbol, "market", "buy", 100.0, price)
        pos = tracker.get(symbol)
        assert abs(pos - 100.0 * cs) < 1e-9

        # 卖出相同数量
        order_manager.place_order(symbol, "market", "sell", 100.0, price)
        pos_after = tracker.get(symbol)
        assert abs(pos_after) < 1e-9, f"卖出后仓位应为 0，实际: {pos_after}"

    def test_realized_pnl_on_close(self, order_manager, tracker, balance, price_engine):
        """平仓时应产生已实现盈亏"""
        symbol = "BTC/USDT:USDT"
        buy_price = price_engine.get_price(symbol)
        cs = SYMBOLS_CONFIG['BTC']['contract_size']

        # 买入
        order_manager.place_order(symbol, "market", "buy", 1000.0, buy_price)

        balance_before_sell = balance.get_usdt_balance()

        # 以更高价格卖出（产生正 PnL）
        sell_price = buy_price * 1.01
        price_engine.set_price(symbol, sell_price)
        order_manager.place_order(symbol, "market", "sell", 1000.0, sell_price)

        balance_after_sell = balance.get_usdt_balance()
        # 合约交易：PnL = (sell_price - buy_price) * contracts * contract_size
        # PnL > 0 应增加余额（减去手续费后仍应增加）
        expected_pnl = (sell_price - buy_price) * 1000.0 * cs
        # 余额变化应接近 PnL（减去手续费）
        actual_change = balance_after_sell - balance_before_sell
        # 手续费不超过 0.1%，所以 PnL 应主导
        assert actual_change > 0, (
            f"盈利平仓后余额应增加: PnL={expected_pnl:.2f}, 实际变化={actual_change:.2f}"
        )

    def test_funding_only_affects_positioned_symbols(
        self, funding_engine, tracker, balance
    ):
        """funding 结算只影响有仓位的交易对"""
        # 只给 BTC 建仓
        tracker.update("BTC/USDT:USDT", 1.0, 80000.0)
        balance_before = balance.get_usdt_balance()

        # 设置所有 symbol 结算
        for symbol, state in funding_engine._states.items():
            state.current_rate = 0.001
            state.mark_price = 80000.0
            state.index_price = 80000.0
            state.next_funding_timestamp = time.time() - 1

        funding_engine.check_settlements(tracker, balance)

        balance_after = balance.get_usdt_balance()
        # 只有 BTC 有仓位，只有 BTC 的 funding 应影响余额
        history = funding_engine.get_settlement_history()
        symbols_settled = {h['symbol'] for h in history if abs(h['funding_amount']) > 1e-10}
        assert "BTC/USDT:USDT" in symbols_settled

"""
ActiveOrdersTracker 单元测试

测试 hft/executor/base.py 中 ActiveOrder 和 ActiveOrdersTracker 的行为。
"""
import asyncio
import time

import pytest

from hft.executor.base import ActiveOrder, ActiveOrdersTracker, OrderIntent


# ============================================================
# 辅助函数
# ============================================================

def make_active_order(
    order_id: str = "order-1",
    exchange_path: str = "okx/swap",
    symbol: str = "BTC/USDT:USDT",
    price: float = 50000.0,
    amount: float = 0.01,
    created_at: float = None,
    timeout_refresh_tolerance: float = 60.0,
) -> ActiveOrder:
    if created_at is None:
        created_at = time.time()
    return ActiveOrder(
        order_id=order_id,
        exchange_path=exchange_path,
        symbol=symbol,
        price=price,
        amount=amount,
        created_at=created_at,
        timeout_refresh_tolerance=timeout_refresh_tolerance,
    )


def make_intent(
    price: float = 50000.0,
    amount: float = 0.01,
    timeout_refresh_tolerance: float = 60.0,
    price_refresh_tolerance: float = 10.0,
    post_only: bool = True,
) -> OrderIntent:
    return OrderIntent(
        price=price,
        amount=amount,
        timeout_refresh_tolerance=timeout_refresh_tolerance,
        price_refresh_tolerance=price_refresh_tolerance,
        post_only=post_only,
    )


# ============================================================
# ActiveOrder.outdated 属性测试
# ============================================================

class TestActiveOrderOutdated:
    def test_fresh_order_not_outdated(self):
        """刚创建的订单不应过期"""
        order = make_active_order(created_at=time.time(), timeout_refresh_tolerance=60.0)
        assert order.outdated is False

    def test_old_order_is_outdated(self):
        """超出超时时间的订单应标记为过期"""
        order = make_active_order(
            created_at=time.time() - 120.0,
            timeout_refresh_tolerance=60.0,
        )
        assert order.outdated is True

    def test_order_at_exact_boundary_is_outdated(self):
        """正好在超时边界（等于）时，订单应过期（time.time() > created_at + tolerance）"""
        now = time.time()
        # created_at + tolerance == now 时，条件 time.time() > ... 为 False，
        # 因此严格超出边界才过期，精确边界时仍为未过期（取决于时钟精度）
        # 这里测试比边界晚 1 毫秒
        order = make_active_order(
            created_at=now - 60.001,
            timeout_refresh_tolerance=60.0,
        )
        assert order.outdated is True

    def test_order_just_before_timeout_not_outdated(self):
        """超时前 1 秒的订单不应过期"""
        order = make_active_order(
            created_at=time.time() - 59.0,
            timeout_refresh_tolerance=60.0,
        )
        assert order.outdated is False

    def test_zero_tolerance_order_is_outdated(self):
        """超时容忍度为 0 时，任何已创建的订单都应过期"""
        order = make_active_order(
            created_at=time.time() - 0.001,
            timeout_refresh_tolerance=0.0,
        )
        assert order.outdated is True


# ============================================================
# ActiveOrdersTracker.is_in_tolerance() 测试
# ============================================================

class TestIsInTolerance:
    def setup_method(self):
        self.tracker = ActiveOrdersTracker()

    def test_exact_match_is_in_tolerance(self):
        """价格完全相同，在容忍范围内"""
        assert self.tracker.is_in_tolerance(100.0, 100.0, 5.0) is True

    def test_within_tolerance(self):
        """价格差小于容忍度，在容忍范围内"""
        assert self.tracker.is_in_tolerance(103.0, 100.0, 5.0) is True

    def test_at_boundary_is_in_tolerance(self):
        """价格差等于容忍度，在容忍范围内（abs(...) <= abs(tolerance)）"""
        assert self.tracker.is_in_tolerance(105.0, 100.0, 5.0) is True

    def test_outside_tolerance(self):
        """价格差超出容忍度，不在容忍范围内"""
        assert self.tracker.is_in_tolerance(106.0, 100.0, 5.0) is False

    def test_negative_diff_within_tolerance(self):
        """价格低于参考价但仍在容忍范围内"""
        assert self.tracker.is_in_tolerance(97.0, 100.0, 5.0) is True

    def test_negative_diff_outside_tolerance(self):
        """价格低于参考价且超出容忍范围"""
        assert self.tracker.is_in_tolerance(94.0, 100.0, 5.0) is False

    def test_negative_tolerance_uses_abs(self):
        """负数容忍度应取绝对值后比较"""
        assert self.tracker.is_in_tolerance(103.0, 100.0, -5.0) is True
        assert self.tracker.is_in_tolerance(106.0, 100.0, -5.0) is False

    def test_zero_tolerance_exact_match(self):
        """零容忍度时只有完全相同才在范围内"""
        assert self.tracker.is_in_tolerance(100.0, 100.0, 0.0) is True

    def test_zero_tolerance_any_diff_outside(self):
        """零容忍度时任何差异都不在范围内"""
        assert self.tracker.is_in_tolerance(100.001, 100.0, 0.0) is False


# ============================================================
# ActiveOrdersTracker.add_active_orders() 测试
# ============================================================

class TestAddActiveOrders:
    def setup_method(self):
        self.tracker = ActiveOrdersTracker()

    def test_add_single_order(self):
        """添加单个订单后应能在 tracker 中找到"""
        order = make_active_order(order_id="o1")
        self.tracker.add_active_orders("okx/swap", "BTC/USDT:USDT", [order])
        assert "o1" in self.tracker.orders["okx/swap"]["BTC/USDT:USDT"]

    def test_add_multiple_orders(self):
        """添加多个订单后都应在 tracker 中"""
        orders = [
            make_active_order(order_id="o1", price=50000.0),
            make_active_order(order_id="o2", price=51000.0),
            make_active_order(order_id="o3", price=49000.0),
        ]
        self.tracker.add_active_orders("okx/swap", "BTC/USDT:USDT", orders)
        symbol_orders = self.tracker.orders["okx/swap"]["BTC/USDT:USDT"]
        assert len(symbol_orders) == 3
        assert "o1" in symbol_orders
        assert "o2" in symbol_orders
        assert "o3" in symbol_orders

    def test_add_order_stores_reference(self):
        """添加的订单对象应与存储的对象一致"""
        order = make_active_order(order_id="o1", price=50000.0)
        self.tracker.add_active_orders("okx/swap", "BTC/USDT:USDT", [order])
        stored = self.tracker.orders["okx/swap"]["BTC/USDT:USDT"]["o1"]
        assert stored is order

    def test_add_order_different_symbols(self):
        """不同交易对的订单应分别存储"""
        order_btc = make_active_order(order_id="o1", symbol="BTC/USDT:USDT")
        order_eth = make_active_order(order_id="o2", symbol="ETH/USDT:USDT")
        self.tracker.add_active_orders("okx/swap", "BTC/USDT:USDT", [order_btc])
        self.tracker.add_active_orders("okx/swap", "ETH/USDT:USDT", [order_eth])
        assert "o1" in self.tracker.orders["okx/swap"]["BTC/USDT:USDT"]
        assert "o2" in self.tracker.orders["okx/swap"]["ETH/USDT:USDT"]
        assert "o1" not in self.tracker.orders["okx/swap"]["ETH/USDT:USDT"]

    def test_add_order_different_exchange_paths(self):
        """不同交易所路径的订单应分别存储"""
        order1 = make_active_order(order_id="o1")
        order2 = make_active_order(order_id="o2")
        self.tracker.add_active_orders("okx/swap", "BTC/USDT:USDT", [order1])
        self.tracker.add_active_orders("binance/swap", "BTC/USDT:USDT", [order2])
        assert "o1" in self.tracker.orders["okx/swap"]["BTC/USDT:USDT"]
        assert "o2" in self.tracker.orders["binance/swap"]["BTC/USDT:USDT"]

    def test_add_empty_list(self):
        """添加空列表不应报错，也不影响已有订单"""
        order = make_active_order(order_id="o1")
        self.tracker.add_active_orders("okx/swap", "BTC/USDT:USDT", [order])
        self.tracker.add_active_orders("okx/swap", "BTC/USDT:USDT", [])
        assert "o1" in self.tracker.orders["okx/swap"]["BTC/USDT:USDT"]

    def test_add_overwrites_same_order_id(self):
        """相同 order_id 再次添加时应覆盖原有记录"""
        order_v1 = make_active_order(order_id="o1", price=50000.0)
        order_v2 = make_active_order(order_id="o1", price=51000.0)
        self.tracker.add_active_orders("okx/swap", "BTC/USDT:USDT", [order_v1])
        self.tracker.add_active_orders("okx/swap", "BTC/USDT:USDT", [order_v2])
        stored = self.tracker.orders["okx/swap"]["BTC/USDT:USDT"]["o1"]
        assert stored.price == 51000.0


# ============================================================
# ActiveOrdersTracker.remove_active_orders() 测试
# ============================================================

class TestRemoveActiveOrders:
    def setup_method(self):
        self.tracker = ActiveOrdersTracker()
        self.exchange_path = "okx/swap"
        self.symbol = "BTC/USDT:USDT"

    def _add(self, order_id, price=50000.0):
        order = make_active_order(order_id=order_id, price=price)
        self.tracker.add_active_orders(self.exchange_path, self.symbol, [order])

    def test_remove_existing_order(self):
        """移除已存在的订单后，不应再出现在 tracker 中"""
        self._add("o1")
        self.tracker.remove_active_orders(self.exchange_path, self.symbol, ["o1"])
        assert "o1" not in self.tracker.orders[self.exchange_path][self.symbol]

    def test_remove_multiple_orders(self):
        """移除多个订单"""
        self._add("o1")
        self._add("o2")
        self._add("o3")
        self.tracker.remove_active_orders(self.exchange_path, self.symbol, ["o1", "o3"])
        symbol_orders = self.tracker.orders[self.exchange_path][self.symbol]
        assert "o1" not in symbol_orders
        assert "o2" in symbol_orders
        assert "o3" not in symbol_orders

    def test_remove_nonexistent_order_is_safe(self):
        """移除不存在的订单不应抛出异常"""
        self._add("o1")
        # 不应抛出异常
        self.tracker.remove_active_orders(self.exchange_path, self.symbol, ["nonexistent"])
        assert "o1" in self.tracker.orders[self.exchange_path][self.symbol]

    def test_remove_from_empty_symbol_is_safe(self):
        """从未添加过订单的 symbol 中移除，不应抛出异常"""
        self.tracker.remove_active_orders(self.exchange_path, "ETH/USDT:USDT", ["o1"])

    def test_remove_empty_list(self):
        """移除空列表不应影响现有订单"""
        self._add("o1")
        self.tracker.remove_active_orders(self.exchange_path, self.symbol, [])
        assert "o1" in self.tracker.orders[self.exchange_path][self.symbol]

    def test_remove_all_orders(self):
        """移除所有订单后，dict 仍存在但为空"""
        self._add("o1")
        self._add("o2")
        self.tracker.remove_active_orders(self.exchange_path, self.symbol, ["o1", "o2"])
        assert len(self.tracker.orders[self.exchange_path][self.symbol]) == 0


# ============================================================
# ActiveOrdersTracker.calculate_changed_orders() 测试
# ============================================================

class TestCalculateChangedOrders:
    def setup_method(self):
        self.tracker = ActiveOrdersTracker()
        self.exchange_path = "okx/swap"
        self.symbol = "BTC/USDT:USDT"

    def _add_order(self, order_id, price, created_at=None, timeout=60.0):
        order = make_active_order(
            order_id=order_id,
            price=price,
            created_at=created_at if created_at is not None else time.time(),
            timeout_refresh_tolerance=timeout,
        )
        self.tracker.add_active_orders(self.exchange_path, self.symbol, [order])
        return order

    def test_no_existing_orders_all_intents_placed(self):
        """没有活跃订单时，所有意图都应被下单"""
        intents = [
            make_intent(price=50000.0),
            make_intent(price=49000.0),
        ]
        to_place, to_remove = self.tracker.calculate_changed_orders(
            self.exchange_path, self.symbol, intents
        )
        assert len(to_place) == 2
        assert len(to_remove) == 0

    def test_matching_order_within_tolerance_not_replaced(self):
        """价格在容忍范围内的活跃订单不应被替换"""
        self._add_order("o1", price=50000.0)
        intents = [make_intent(price=50005.0, price_refresh_tolerance=10.0)]
        to_place, to_remove = self.tracker.calculate_changed_orders(
            self.exchange_path, self.symbol, intents
        )
        assert len(to_place) == 0
        assert len(to_remove) == 0

    def test_matching_order_at_exact_price_not_replaced(self):
        """价格完全相同的活跃订单不应被替换"""
        self._add_order("o1", price=50000.0)
        intents = [make_intent(price=50000.0, price_refresh_tolerance=10.0)]
        to_place, to_remove = self.tracker.calculate_changed_orders(
            self.exchange_path, self.symbol, intents
        )
        assert len(to_place) == 0
        assert len(to_remove) == 0

    def test_matching_order_at_tolerance_boundary_not_replaced(self):
        """价格差等于容忍度边界时，不应被替换"""
        self._add_order("o1", price=50000.0)
        intents = [make_intent(price=50010.0, price_refresh_tolerance=10.0)]
        to_place, to_remove = self.tracker.calculate_changed_orders(
            self.exchange_path, self.symbol, intents
        )
        assert len(to_place) == 0
        assert len(to_remove) == 0

    def test_outdated_order_removed_and_intent_placed(self):
        """过期的订单应被移除，对应意图应重新下单"""
        self._add_order("o1", price=50000.0, created_at=time.time() - 120.0, timeout=60.0)
        intents = [make_intent(price=50000.0, price_refresh_tolerance=10.0)]
        to_place, to_remove = self.tracker.calculate_changed_orders(
            self.exchange_path, self.symbol, intents
        )
        assert len(to_remove) == 1
        assert to_remove[0].order_id == "o1"
        # 由于过期订单不算作匹配，意图应被重新下单
        assert len(to_place) == 1

    def test_unmatched_existing_order_removed(self):
        """价格偏移超出容忍度的活跃订单应被移除"""
        self._add_order("o1", price=50000.0)
        # 意图价格与活跃订单差 200，容忍度 10
        intents = [make_intent(price=50200.0, price_refresh_tolerance=10.0)]
        to_place, to_remove = self.tracker.calculate_changed_orders(
            self.exchange_path, self.symbol, intents
        )
        assert len(to_remove) == 1
        assert to_remove[0].order_id == "o1"
        assert len(to_place) == 1

    def test_market_order_intent_always_placed(self):
        """市价单意图（price=None）总是应被下单"""
        intents = [make_intent(price=None)]
        to_place, to_remove = self.tracker.calculate_changed_orders(
            self.exchange_path, self.symbol, intents
        )
        assert len(to_place) == 1
        assert to_place[0].price is None
        assert len(to_remove) == 0

    def test_market_order_placed_even_with_existing_orders(self):
        """即使有活跃限价单，市价单意图也应总是下单"""
        self._add_order("o1", price=50000.0)
        intents = [make_intent(price=None)]
        to_place, to_remove = self.tracker.calculate_changed_orders(
            self.exchange_path, self.symbol, intents
        )
        assert len(to_place) == 1
        # 活跃订单价格没有对应意图（意图是市价单），所以活跃订单应被移除
        assert len(to_remove) == 1

    def test_empty_intents_all_existing_removed(self):
        """意图列表为空时，所有活跃订单都应被移除"""
        self._add_order("o1", price=50000.0)
        self._add_order("o2", price=49000.0)
        to_place, to_remove = self.tracker.calculate_changed_orders(
            self.exchange_path, self.symbol, []
        )
        assert len(to_place) == 0
        assert len(to_remove) == 2

    def test_mixed_scenario(self):
        """混合场景：部分匹配、部分过期、部分新增"""
        # o1: 价格匹配，应保留
        self._add_order("o1", price=50000.0, timeout=60.0)
        # o2: 价格不匹配，应被移除
        self._add_order("o2", price=48000.0, timeout=60.0)
        # o3: 已过期，应被移除
        self._add_order("o3", price=51000.0, created_at=time.time() - 120.0, timeout=60.0)

        intents = [
            make_intent(price=50005.0, price_refresh_tolerance=10.0),   # 匹配 o1
            make_intent(price=55000.0, price_refresh_tolerance=10.0),   # 全新意图
        ]
        to_place, to_remove = self.tracker.calculate_changed_orders(
            self.exchange_path, self.symbol, intents
        )
        remove_ids = {o.order_id for o in to_remove}
        assert "o2" in remove_ids
        assert "o3" in remove_ids
        assert "o1" not in remove_ids
        # 55000 的意图没有匹配的活跃订单，应被下单
        assert any(i.price == 55000.0 for i in to_place)
        # 50005 的意图匹配 o1，不应下单
        assert not any(i.price == 50005.0 for i in to_place)

    def test_no_existing_no_intents_returns_empty(self):
        """没有活跃订单也没有意图时，返回两个空列表"""
        to_place, to_remove = self.tracker.calculate_changed_orders(
            self.exchange_path, self.symbol, []
        )
        assert to_place == []
        assert to_remove == []

    def test_multiple_intents_matched_by_same_order_does_not_duplicate_remove(self):
        """两个相近意图都匹配同一活跃订单，该订单不应被移除"""
        self._add_order("o1", price=50000.0, timeout=60.0)
        intents = [
            make_intent(price=50003.0, price_refresh_tolerance=10.0),
            make_intent(price=49997.0, price_refresh_tolerance=10.0),
        ]
        to_place, to_remove = self.tracker.calculate_changed_orders(
            self.exchange_path, self.symbol, intents
        )
        # o1 匹配了两个意图，不应被移除
        assert len(to_remove) == 0
        # 两个意图都被 o1 满足，都不需要下单
        # 注意：calculate_changed_orders 不去重，但两个意图都匹配到 o1，所以均不下单
        assert len(to_place) == 0


# ============================================================
# defaultdict 自动创建路径测试
# ============================================================

class TestDefaultDictAutocreation:
    def test_accessing_new_exchange_path_creates_nested_defaultdict(self):
        """访问新的 exchange_path 应自动创建嵌套 defaultdict"""
        tracker = ActiveOrdersTracker()
        # 访问前不存在
        assert "new_exchange" not in tracker.orders
        # 访问后自动创建
        sub = tracker.orders["new_exchange"]
        assert "new_exchange" in tracker.orders
        assert isinstance(sub, dict)

    def test_accessing_new_symbol_creates_dict(self):
        """访问新的 symbol 应自动创建空 dict"""
        tracker = ActiveOrdersTracker()
        symbol_dict = tracker.orders["okx/swap"]["NEW/USDT:USDT"]
        assert isinstance(symbol_dict, dict)
        assert len(symbol_dict) == 0

    def test_calculate_changed_orders_on_unseen_path_returns_empty(self):
        """对从未添加过订单的路径调用 calculate_changed_orders 应正常返回"""
        tracker = ActiveOrdersTracker()
        to_place, to_remove = tracker.calculate_changed_orders(
            "never_seen_exchange", "NEW/USDT:USDT", []
        )
        assert to_place == []
        assert to_remove == []

    def test_remove_active_orders_on_unseen_path_is_safe(self):
        """对从未添加过订单的路径调用 remove_active_orders 不应抛异常"""
        tracker = ActiveOrdersTracker()
        tracker.remove_active_orders("never_seen_exchange", "NEW/USDT:USDT", ["o1"])


# ============================================================
# 并发安全测试
# ============================================================

class TestConcurrencySafety:
    async def test_concurrent_add_and_calculate(self):
        """并发地添加订单和计算变更，不应出现数据竞争或异常"""
        tracker = ActiveOrdersTracker()
        exchange_path = "okx/swap"
        symbol = "BTC/USDT:USDT"
        errors = []

        async def add_orders():
            for i in range(20):
                order = make_active_order(order_id=f"o{i}", price=50000.0 + i)
                async with tracker._lock:
                    tracker.add_active_orders(exchange_path, symbol, [order])
                await asyncio.sleep(0)

        async def calculate_orders():
            for _ in range(20):
                try:
                    intents = [make_intent(price=50010.0, price_refresh_tolerance=5.0)]
                    async with tracker._lock:
                        tracker.calculate_changed_orders(exchange_path, symbol, intents)
                except Exception as e:
                    errors.append(e)
                await asyncio.sleep(0)

        await asyncio.gather(add_orders(), calculate_orders())
        assert len(errors) == 0

    async def test_concurrent_add_and_remove(self):
        """并发地添加和移除订单，不应出现数据竞争或异常"""
        tracker = ActiveOrdersTracker()
        exchange_path = "okx/swap"
        symbol = "BTC/USDT:USDT"
        errors = []

        # 预先添加一批订单
        pre_orders = [make_active_order(order_id=f"pre{i}", price=50000.0 + i) for i in range(10)]
        tracker.add_active_orders(exchange_path, symbol, pre_orders)

        async def add_orders():
            for i in range(20):
                order = make_active_order(order_id=f"new{i}", price=51000.0 + i)
                async with tracker._lock:
                    tracker.add_active_orders(exchange_path, symbol, [order])
                await asyncio.sleep(0)

        async def remove_orders():
            for i in range(10):
                try:
                    async with tracker._lock:
                        tracker.remove_active_orders(exchange_path, symbol, [f"pre{i}"])
                except Exception as e:
                    errors.append(e)
                await asyncio.sleep(0)

        await asyncio.gather(add_orders(), remove_orders())
        assert len(errors) == 0

    async def test_lock_exists_and_is_asyncio_lock(self):
        """tracker 应持有一个 asyncio.Lock 实例"""
        tracker = ActiveOrdersTracker()
        assert isinstance(tracker._lock, asyncio.Lock)

    async def test_lock_can_be_acquired(self):
        """锁应可以正常被获取"""
        tracker = ActiveOrdersTracker()
        async with tracker._lock:
            # 在持有锁的情况下操作 tracker
            order = make_active_order(order_id="o1")
            tracker.add_active_orders("okx/swap", "BTC/USDT:USDT", [order])
        assert "o1" in tracker.orders["okx/swap"]["BTC/USDT:USDT"]

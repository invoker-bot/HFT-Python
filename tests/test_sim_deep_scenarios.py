"""
SimulatedExchange 深度场景测试

覆盖真实运行中的复杂情况：
- top-5 切换（币对轮换）
- 币对下架
- 孤儿订单（切换后遗留的挂单）
- 仓位残留（切换后需要平仓）
- 极端价格场景
- 并发操作一致性
- 无限增长修剪验证
"""
import time
import pytest

from hft.exchange.simulated.engines.price import PriceEngine, SymbolPriceState
from hft.exchange.simulated.engines.funding import FundingEngine
from hft.exchange.simulated.engines.orders import OrderManager, SimulatedOrder
from hft.exchange.simulated.engines.positions import PositionTracker
from hft.exchange.simulated.engines.balance import BalanceTracker
from hft.exchange.simulated.markets import SYMBOLS_CONFIG, get_swap_symbols


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
def order_manager(tracker, balance):
    contract_sizes = {
        f"{base}/USDT:USDT": cfg['contract_size']
        for base, cfg in SYMBOLS_CONFIG.items()
    }
    return OrderManager(
        tracker, balance,
        fill_probability=1.0,
        contract_sizes=contract_sizes,
    )


@pytest.fixture
def price_engine():
    return PriceEngine(volatility=0.001, seed=42)


@pytest.fixture
def funding_engine():
    return FundingEngine(
        swap_symbols=get_swap_symbols(),
        base_rate=0.0001,
        interval_hours=8,
        seed=42,
    )


# ============================================================
# 1. Top-5 切换场景
# ============================================================

class TestTop5Rotation:
    """
    模拟策略从一组 top-5 切换到另一组 top-5 时的各种问题。
    真实场景：市场条件变化 → fair_price 偏离度排名改变 → 选中的 5 个币对变化。
    """

    def test_rotation_leaves_orphan_orders(self, order_manager, price_engine):
        """
        场景：旧 top-5 中有挂单，切换到新 top-5 后旧挂单成为孤儿。
        预期：孤儿订单仍在 _orders 中，需要显式取消。
        """
        om = order_manager
        old_top5 = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
                     "DOGE/USDT:USDT", "ADA/USDT:USDT"]
        new_top5 = ["BTC/USDT:USDT", "ETH/USDT:USDT", "ARB/USDT:USDT",
                     "OP/USDT:USDT", "SUI/USDT:USDT"]

        # 在旧 top-5 中挂限价单
        old_order_ids = {}
        for symbol in old_top5:
            price = price_engine.get_price(symbol)
            order = om.place_order(symbol, "limit", "buy", 10.0, price * 0.95)
            old_order_ids[symbol] = order['id']

        assert len(om._orders) == 5

        # 切换到新 top-5：被踢出的 3 个币对的订单成为孤儿
        removed = set(old_top5) - set(new_top5)  # SOL, DOGE, ADA
        assert len(removed) == 3

        # 孤儿订单仍然存在
        orphan_orders = [oid for sym, oid in old_order_ids.items() if sym in removed]
        for oid in orphan_orders:
            order = om.get_order(oid)
            assert order['status'] == 'open', f"孤儿订单 {oid} 状态应为 open"

        # 正确做法：切换时主动取消旧币对的订单
        for symbol in removed:
            om.cancel_order(old_order_ids[symbol])

        # 验证只剩新 top-5 中的共同部分
        remaining = om.get_open_orders()
        remaining_symbols = {o['symbol'] for o in remaining}
        assert remaining_symbols == {"BTC/USDT:USDT", "ETH/USDT:USDT"}

    def test_rotation_with_existing_positions(self, order_manager, price_engine, tracker):
        """
        场景：旧 top-5 中有持仓，切换后旧币对仓位需要平掉。
        预期：旧仓位不会自动平仓，需要显式下单。
        """
        om = order_manager
        old_top5 = ["SOL/USDT:USDT", "DOGE/USDT:USDT", "ADA/USDT:USDT",
                     "AVAX/USDT:USDT", "LINK/USDT:USDT"]

        # 在旧 top-5 中建立仓位
        for symbol in old_top5:
            price = price_engine.get_price(symbol)
            cs = SYMBOLS_CONFIG[symbol.split('/')[0]]['contract_size']
            contracts = 100.0 / (price * cs)  # ~$100 仓位
            om.place_order(symbol, "market", "buy", contracts, price)

        # 验证所有 5 个都有仓位
        for symbol in old_top5:
            assert tracker.get(symbol) > 0, f"{symbol} 应有多头仓位"

        # 切换到全新的 top-5
        new_top5 = ["BTC/USDT:USDT", "ETH/USDT:USDT", "ARB/USDT:USDT",
                     "OP/USDT:USDT", "SUI/USDT:USDT"]

        # 旧仓位仍然存在（不会自动平仓）
        for symbol in old_top5:
            assert tracker.get(symbol) > 0, f"切换后 {symbol} 仓位不应自动消失"

        # 正确做法：对被踢出的币对下平仓单
        removed = set(old_top5) - set(new_top5)
        for symbol in removed:
            pos = tracker.get(symbol)
            cs = SYMBOLS_CONFIG[symbol.split('/')[0]]['contract_size']
            contracts = pos / cs
            price = price_engine.get_price(symbol)
            om.place_order(symbol, "market", "sell", contracts, price)

        # 验证被踢出的币对仓位已平
        for symbol in removed:
            assert abs(tracker.get(symbol)) < 1e-9, f"{symbol} 仓位应已平"

    def test_rapid_rotation_back_and_forth(self, order_manager, price_engine, tracker):
        """
        场景：币对被踢出后又迅速回来（振荡）。
        预期：重新加入时仓位和订单状态正确。
        """
        om = order_manager
        symbol = "SOL/USDT:USDT"
        price = price_engine.get_price(symbol)
        cs = SYMBOLS_CONFIG['SOL']['contract_size']

        # 第 1 轮：建仓
        contracts = 100.0
        om.place_order(symbol, "market", "buy", contracts, price)
        pos_1 = tracker.get(symbol)
        assert pos_1 > 0

        # 模拟被踢出 → 不做任何操作（仓位保留）

        # 第 2 轮：重新被选中，策略再次下单
        om.place_order(symbol, "market", "buy", contracts, price)
        pos_2 = tracker.get(symbol)
        # 仓位应该是累加的
        assert abs(pos_2 - pos_1 * 2) < 1e-9, f"重新加入后仓位应累加: {pos_1} → {pos_2}"

    def test_rotation_partial_fill_then_cancel(self, order_manager, price_engine):
        """
        场景：限价单部分成交后，币对被踢出，需要取消剩余。
        预期：取消后 filled 部分保留，remaining 取消。
        """
        om = order_manager
        symbol = "ETH/USDT:USDT"
        price = price_engine.get_price(symbol)

        # 挂限价买单
        order = om.place_order(symbol, "limit", "buy", 100.0, price * 1.01)
        order_id = order['id']

        # 手动执行部分成交（30%）
        sim_order = om._orders[order_id]
        om._execute_fill(sim_order, 30.0, price)

        # 部分成交状态
        assert sim_order.filled == 30.0
        assert sim_order.status == 'open'

        # 币对被踢出 → 取消剩余
        result = om.cancel_order(order_id)
        assert result['status'] == 'canceled'
        assert result['filled'] == 30.0  # 已成交部分保留
        assert result['remaining'] == 70.0


# ============================================================
# 2. 币对下架场景
# ============================================================

class TestSymbolDelisting:
    """
    模拟交易对下架（从市场数据中消失）时的行为。
    真实场景：交易所公告某币对将下架，需要处理现有仓位和订单。
    """

    def test_orders_for_delisted_symbol(self, order_manager, price_engine):
        """
        场景：币对下架后，该币对的挂单无法成交。
        预期：try_fill_orders 跳过找不到 price_state 的订单。
        """
        om = order_manager
        symbol = "FLOKI/USDT:USDT"
        price = price_engine.get_price(symbol)

        # 挂限价单
        order = om.place_order(symbol, "limit", "buy", 1000.0, price * 1.01)
        assert order['status'] == 'open'

        # 模拟下架：price_states 中不包含该币对
        price_states = {
            s: price_engine.get_state(s) for s in price_engine.symbols
            if s != symbol
        }

        # try_fill 应该跳过（不报错）
        om.try_fill_orders(price_states)

        # 订单仍挂着
        assert len(om.get_open_orders(symbol)) == 1

    def test_position_remains_after_delisting(self, order_manager, price_engine, tracker):
        """
        场景：已有仓位的币对下架。
        预期：仓位数据保留，需要通过其他方式清理。
        """
        om = order_manager
        symbol = "BONK/USDT:USDT"
        price = price_engine.get_price(symbol)
        cs = SYMBOLS_CONFIG['BONK']['contract_size']

        # 建仓
        contracts = 10000.0
        om.place_order(symbol, "market", "buy", contracts, price)
        assert tracker.get(symbol) > 0

        # 模拟下架：从 symbols 中移除
        # 仓位数据仍然存在（不会自动清除）
        pos = tracker.get(symbol)
        assert pos > 0, "仓位不应因下架而消失"

        # 正确做法：下架前平仓
        om.place_order(symbol, "market", "sell", contracts, price)
        assert abs(tracker.get(symbol)) < 1e-9

    def test_cancel_all_orders_for_delisted_symbol(self, order_manager, price_engine):
        """
        场景：下架时批量取消该币对所有挂单。
        预期：所有挂单被取消，不报错。
        """
        om = order_manager
        symbol = "WIF/USDT:USDT"
        price = price_engine.get_price(symbol)

        # 挂多个限价单
        order_ids = []
        for i in range(5):
            order = om.place_order(symbol, "limit", "buy", 10.0, price * (0.9 + i * 0.01))
            order_ids.append(order['id'])

        assert len(om.get_open_orders(symbol)) == 5

        # 批量取消
        results = om.cancel_orders(order_ids, symbol)
        assert all(r['status'] == 'canceled' for r in results)
        assert len(om.get_open_orders(symbol)) == 0

    def test_delisted_symbol_funding_ignored(self, funding_engine, tracker, balance):
        """
        场景：下架的币对在 funding 结算时应跳过。
        预期：只结算仍存在的交易对。
        """
        fe = funding_engine

        # 给 FLOKI 建仓
        tracker.update("FLOKI/USDT:USDT", 1000.0)
        balance_before = balance.get_usdt_balance()

        # 模拟下架：从 _states 中移除
        fe._states.pop("FLOKI/USDT:USDT", None)

        # 触发结算
        for state in fe._states.values():
            state.next_funding_timestamp = time.time() - 1
            state.mark_price = 100.0
            state.index_price = 100.0
        fe.check_settlements(tracker, balance)

        # FLOKI 仓位不受影响（无结算）
        assert tracker.get("FLOKI/USDT:USDT") == 1000.0


# ============================================================
# 3. 极端价格与边界条件
# ============================================================

class TestExtremePriceScenarios:
    """
    模拟极端市场条件下的行为。
    """

    def test_zero_price_order(self, order_manager, tracker):
        """
        场景：价格为 0 的市价单。
        预期：订单成交但 cost=0，仓位更新正常。
        """
        om = order_manager
        order = om.place_order("BTC/USDT:USDT", "market", "buy", 1.0, 0.0)
        assert order['status'] == 'closed'
        assert order['cost'] == 0.0
        # 仓位仍更新（按合约张数）
        assert tracker.get("BTC/USDT:USDT") != 0

    def test_very_small_amount_order(self, order_manager, tracker):
        """
        场景：极小数量的订单。
        预期：成交后仓位反映极小变化。
        """
        om = order_manager
        om.place_order("BTC/USDT:USDT", "market", "buy", 0.001, 80000.0)
        pos = tracker.get("BTC/USDT:USDT")
        cs = SYMBOLS_CONFIG['BTC']['contract_size']
        expected = 0.001 * cs
        assert abs(pos - expected) < 1e-12

    def test_very_large_amount_order(self, order_manager, tracker, balance):
        """
        场景：超大数量的订单（可能导致余额为负）。
        预期：订单正常执行，余额可能变为负数（模拟环境不限制）。
        """
        om = order_manager
        # 下一个巨大的现货买单
        om.place_order("BTC/USDT", "market", "buy", 1000000.0, 80000.0)
        # 现货买入扣 USDT
        assert balance.get_usdt_balance() < 0  # 余额变负

    def test_price_exactly_at_limit_order(self, order_manager, price_engine):
        """
        场景：市价恰好等于限价单价格。
        预期：价格有利时应该能成交。
        """
        om = order_manager
        symbol = "BTC/USDT:USDT"
        mid = price_engine.get_price(symbol)

        # 挂买单恰好在当前 ask 价
        state = price_engine.get_state(symbol)
        ask = state.mid_price + state.mid_price * state.spread_bps / 10000
        order = om.place_order(symbol, "limit", "buy", 1.0, ask)

        # 多次尝试成交
        for _ in range(20):
            om.try_fill_orders({symbol: price_engine.get_state(symbol)})

        # 应该已成交
        open_orders = om.get_open_orders(symbol)
        assert len(open_orders) == 0, "ask 价的买单应该能成交"

    def test_negative_funding_rate(self, funding_engine, tracker, balance):
        """
        场景：负费率 + 多头 → 应该收取 funding。
        预期：余额增加。
        """
        fe = funding_engine
        symbol = "BTC/USDT:USDT"

        tracker.update(symbol, 1.0)  # 1 BTC 多头
        balance_before = balance.get_usdt_balance()

        state = fe._states[symbol]
        state.current_rate = -0.001  # 负费率
        state.mark_price = 80000.0
        state.index_price = 80000.0
        state.next_funding_timestamp = time.time() - 1

        fe.check_settlements(tracker, balance)

        balance_after = balance.get_usdt_balance()
        # 负费率 + 多头 → funding = -1.0 * (-0.001) * 80000 = +80
        assert balance_after > balance_before, "负费率多头应收取 funding"

    def test_short_position_positive_rate(self, funding_engine, tracker, balance):
        """
        场景：正费率 + 空头 → 应该收取 funding。
        预期：余额增加。
        """
        fe = funding_engine
        symbol = "ETH/USDT:USDT"

        tracker.update(symbol, -10.0)  # 10 ETH 空头
        balance_before = balance.get_usdt_balance()

        state = fe._states[symbol]
        state.current_rate = 0.001  # 正费率
        state.mark_price = 3000.0
        state.index_price = 3000.0
        state.next_funding_timestamp = time.time() - 1

        fe.check_settlements(tracker, balance)

        balance_after = balance.get_usdt_balance()
        # 正费率 + 空头 → funding = -(-10) * 0.001 * 3000 = +30
        assert balance_after > balance_before, "正费率空头应收取 funding"


# ============================================================
# 4. 并发操作与竞态条件
# ============================================================

class TestConcurrencyEdgeCases:
    """
    模拟多操作交错执行时的一致性。
    """

    def test_cancel_already_filled_order(self, order_manager, price_engine):
        """
        场景：订单在取消请求之前已经成交。
        预期：cancel_order 返回已成交的订单信息（不报错）。
        """
        om = order_manager
        symbol = "BTC/USDT:USDT"
        price = price_engine.get_price(symbol)

        # 下市价单（立即成交）
        order = om.place_order(symbol, "market", "buy", 1.0, price)
        assert order['status'] == 'closed'

        # 尝试取消已成交的订单
        result = om.cancel_order(order['id'])
        assert result['status'] == 'closed'  # 返回已成交状态

    def test_cancel_nonexistent_order(self, order_manager):
        """
        场景：取消一个不存在的订单。
        预期：抛出异常。
        """
        with pytest.raises(Exception, match="not found"):
            order_manager.cancel_order("nonexistent-123")

    def test_double_cancel(self, order_manager, price_engine):
        """
        场景：同一个订单取消两次。
        预期：第二次取消返回已取消的订单信息。
        """
        om = order_manager
        price = price_engine.get_price("BTC/USDT:USDT")
        order = om.place_order("BTC/USDT:USDT", "limit", "buy", 1.0, price * 0.5)

        result1 = om.cancel_order(order['id'])
        assert result1['status'] == 'canceled'

        # 第二次取消：应从 _closed_orders 中找到
        result2 = om.cancel_order(order['id'])
        assert result2['status'] == 'canceled'

    def test_fill_during_cancel_window(self, order_manager, price_engine):
        """
        场景：订单在取消前的最后一刻成交了。
        预期：try_fill 先成交，随后 cancel_order 发现已成交。
        """
        om = order_manager
        symbol = "ETH/USDT:USDT"
        state = price_engine.get_state(symbol)
        mid = state.mid_price

        # 挂一个很容易成交的限价买单
        order = om.place_order(symbol, "limit", "buy", 1.0, mid * 1.05)
        order_id = order['id']

        # fill_probability=1.0，多次尝试后应该成交
        for _ in range(20):
            om.try_fill_orders({symbol: price_engine.get_state(symbol)})

        # 尝试取消
        result = om.cancel_order(order_id)
        # 可能已成交（closed）或已部分成交后被取消
        assert result['status'] in ('closed', 'canceled')

    def test_multiple_symbols_interleaved_operations(self, order_manager, price_engine, tracker):
        """
        场景：多个交易对的操作交错进行。
        预期：各交易对的仓位和订单互不干扰。
        """
        om = order_manager
        symbols = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]

        # 交错操作
        for symbol in symbols:
            price = price_engine.get_price(symbol)
            om.place_order(symbol, "market", "buy", 100.0, price)

        for symbol in symbols:
            price = price_engine.get_price(symbol)
            om.place_order(symbol, "limit", "sell", 50.0, price * 1.05)

        # 各自独立
        for symbol in symbols:
            pos = tracker.get(symbol)
            assert pos > 0, f"{symbol} 应有多头仓位"
            open_orders = om.get_open_orders(symbol)
            assert len(open_orders) == 1, f"{symbol} 应有 1 个挂单"


# ============================================================
# 5. 仓位方向翻转与对冲
# ============================================================

class TestPositionFlipping:
    """
    模拟仓位从多头翻空头、对冲等复杂情况。
    """

    def test_flip_long_to_short(self, order_manager, price_engine, tracker):
        """
        场景：多头 → 平仓 → 空头（一步到位）。
        预期：仓位从正变负。
        """
        om = order_manager
        symbol = "BTC/USDT:USDT"
        price = price_engine.get_price(symbol)

        # 买入 100 张
        om.place_order(symbol, "market", "buy", 100.0, price)
        assert tracker.get(symbol) > 0

        # 卖出 200 张（超过持仓 → 翻空）
        om.place_order(symbol, "market", "sell", 200.0, price)
        assert tracker.get(symbol) < 0, "卖出超过持仓应产生空头"

    def test_gradual_position_reduction(self, order_manager, price_engine, tracker):
        """
        场景：逐步减仓（多次小量卖出）。
        预期：每次减仓后仓位递减。
        """
        om = order_manager
        symbol = "ETH/USDT:USDT"
        price = price_engine.get_price(symbol)

        # 买入 1000 张
        om.place_order(symbol, "market", "buy", 1000.0, price)
        initial_pos = tracker.get(symbol)

        # 逐步减仓
        positions = [initial_pos]
        for _ in range(5):
            om.place_order(symbol, "market", "sell", 200.0, price)
            positions.append(tracker.get(symbol))

        # 仓位应单调递减
        for i in range(1, len(positions)):
            assert positions[i] < positions[i - 1], f"仓位应递减: {positions}"

        # 最终应该是 0（1000 - 5*200 = 0）
        assert abs(tracker.get(symbol)) < 1e-9

    def test_hedge_position_spot_and_swap(self, order_manager, price_engine, tracker):
        """
        场景：现货买入 + 合约做空（对冲）。
        预期：两个方向的仓位独立记录。
        """
        om = order_manager
        btc_spot = "BTC/USDT"
        btc_swap = "BTC/USDT:USDT"
        price = price_engine.get_price(btc_spot)

        # 现货买入
        om.place_order(btc_spot, "market", "buy", 1.0, price)
        # 合约做空
        om.place_order(btc_swap, "market", "sell", 1000.0, price)  # 1000 * 0.001 = 1 BTC

        spot_pos = tracker.get(btc_spot)
        swap_pos = tracker.get(btc_swap)

        # 现货多头（contract_size=1 for spot）
        assert spot_pos > 0
        # 合约空头
        assert swap_pos < 0
        # 经济上对冲，但仓位独立
        assert abs(spot_pos + swap_pos) < 1e-6  # ~0 净敞口


# ============================================================
# 6. 无限增长修剪验证
# ============================================================

class TestUnboundedGrowthFix:
    """
    验证无限增长的修复效果。
    """

    def test_closed_orders_trimmed(self, order_manager, price_engine):
        """
        验证 _closed_orders 在超过 MAX_CLOSED_ORDERS 后被修剪。
        """
        om = order_manager
        price = price_engine.get_price("BTC/USDT:USDT")

        # 生成超过上限的已关闭订单
        for _ in range(OrderManager.MAX_CLOSED_ORDERS + 200):
            om.place_order("BTC/USDT:USDT", "market", "buy", 1.0, price)

        assert len(om._closed_orders) <= OrderManager.MAX_CLOSED_ORDERS, (
            f"_closed_orders 应被修剪: {len(om._closed_orders)} > {OrderManager.MAX_CLOSED_ORDERS}"
        )

    def test_closed_orders_trimmed_via_cancel(self, order_manager, price_engine):
        """
        验证通过取消产生的 closed_orders 也被修剪。
        """
        om = order_manager
        price = price_engine.get_price("BTC/USDT:USDT")

        for _ in range(OrderManager.MAX_CLOSED_ORDERS + 100):
            order = om.place_order("BTC/USDT:USDT", "limit", "buy", 1.0, price * 0.5)
            om.cancel_order(order['id'])

        assert len(om._closed_orders) <= OrderManager.MAX_CLOSED_ORDERS

    def test_settlement_history_trimmed(self, funding_engine, tracker, balance):
        """
        验证 _settlement_history 在超过 MAX_SETTLEMENT_HISTORY 后被修剪。
        """
        fe = funding_engine
        tracker.update("BTC/USDT:USDT", 1.0)

        # 触发大量结算
        for _ in range(100):
            for state in fe._states.values():
                state.next_funding_timestamp = time.time() - 1
                state.mark_price = 80000.0
                state.index_price = 80000.0
            fe.check_settlements(tracker, balance)

        assert len(fe._settlement_history) <= FundingEngine.MAX_SETTLEMENT_HISTORY, (
            f"_settlement_history 应被修剪: {len(fe._settlement_history)}"
        )

    def test_queue_maxsize_prevents_overflow(self, order_manager, price_engine):
        """
        验证 _update_queue 设置了 maxsize，超出时不报错（put_nowait 捕获 QueueFull）。
        """
        om = order_manager
        price = price_engine.get_price("BTC/USDT:USDT")

        # 下超过 queue maxsize 的订单
        for _ in range(OrderManager.MAX_QUEUE_SIZE + 100):
            om.place_order("BTC/USDT:USDT", "market", "buy", 1.0, price)

        # queue 不应超过 maxsize
        assert om._update_queue.qsize() <= OrderManager.MAX_QUEUE_SIZE

    def test_get_order_still_works_after_trim(self, order_manager, price_engine):
        """
        验证修剪后 get_order 对最近的订单仍然有效。
        """
        om = order_manager
        price = price_engine.get_price("BTC/USDT:USDT")

        # 生成大量订单
        last_order_id = None
        for _ in range(OrderManager.MAX_CLOSED_ORDERS + 200):
            order = om.place_order("BTC/USDT:USDT", "market", "buy", 1.0, price)
            last_order_id = order['id']

        # 最近的订单应该仍可查询
        result = om.get_order(last_order_id)
        assert result['id'] == last_order_id
        assert result['status'] == 'closed'

    def test_early_orders_pruned_after_trim(self, order_manager, price_engine):
        """
        验证修剪后最早的订单被移除。
        """
        om = order_manager
        price = price_engine.get_price("BTC/USDT:USDT")

        # 第一个订单
        first_order = om.place_order("BTC/USDT:USDT", "market", "buy", 1.0, price)
        first_id = first_order['id']

        # 生成足够多的后续订单触发修剪
        for _ in range(OrderManager.MAX_CLOSED_ORDERS + 200):
            om.place_order("BTC/USDT:USDT", "market", "buy", 1.0, price)

        # 最早的订单应该已被修剪
        with pytest.raises(Exception, match="not found"):
            om.get_order(first_id)


# ============================================================
# 7. 多交易所场景
# ============================================================

class TestMultiExchangeScenarios:
    """
    模拟多个交易所实例共存时的行为。
    """

    def test_separate_position_trackers(self):
        """
        场景：两个交易所各自有独立的 PositionTracker。
        预期：互不影响。
        """
        pt1 = PositionTracker()
        pt2 = PositionTracker()
        bal1 = BalanceTracker(100_000.0)
        bal2 = BalanceTracker(100_000.0)

        om1 = OrderManager(pt1, bal1, fill_probability=1.0,
                           contract_sizes={"BTC/USDT:USDT": 0.001})
        om2 = OrderManager(pt2, bal2, fill_probability=1.0,
                           contract_sizes={"BTC/USDT:USDT": 0.001})

        om1.place_order("BTC/USDT:USDT", "market", "buy", 1000.0, 80000.0)
        om2.place_order("BTC/USDT:USDT", "market", "sell", 500.0, 80000.0)

        assert pt1.get("BTC/USDT:USDT") > 0
        assert pt2.get("BTC/USDT:USDT") < 0
        assert pt1.get("BTC/USDT:USDT") != pt2.get("BTC/USDT:USDT")

    def test_separate_balance_trackers(self):
        """
        场景：两个交易所的手续费各自扣除。
        预期：互不影响。
        """
        bal1 = BalanceTracker(100_000.0)
        bal2 = BalanceTracker(50_000.0)

        bal1.apply_fee(100.0)
        bal2.apply_fee(200.0)

        assert bal1.get_usdt_balance() == 99_900.0
        assert bal2.get_usdt_balance() == 49_800.0


# ============================================================
# 8. Funding 结算边界条件
# ============================================================

class TestFundingEdgeCases:
    """
    模拟 funding 结算的各种边界情况。
    """

    def test_zero_position_no_settlement(self, funding_engine, tracker, balance):
        """
        场景：零仓位不应产生 funding 结算。
        """
        fe = funding_engine
        balance_before = balance.get_usdt_balance()

        for state in fe._states.values():
            state.next_funding_timestamp = time.time() - 1
            state.mark_price = 80000.0
            state.index_price = 80000.0
        fe.check_settlements(tracker, balance)

        assert balance.get_usdt_balance() == balance_before

    def test_tiny_position_settlement(self, funding_engine, tracker, balance):
        """
        场景：极小仓位（接近 1e-12 阈值）不应结算。
        """
        fe = funding_engine
        tracker.update("BTC/USDT:USDT", 1e-13)  # 低于阈值
        balance_before = balance.get_usdt_balance()

        state = fe._states["BTC/USDT:USDT"]
        state.next_funding_timestamp = time.time() - 1
        state.mark_price = 80000.0
        state.index_price = 80000.0
        fe.check_settlements(tracker, balance)

        assert balance.get_usdt_balance() == balance_before

    def test_multiple_settlements_in_sequence(self, funding_engine, tracker, balance):
        """
        场景：连续多次结算（模拟长时间离线后恢复）。
        预期：每次结算独立，费率均值回归。
        """
        fe = funding_engine
        symbol = "BTC/USDT:USDT"
        tracker.update(symbol, 1.0)

        rates = []
        for _ in range(20):
            state = fe._states[symbol]
            state.next_funding_timestamp = time.time() - 1
            state.mark_price = 80000.0
            state.index_price = 80000.0
            rates.append(state.current_rate)
            fe.check_settlements(tracker, balance)

        # 费率应该在基准附近波动（均值回归）
        avg_rate = sum(rates) / len(rates)
        base_rate = fe._states[symbol].base_rate
        assert abs(avg_rate - base_rate) < base_rate * 5, (
            f"费率均值 {avg_rate} 应接近基准 {base_rate}"
        )

    def test_extreme_funding_rate(self, funding_engine, tracker, balance):
        """
        场景：费率达到最大/最小值。
        预期：被 clamp 到合理范围。
        """
        fe = funding_engine
        symbol = "BTC/USDT:USDT"

        state = fe._states[symbol]
        state.current_rate = 0.1  # 设置一个超大费率

        # 触发一次结算（会导致均值回归）
        state.next_funding_timestamp = time.time() - 1
        state.mark_price = 80000.0
        state.index_price = 80000.0
        tracker.update(symbol, 1.0)
        fe.check_settlements(tracker, balance)

        # 费率应被 clamp
        new_rate = state.current_rate
        assert new_rate <= state.maximum_rate, f"费率 {new_rate} 超过最大值 {state.maximum_rate}"
        assert new_rate >= state.minimum_rate, f"费率 {new_rate} 低于最小值 {state.minimum_rate}"


# ============================================================
# 9. 价格引擎边界条件
# ============================================================

class TestPriceEngineEdgeCases:
    """
    测试价格引擎的边界情况。
    """

    def test_set_price_override_and_orders(self, order_manager, price_engine):
        """
        场景：手动注入价格后，限价单应按注入价格判断成交。
        """
        om = order_manager
        symbol = "BTC/USDT:USDT"

        # 挂买单在 70000
        order = om.place_order(symbol, "limit", "buy", 1.0, 70000.0)

        # 设置价格到 65000（低于买单价 → 应该能成交）
        price_engine.set_price(symbol, 65000.0)

        for _ in range(20):
            om.try_fill_orders({symbol: price_engine.get_state(symbol)})

        open_orders = om.get_open_orders(symbol)
        assert len(open_orders) == 0, "价格低于买单价时应成交"

    def test_high_volatility_prices_stay_positive(self):
        """
        场景：高波动率下价格不应变为负数。
        """
        pe = PriceEngine(volatility=0.05, seed=42)  # 5% volatility
        for _ in range(2000):
            pe.step_all()

        for symbol in pe.symbols:
            price = pe.get_price(symbol)
            assert price > 0, f"{symbol} 价格变为非正: {price}"

    def test_concurrent_step_and_read(self, price_engine):
        """
        场景：step 和 get_price 交替调用。
        预期：每次读取都返回有效价格。
        """
        pe = price_engine
        for _ in range(100):
            pe.step_all()
            for symbol in list(pe.symbols)[:5]:
                price = pe.get_price(symbol)
                assert price > 0
                ticker = pe.get_ticker(symbol)
                assert ticker['bid'] > 0
                assert ticker['ask'] > ticker['bid']


# ============================================================
# 10. 复杂订单生命周期
# ============================================================

class TestComplexOrderLifecycle:
    """
    测试订单在复杂场景下的完整生命周期。
    """

    def test_many_partial_fills_converge(self, order_manager, price_engine):
        """
        场景：限价单经过多次部分成交后最终全部成交。
        预期：filled + remaining = amount（或 remaining ≈ 0）。
        """
        om = order_manager
        symbol = "BTC/USDT:USDT"
        state = price_engine.get_state(symbol)

        # 挂买单在有利价位
        order = om.place_order(symbol, "limit", "buy", 100.0, state.mid_price * 1.02)
        order_id = order['id']

        # 多次尝试成交
        for _ in range(100):
            om.try_fill_orders({symbol: price_engine.get_state(symbol)})
            price_engine.step(symbol)

        # 验证订单一致性
        result = om.get_order(order_id)
        if result['status'] == 'closed':
            assert result['remaining'] == 0
            # filled 可能略小于 amount（remaining < 0.1% 时视为全部成交）
            assert result['filled'] >= result['amount'] * 0.999
        else:
            assert result['filled'] + result['remaining'] == pytest.approx(result['amount'], rel=1e-9)

    def test_mixed_order_types(self, order_manager, price_engine, tracker):
        """
        场景：市价单和限价单混合操作。
        预期：仓位正确反映所有成交。
        """
        om = order_manager
        symbol = "ETH/USDT:USDT"
        price = price_engine.get_price(symbol)

        # 市价买 + 限价卖
        om.place_order(symbol, "market", "buy", 500.0, price)
        order = om.place_order(symbol, "limit", "sell", 200.0, price * 0.99)

        # 让限价单有机会成交
        for _ in range(50):
            om.try_fill_orders({symbol: price_engine.get_state(symbol)})
            price_engine.step(symbol)

        pos = tracker.get(symbol)
        # 买了 500 张，卖了部分/全部 200 张
        cs = SYMBOLS_CONFIG['ETH']['contract_size']
        # 仓位应在 300*cs 到 500*cs 之间
        assert pos > 0, "应仍有多头仓位"

    def test_order_for_unknown_contract_size(self, order_manager, tracker):
        """
        场景：对没有配置 contract_size 的交易对下单。
        预期：使用默认 contract_size=1.0。
        """
        om = order_manager
        # UNKNOWN 不在 contract_sizes 中
        om.place_order("UNKNOWN/USDT:USDT", "market", "buy", 10.0, 100.0)
        # 默认 contract_size=1.0，仓位 = 10.0
        assert tracker.get("UNKNOWN/USDT:USDT") == 10.0

    def test_batch_cancel_with_mixed_states(self, order_manager, price_engine):
        """
        场景：批量取消包含已成交、已取消、仍 open 的订单。
        预期：不报错，各状态正确。
        """
        om = order_manager
        symbol = "SOL/USDT:USDT"
        price = price_engine.get_price(symbol)

        # 市价单（立即成交）
        filled_order = om.place_order(symbol, "market", "buy", 1.0, price)
        # 限价单（挂着）
        open_order = om.place_order(symbol, "limit", "buy", 1.0, price * 0.5)
        # 已取消
        cancel_me = om.place_order(symbol, "limit", "sell", 1.0, price * 2.0)
        om.cancel_order(cancel_me['id'])

        # 批量取消所有（混合状态）
        results = om.cancel_orders(
            [filled_order['id'], open_order['id'], cancel_me['id']],
            symbol
        )

        # 不应报错
        assert len(results) == 3
        statuses = {r['id']: r['status'] for r in results}
        assert statuses[filled_order['id']] == 'closed'    # 已成交
        assert statuses[open_order['id']] == 'canceled'     # 刚取消
        assert statuses[cancel_me['id']] == 'canceled'      # 已取消

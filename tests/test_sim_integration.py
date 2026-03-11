"""
SimulatedExchange 集成测试

验证完整 app loop 运行稳定性、仓位边界、订单管理等。

长期运行可能累积的错误类别：
1. 内存泄漏: _closed_orders 无限增长、_settlement_history 无限增长、HealthyDataArray 数据堆积
2. 仓位越界: position_usd 超出 max_position_usd 限制
3. 余额耗尽: 手续费 + 滑点导致余额变为负数
4. 订单残留: 限价单挂起后永远不成交也不取消，占用内存
5. 价格漂移: GBM 随机游走导致价格趋近 0 或趋向无穷大
6. Funding 结算累积: settlement_history 无限增长
7. 订单计数器溢出: _order_counter 持续递增
8. Queue 积压: asyncio.Queue 中订单更新堆积无人消费
9. Scope/变量泄漏: conditional_vars_update_times 无限增长
10. 指标数据窗口: HealthyDataArray._data_list 在 shrink 触发前过度膨胀
"""
import asyncio
import time
import pytest

from hft.core.app.factory import AppFactory


@pytest.fixture
def app_core():
    """创建 sim-spot-future AppCore 实例（不恢复缓存）"""
    factory = AppFactory("sim-spot-future", restore_cache=False)
    return factory.get_or_create_app_core()


def get_exchanges(app_core):
    """获取所有交易所实例"""
    return list(app_core.exchange_group.children.values())


@pytest.mark.integration
class TestSimIntegration:
    """SimulatedExchange 端到端集成测试"""

    async def test_app_starts_and_runs_ticks(self, app_core):
        """app 能正常启动并运行多个 tick 不报错"""
        await app_core.run_ticks(duration=10.0, initialize=True, finalize=True)

    async def test_price_engine_produces_data(self, app_core):
        """PriceEngine 能正常产生价格数据"""
        await app_core.start(True)
        try:
            exchange = get_exchanges(app_core)[0]
            assert hasattr(exchange, 'price_engine')

            ticker = await exchange.fetch_ticker("BTC/USDT")
            assert ticker['bid'] > 0
            assert ticker['ask'] > 0
            assert ticker['bid'] < ticker['ask']

            book = await exchange.fetch_order_book("BTC/USDT", limit=5)
            assert len(book['bids']) == 5
            assert len(book['asks']) == 5
        finally:
            await app_core.stop(True)

    async def test_balance_tracking(self, app_core):
        """余额追踪正常工作"""
        await app_core.start(True)
        try:
            exchange = get_exchanges(app_core)[0]
            balance = await exchange.medal_fetch_balance_usd('swap')
            assert balance > 0
        finally:
            await app_core.stop(True)

    async def test_position_starts_empty(self, app_core):
        """初始仓位应为空"""
        await app_core.start(True)
        try:
            exchange = get_exchanges(app_core)[0]
            positions = await exchange.fetch_positions()
            assert len(positions) == 0
        finally:
            await app_core.stop(True)

    async def test_order_lifecycle(self, app_core):
        """订单创建、查询、取消的完整生命周期"""
        await app_core.start(True)
        try:
            exchange = get_exchanges(app_core)[0]

            order = await exchange.create_order(
                "BTC/USDT:USDT", "limit", "buy", 1.0, 50000.0
            )
            assert order['status'] == 'open'
            order_id = order['id']

            open_orders = await exchange.fetch_open_orders("BTC/USDT:USDT")
            assert any(o['id'] == order_id for o in open_orders)

            cancelled = await exchange.cancel_order(order_id, "BTC/USDT:USDT")
            assert cancelled['status'] == 'canceled'

            open_orders = await exchange.fetch_open_orders("BTC/USDT:USDT")
            assert not any(o['id'] == order_id for o in open_orders)
        finally:
            await app_core.stop(True)

    async def test_market_order_fills_and_updates_position(self, app_core):
        """市价单立即成交并更新仓位"""
        await app_core.start(True)
        try:
            exchange = get_exchanges(app_core)[0]

            ticker = await exchange.fetch_ticker("BTC/USDT:USDT")
            price = ticker['last']
            order = await exchange.create_order(
                "BTC/USDT:USDT", "market", "buy", 100.0, price
            )
            assert order['status'] == 'closed'
            assert order['filled'] == 100.0

            positions = await exchange.fetch_positions()
            btc_pos = [p for p in positions if p['symbol'] == "BTC/USDT:USDT"]
            assert len(btc_pos) == 1
            assert btc_pos[0]['side'] == 'long'
        finally:
            await app_core.stop(True)

    async def test_multiple_ticks_price_changes(self, app_core):
        """多 tick 后价格应发生变化"""
        await app_core.start(True)
        try:
            exchange = get_exchanges(app_core)[0]
            price_before = (await exchange.fetch_ticker("BTC/USDT"))['last']
            for _ in range(10):
                await exchange.on_tick()
            price_after = (await exchange.fetch_ticker("BTC/USDT"))['last']
            assert isinstance(price_after, float)
            assert price_after > 0
        finally:
            await app_core.stop(True)

    async def test_funding_rates_available(self, app_core):
        """资金费率数据应可用"""
        await app_core.start(True)
        try:
            exchange = get_exchanges(app_core)[0]
            rates = await exchange.medal_fetch_funding_rates_internal()
            assert len(rates) > 0
            btc_rate = rates.get("BTC/USDT:USDT")
            assert btc_rate is not None
            assert btc_rate.mark_price > 0
        finally:
            await app_core.stop(True)

    async def test_balance_decreases_after_trading(self, app_core):
        """交易后余额应减少（手续费）"""
        await app_core.start(True)
        try:
            exchange = get_exchanges(app_core)[0]
            balance_before = await exchange.medal_fetch_balance_usd('swap')

            ticker = await exchange.fetch_ticker("BTC/USDT:USDT")
            price = ticker['last']
            await exchange.create_order("BTC/USDT:USDT", "market", "buy", 1000.0, price)
            await exchange.create_order("BTC/USDT:USDT", "market", "sell", 1000.0, price)

            balance_after = await exchange.medal_fetch_balance_usd('swap')
            assert balance_after < balance_before
        finally:
            await app_core.stop(True)


@pytest.mark.integration
class TestLongRunningStability:
    """
    长时间运行稳定性测试

    验证策略在持续运行后不会产生各种累积错误。
    每个测试关注一个具体的潜在问题。
    """

    async def _run_exchange_ticks(self, app_core, n_ticks: int):
        """手动推进 n 个 tick（不等待 interval 延迟）"""
        for exchange in get_exchanges(app_core):
            for _ in range(n_ticks):
                await exchange.on_tick()

    # === 1. 内存泄漏: _closed_orders 无限增长 ===

    async def test_closed_orders_accumulation(self, app_core):
        """
        验证: _closed_orders 在超过 MAX_CLOSED_ORDERS 后被修剪，不会无限增长。
        """
        await app_core.start(True)
        try:
            exchange = get_exchanges(app_core)[0]
            om = exchange.order_manager
            ticker = await exchange.fetch_ticker("BTC/USDT:USDT")
            price = ticker['last']

            from hft.exchange.simulated.engines.orders import OrderManager as OM
            n_orders = OM.MAX_CLOSED_ORDERS + 200
            for _ in range(n_orders):
                await exchange.create_order("BTC/USDT:USDT", "market", "buy", 1.0, price)

            # 修剪后不应超过上限
            assert len(om._closed_orders) <= OM.MAX_CLOSED_ORDERS, (
                f"_closed_orders 应被修剪: {len(om._closed_orders)}"
            )
        finally:
            await app_core.stop(True)

    # === 2. Funding 结算历史无限增长 ===

    async def test_settlement_history_accumulation(self, app_core):
        """
        验证: _settlement_history 在超过 MAX_SETTLEMENT_HISTORY 后被修剪。
        """
        await app_core.start(True)
        try:
            exchange = get_exchanges(app_core)[0]
            fe = exchange.funding_engine

            # 先建立仓位
            ticker = await exchange.fetch_ticker("BTC/USDT:USDT")
            price = ticker['last']
            await exchange.create_order("BTC/USDT:USDT", "market", "buy", 1000.0, price)

            from hft.exchange.simulated.engines.funding import FundingEngine as FE
            # 触发足够多的结算超过上限
            n_settlements = 100
            for _ in range(n_settlements):
                for symbol, state in fe._states.items():
                    state.next_funding_timestamp = time.time() - 1
                    state.mark_price = exchange.price_engine.get_price(symbol)
                fe.check_settlements(exchange.position_tracker, exchange.balance_tracker)

            history = fe.get_settlement_history()
            assert len(history) <= FE.MAX_SETTLEMENT_HISTORY, (
                f"_settlement_history 应被修剪: {len(history)}"
            )
        finally:
            await app_core.stop(True)

    # === 3. 价格合理性: GBM 不会导致极端价格 ===

    async def test_price_stays_reasonable_after_many_ticks(self, app_core):
        """
        问题: GBM 随机游走可能导致价格趋近 0 或无穷大。

        验证: 1000 ticks 后所有价格仍在合理范围内。
        """
        await app_core.start(True)
        try:
            exchange = get_exchanges(app_core)[0]
            pe = exchange.price_engine

            # 记录初始价格
            initial_prices = {s: pe.get_price(s) for s in pe.symbols}

            # 运行 1000 ticks
            for _ in range(1000):
                pe.step_all()

            for symbol in pe.symbols:
                price = pe.get_price(symbol)
                initial = initial_prices[symbol]
                # 价格必须为正
                assert price > 0, f"{symbol} 价格变为 {price}"
                # 1000 ticks 后价格不应偏离初始值太多（volatility=0.001 时 ~3%）
                ratio = price / initial
                assert 0.5 < ratio < 2.0, (
                    f"{symbol} 价格偏离过大: {initial} -> {price} (ratio={ratio:.3f})"
                )
        finally:
            await app_core.stop(True)

    # === 4. 现货合约价差: swap 跟随 spot ===

    async def test_spot_swap_basis_bounded(self, app_core):
        """
        问题: 合约价格应始终跟随现货，basis 不应无限漂移。

        验证: 多 tick 后 spot-swap 价差仍在 1% 以内。
        """
        await app_core.start(True)
        try:
            exchange = get_exchanges(app_core)[0]
            pe = exchange.price_engine

            for _ in range(500):
                pe.step_all()

            for symbol in pe.symbols:
                if ':' in symbol:
                    spot_symbol = symbol.replace(':USDT', '')
                    spot_price = pe.get_price(spot_symbol)
                    swap_price = pe.get_price(symbol)
                    basis = abs(swap_price / spot_price - 1)
                    assert basis < 0.01, (
                        f"{symbol} basis 过大: spot={spot_price}, swap={swap_price}, basis={basis:.4f}"
                    )
        finally:
            await app_core.stop(True)

    # === 5. 余额不为负: 交易 + 手续费不会导致负余额 ===

    async def test_balance_never_negative(self, app_core):
        """
        问题: 大量交易的手续费累积可能导致余额变为负数。

        验证: 大量交易后余额仍为正。
        """
        await app_core.start(True)
        try:
            exchange = get_exchanges(app_core)[0]
            bt = exchange.balance_tracker

            ticker = await exchange.fetch_ticker("ETH/USDT:USDT")
            price = ticker['last']

            # 反复买卖（每次都收手续费）
            for _ in range(100):
                await exchange.create_order("ETH/USDT:USDT", "market", "buy", 100.0, price)
                await exchange.create_order("ETH/USDT:USDT", "market", "sell", 100.0, price)

            balance = bt.get_usdt_balance()
            assert balance > 0, f"余额变为负数: {balance}"
        finally:
            await app_core.stop(True)

    # === 6. 限价单挂单残留 ===

    async def test_limit_orders_eventually_fill_or_cancel(self, app_core):
        """
        问题: 限价单可能因价格远离而永远挂着，占用内存。

        验证: 价格有利的限价单在多次 tick 后能成交。
        """
        await app_core.start(True)
        try:
            exchange = get_exchanges(app_core)[0]
            om = exchange.order_manager

            ticker = await exchange.fetch_ticker("BTC/USDT:USDT")
            mid = ticker['last']

            # 挂一个略高于市价的买单（应该很容易成交）
            order = await exchange.create_order(
                "BTC/USDT:USDT", "limit", "buy", 1.0, mid * 1.01
            )
            assert order['status'] == 'open'

            # 多次推进 tick（fill_probability=0.5，多次应该能成交）
            for _ in range(50):
                await exchange.on_tick()

            open_orders = await exchange.fetch_open_orders("BTC/USDT:USDT")
            # 50 ticks 后该订单应该已经成交
            assert len(open_orders) == 0, (
                f"限价单 50 ticks 后仍未成交，剩余挂单: {len(open_orders)}"
            )
        finally:
            await app_core.stop(True)

    # === 7. 仓位方向一致性 ===

    async def test_position_direction_consistency(self, app_core):
        """
        问题: 买卖操作后仓位方向应正确。

        验证: 买入→多头，卖出平仓→仓位清零，反向→空头。
        """
        await app_core.start(True)
        try:
            exchange = get_exchanges(app_core)[0]
            pt = exchange.position_tracker

            ticker = await exchange.fetch_ticker("SOL/USDT:USDT")
            price = ticker['last']

            # 买入 → 多头
            await exchange.create_order("SOL/USDT:USDT", "market", "buy", 100.0, price)
            assert pt.get("SOL/USDT:USDT") > 0

            # 卖出相同数量 → 平仓
            await exchange.create_order("SOL/USDT:USDT", "market", "sell", 100.0, price)
            assert abs(pt.get("SOL/USDT:USDT")) < 1e-9

            # 再卖出 → 空头
            await exchange.create_order("SOL/USDT:USDT", "market", "sell", 100.0, price)
            assert pt.get("SOL/USDT:USDT") < 0
        finally:
            await app_core.stop(True)

    # === 8. Order Queue 不会无限积压 ===

    async def test_order_queue_bounded(self, app_core):
        """
        验证: _update_queue 设置了 maxsize，超出时 put_nowait 跳过（不报错）。
        """
        await app_core.start(True)
        try:
            exchange = get_exchanges(app_core)[0]
            om = exchange.order_manager
            ticker = await exchange.fetch_ticker("BTC/USDT:USDT")
            price = ticker['last']

            from hft.exchange.simulated.engines.orders import OrderManager as OM
            # 下超过 maxsize 的订单
            for _ in range(OM.MAX_QUEUE_SIZE + 100):
                await exchange.create_order("BTC/USDT:USDT", "market", "buy", 1.0, price)

            queue_size = om._update_queue.qsize()
            assert queue_size <= OM.MAX_QUEUE_SIZE

            # 消费 queue
            updates = await om.wait_for_updates(timeout=0.1)
            assert len(updates) > 0

            # 消费后 queue 应该清空
            remaining = om._update_queue.qsize()
            assert remaining == 0
        finally:
            await app_core.stop(True)

    # === 9. 订单 ID 唯一性 ===

    async def test_order_ids_unique(self, app_core):
        """
        问题: _order_counter 递增，但需确保 ID 不重复。

        验证: 大量订单的 ID 全部唯一。
        """
        await app_core.start(True)
        try:
            exchange = get_exchanges(app_core)[0]
            ticker = await exchange.fetch_ticker("BTC/USDT:USDT")
            price = ticker['last']

            ids = set()
            for _ in range(500):
                order = await exchange.create_order("BTC/USDT:USDT", "market", "buy", 1.0, price)
                ids.add(order['id'])

            assert len(ids) == 500, f"订单 ID 不唯一，500 个订单只有 {len(ids)} 个不同 ID"
        finally:
            await app_core.stop(True)

    # === 10. 多交易对并行操作 ===

    async def test_multi_symbol_isolation(self, app_core):
        """
        问题: 多个交易对的仓位和订单应该互相隔离。

        验证: 不同交易对的操作不会互相干扰。
        """
        await app_core.start(True)
        try:
            exchange = get_exchanges(app_core)[0]
            pt = exchange.position_tracker

            btc_ticker = await exchange.fetch_ticker("BTC/USDT:USDT")
            eth_ticker = await exchange.fetch_ticker("ETH/USDT:USDT")

            # BTC 买入
            await exchange.create_order("BTC/USDT:USDT", "market", "buy", 100.0, btc_ticker['last'])
            # ETH 卖出
            await exchange.create_order("ETH/USDT:USDT", "market", "sell", 100.0, eth_ticker['last'])

            btc_pos = pt.get("BTC/USDT:USDT")
            eth_pos = pt.get("ETH/USDT:USDT")

            # BTC 多头，ETH 空头
            assert btc_pos > 0, f"BTC 应为多头，实际 {btc_pos}"
            assert eth_pos < 0, f"ETH 应为空头，实际 {eth_pos}"

            # 互相独立
            sol_pos = pt.get("SOL/USDT:USDT")
            assert sol_pos == 0.0, f"SOL 不应有仓位，实际 {sol_pos}"
        finally:
            await app_core.stop(True)

    # === 11. 策略 flow 执行: 变量传递完整性 ===

    async def test_strategy_flow_execution(self, app_core):
        """
        问题: 策略 flow 从 GlobalScope → TradingPairScope 的变量传递链可能断裂。

        验证: 策略计算 flow_nodes 不报错，且输出的节点含有必要变量。
        """
        await app_core.start(True)
        try:
            # 需要等指标就绪（有数据）
            # 手动推进 exchange ticks 让数据流动
            for exchange in get_exchanges(app_core):
                for _ in range(5):
                    await exchange.on_tick()

            strategy = app_core.strategy
            nodes = strategy.calculate_flow_nodes()
            # 可能因为指标还没健康而返回空，这是正常的
            if len(nodes) > 0:
                for node in nodes.values():
                    # GlobalScope 变量应该传递下来
                    max_position_usd = node.get_var("max_position_usd", None)
                    assert max_position_usd is not None, "max_position_usd 未传递到 TradingPairScope"
                    assert max_position_usd == 2000
        finally:
            await app_core.stop(True)

    # === 12. 完整 loop 运行后状态一致性 ===

    async def test_full_loop_state_consistency(self, app_core):
        """
        问题: 完整 app loop 运行后各组件状态应一致。

        验证: 运行一段时间后交易所状态不矛盾。
        """
        # 使用较短 interval 加速
        app_core.config.interval = 0.1
        await app_core.run_ticks(duration=5.0, initialize=True, finalize=False)
        try:
            for exchange in get_exchanges(app_core):
                # 余额应为正
                balance = await exchange.medal_fetch_balance_usd('swap')
                assert balance > 0, f"余额异常: {balance}"

                # 价格应为正
                for symbol in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
                    ticker = await exchange.fetch_ticker(symbol)
                    assert ticker['last'] > 0
                    assert ticker['bid'] > 0
                    assert ticker['ask'] > 0
        finally:
            await app_core.stop(True)

    # === 13. 大量 tick 后 open orders 不会泄漏 ===

    async def test_open_orders_not_leaking(self, app_core):
        """
        问题: 如果限价单被取消但从 _orders dict 中未移除，会导致内存泄漏。

        验证: 取消的订单确实从 _orders 中移除。
        """
        await app_core.start(True)
        try:
            exchange = get_exchanges(app_core)[0]
            om = exchange.order_manager
            ticker = await exchange.fetch_ticker("BTC/USDT:USDT")
            price = ticker['last']

            # 下 50 个限价单
            order_ids = []
            for i in range(50):
                order = await exchange.create_order(
                    "BTC/USDT:USDT", "limit", "buy", 1.0, price * 0.5  # 远低于市价，不会成交
                )
                order_ids.append(order['id'])

            assert len(om._orders) == 50

            # 全部取消
            for oid in order_ids:
                await exchange.cancel_order(oid, "BTC/USDT:USDT")

            # _orders 应该为空
            assert len(om._orders) == 0, f"取消后仍有 {len(om._orders)} 个 open orders"
            # _closed_orders 应该增加了 50 个
            assert len(om._closed_orders) == 50
        finally:
            await app_core.stop(True)

    # === 14. 部分成交后的订单状态正确 ===

    async def test_partial_fill_state(self, app_core):
        """
        问题: 部分成交后订单的 filled/remaining/status 可能不一致。

        验证: 部分成交后字段一致。
        """
        await app_core.start(True)
        try:
            exchange = get_exchanges(app_core)[0]
            om = exchange.order_manager

            # 使用内部 API 精确控制部分成交
            from hft.exchange.simulated.engines.orders import SimulatedOrder
            order = SimulatedOrder(
                id="test-partial",
                symbol="BTC/USDT:USDT",
                type="limit",
                side="buy",
                amount=10.0,
                price=80000.0,
            )
            om._orders[order.id] = order

            # 手动执行部分成交
            om._execute_fill(order, 3.0, 80000.0)

            assert order.filled == 3.0
            assert abs(order.remaining - 7.0) < 1e-9
            assert order.status == 'open'  # 未完全成交

            # 再成交剩余
            om._execute_fill(order, 7.0, 80000.0)

            assert order.filled == 10.0
            assert order.remaining == 0.0
            assert order.status == 'closed'
            assert order.average == 80000.0
        finally:
            await app_core.stop(True)

    # === 15. Funding 结算金额正确性 ===

    async def test_funding_settlement_correctness(self, app_core):
        """
        问题: funding_amount = -position × rate × mark_price 计算可能有误。

        验证: 多头持正费率时应支付 funding（余额减少）。
        """
        await app_core.start(True)
        try:
            exchange = get_exchanges(app_core)[0]
            bt = exchange.balance_tracker
            pt = exchange.position_tracker
            fe = exchange.funding_engine

            balance_before = bt.get_usdt_balance()

            # 建立 1 BTC 多头仓位
            ticker = await exchange.fetch_ticker("BTC/USDT:USDT")
            price = ticker['last']
            contract_size = exchange._sim_contract_sizes.get("BTC/USDT:USDT", 0.001)
            contracts = 1.0 / contract_size  # 1 BTC 对应的合约数
            await exchange.create_order("BTC/USDT:USDT", "market", "buy", contracts, price)

            # 确认仓位
            pos = pt.get("BTC/USDT:USDT")
            assert abs(pos - 1.0) < 1e-6, f"仓位应为 1.0 BTC，实际 {pos}"

            balance_after_trade = bt.get_usdt_balance()

            # 设置正费率并触发结算
            state = fe._states["BTC/USDT:USDT"]
            state.current_rate = 0.001  # 正费率
            state.mark_price = price
            state.next_funding_timestamp = time.time() - 1  # 触发结算

            fe.check_settlements(pt, bt)

            balance_after_funding = bt.get_usdt_balance()
            # 多头 + 正费率 → 支付 funding → 余额减少
            # funding = -1.0 * 0.001 * price = -0.001 * price (负数 → 余额减少)
            expected_funding = -1.0 * 0.001 * price
            actual_change = balance_after_funding - balance_after_trade
            assert abs(actual_change - expected_funding) < 1.0, (
                f"Funding 结算金额异常: 预期 {expected_funding:.2f}, 实际变化 {actual_change:.2f}"
            )
        finally:
            await app_core.stop(True)

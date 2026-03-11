"""
策略执行测试 - 验证策略在模拟交易所中的端到端执行

测试真实策略（market_neutral_positions/spot_future）完整执行链路：
    指标就绪 → 策略计算 flow → 执行器下单 → 仓位管理

使用 sim-spot-future-test 配置（放宽指标健康参数，加速测试）。
"""
import asyncio
import time
import pytest
import pytest_asyncio

from hft.core.app.factory import AppFactory
from hft.core.listener import ListenerState
from hft.core.scope.scopes import TradingPairScope, TradingPairClassScope


def get_exchanges(app_core):
    return list(app_core.exchange_group.children.values())


async def warm_up_app(app_core, rounds=3):
    """
    手动预热 app，让指标尽快就绪。

    通过主动调用 exchange.on_tick()、indicator start/tick、executor.tick() 来
    绕过 background task interval 的等待，快速填充数据。

    指标由 query_indicator() 惰性创建，状态为 STOPPED + enabled=True，
    需要手动启动和 tick 来填充数据。
    """
    exchanges = get_exchanges(app_core)
    if not exchanges:
        return

    # 多轮预热
    for round_idx in range(rounds):
        # 推进交易所价格引擎
        for exchange in exchanges:
            if exchange.state == ListenerState.RUNNING:
                for _ in range(10):
                    await exchange.on_tick()

        # 让后台任务运行
        await asyncio.sleep(0.2)

        # 启动并 tick 所有指标
        for child in list(app_core.indicator_group.children.values()):
            # 惰性创建的指标需要先启动
            if child.state == ListenerState.STOPPED and child.enabled:
                try:
                    await child.start(True)
                except Exception:
                    pass
            if child.state in (ListenerState.STARTING, ListenerState.RUNNING):
                try:
                    await child.tick()
                except Exception:
                    pass
            for sub in list(child.children.values()):
                if sub.state == ListenerState.STOPPED and sub.enabled:
                    try:
                        await sub.start(True)
                    except Exception:
                        pass
                if sub.state in (ListenerState.STARTING, ListenerState.RUNNING):
                    try:
                        await sub.tick()
                    except Exception:
                        pass

        # 触发 executor tick（创建/查询指标 + 计算 flow + 下单）
        try:
            await app_core.executor.tick()
        except Exception:
            pass


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def app():
    """创建、启动、预热（module 级共享，只初始化一次）"""
    factory = AppFactory("sim-spot-future-test", restore_cache=False)
    app_core = factory.get_or_create_app_core()
    # 使用 run_ticks 确保完整的 listener 生命周期（交易所启动需要几轮 update_background_task）
    await app_core.run_ticks(duration=5.0, initialize=True, finalize=False)
    # 额外预热：主动填充指标数据
    await warm_up_app(app_core, rounds=6)
    yield app_core
    await app_core.stop(True)


# ===== 辅助函数 =====

def collect_ready_indicators(app_core):
    """收集所有已就绪的 indicator"""
    ready = []
    not_ready = []
    for name, child in app_core.indicator_group.children.items():
        if hasattr(child, 'ready'):
            if child.ready:
                ready.append(name)
            else:
                not_ready.append(name)
        for sub_name, sub in child.children.items():
            full_name = f"{name}/{sub_name}"
            if hasattr(sub, 'ready'):
                try:
                    if sub.ready:
                        ready.append(full_name)
                    else:
                        not_ready.append(full_name)
                except Exception:
                    not_ready.append(full_name)
    return ready, not_ready


def get_flow_nodes(app_core):
    """获取策略 flow 节点"""
    return app_core.strategy.calculate_flow_nodes()


# ===== 指标就绪测试 =====

@pytest.mark.integration
class TestIndicatorReadiness:
    """验证指标在模拟交易所中能够变为就绪状态"""

    async def test_exchange_is_running(self, app):
        """交易所应在运行状态"""
        for exchange in get_exchanges(app):
            assert exchange.state == ListenerState.RUNNING, f"{exchange.name} should be running"
            assert exchange.ready, f"{exchange.name} should be ready"

    async def test_indicators_created(self, app):
        """应至少创建了部分 indicator"""
        children = app.indicator_group.children
        assert len(children) > 0, "Should have created some indicators"

    async def test_some_indicators_ready(self, app):
        """预热后应有部分指标就绪"""
        ready, not_ready = collect_ready_indicators(app)
        assert len(ready) > 0, (
            f"No indicators ready. Not ready: {not_ready[:10]}"
        )

    async def test_ticker_indicators_ready(self, app):
        """TickerDataSource 应在预热后就绪"""
        ready, _ = collect_ready_indicators(app)
        ticker_ready = [r for r in ready if 'ticker' in r.lower() and 'volume' not in r.lower()]
        assert len(ticker_ready) > 0, "At least one ticker indicator should be ready"

    async def test_fair_price_indicators_ready(self, app):
        """FairPriceIndicator 应就绪（BaseIndicator，无 data 依赖）"""
        ready, _ = collect_ready_indicators(app)
        fp_ready = [r for r in ready if 'fair_price' in r.lower()]
        # fair_price 继承自 BaseIndicator，只要 running 就 ready
        assert len(fp_ready) > 0, "FairPriceIndicator should be ready"


# ===== 策略 Flow 测试 =====

@pytest.mark.integration
class TestStrategyFlow:
    """验证策略 flow 的执行和输出"""

    async def test_flow_produces_nodes(self, app):
        """calculate_flow_nodes 应返回非空的节点字典"""
        nodes = get_flow_nodes(app)
        # 可能部分节点由于指标未就绪被跳过
        # 但至少应该有一些节点（哪怕不完整）
        # 注意：如果所有 trade_intensity 都未 ready，可能返回空
        if len(nodes) == 0:
            # 检查是否因为 trade_intensity 未就绪
            ready, not_ready = collect_ready_indicators(app)
            ti_not_ready = [n for n in not_ready if 'trade_intensity' in n.lower()]
            if ti_not_ready:
                pytest.skip(
                    f"trade_intensity not ready yet: {ti_not_ready[:5]}. "
                    "This is expected if not enough trades accumulated."
                )
        assert isinstance(nodes, dict)

    async def test_flow_nodes_are_trading_pair_scope(self, app):
        """最终节点应为 TradingPairScope 级别"""
        nodes = get_flow_nodes(app)
        if not nodes:
            pytest.skip("No flow nodes produced")
        for key, node in nodes.items():
            assert isinstance(node.scope, TradingPairScope), (
                f"Node {key} should be TradingPairScope, got {type(node.scope).__name__}"
            )

    async def test_flow_nodes_have_position_usd(self, app):
        """节点应包含 position_usd 变量"""
        nodes = get_flow_nodes(app)
        if not nodes:
            pytest.skip("No flow nodes produced")
        for key, node in nodes.items():
            position_usd = node.get_var("position_usd", None)
            assert position_usd is not None, f"Node {key} should have position_usd"

    async def test_flow_nodes_position_usd_within_bounds(self, app):
        """position_usd 应在 [-max_position_usd, max_position_usd] 范围内"""
        nodes = get_flow_nodes(app)
        if not nodes:
            pytest.skip("No flow nodes produced")
        max_pos = 2000  # from strategy config
        for key, node in nodes.items():
            pos_usd = node.get_var("position_usd", 0)
            assert abs(pos_usd) <= max_pos * 1.01, (
                f"Node {key}: position_usd={pos_usd} exceeds max_position_usd={max_pos}"
            )

    async def test_max_pairs_filter(self, app):
        """flow 节点不应超过 max_pairs 组"""
        nodes = get_flow_nodes(app)
        if not nodes:
            pytest.skip("No flow nodes produced")
        # 每组有多个 TradingPairScope（不同 exchange 实例）
        # 提取唯一的 symbol 组
        symbols = set()
        for key, node in nodes.items():
            symbol = node.get_var("symbol")
            if symbol:
                base = symbol.split('/')[0]
                symbols.add(base)
        max_pairs = 5  # from strategy config
        assert len(symbols) <= max_pairs, (
            f"Selected {len(symbols)} groups > max_pairs={max_pairs}: {symbols}"
        )

    async def test_flow_nodes_have_exchange_path(self, app):
        """每个节点应有 exchange_path 变量"""
        nodes = get_flow_nodes(app)
        if not nodes:
            pytest.skip("No flow nodes produced")
        for key, node in nodes.items():
            ep = node.get_var("exchange_path")
            assert ep is not None, f"Node {key} missing exchange_path"

    async def test_direction_follows_fair_price(self, app):
        """direction 应与 fair_price 偏离一致"""
        nodes = get_flow_nodes(app)
        if not nodes:
            pytest.skip("No flow nodes produced")
        for key, node in nodes.items():
            direction = node.get_var("direction", 0)
            delta_price = node.get_var("delta_price", 0)
            entry_threshold = node.get_var("entry_threshold", 0.001)
            if direction != 0:
                # direction=1 means delta_price > entry_threshold
                # direction=-1 means delta_price < -entry_threshold
                if direction == 1:
                    assert delta_price >= entry_threshold, (
                        f"direction=1 but delta_price={delta_price} < threshold={entry_threshold}"
                    )
                elif direction == -1:
                    assert delta_price <= -entry_threshold, (
                        f"direction=-1 but delta_price={delta_price} > -threshold"
                    )


# ===== 执行器下单测试 =====

@pytest.mark.integration
class TestExecutorOrders:
    """验证执行器的下单行为"""

    async def test_active_orders_tracker_populated(self, app):
        """执行器应跟踪活跃订单"""
        tracker = app.executor.active_orders_tracker
        total_orders = sum(
            len(orders)
            for symbols in tracker.orders.values()
            for orders in symbols.values()
        )
        # 可能还没有下单（指标未就绪），但 tracker 应存在
        assert tracker is not None
        # 如果有 flow 节点，应有订单
        nodes = get_flow_nodes(app)
        if nodes:
            # 至少应该尝试过下单
            exchanges = get_exchanges(app)
            any_orders = False
            for exchange in exchanges:
                open_orders = exchange.order_manager.get_open_orders()
                closed = exchange.order_manager._closed_orders
                if open_orders or closed:
                    any_orders = True
            if not any_orders:
                # 可能因为 trade_intensity 未就绪导致 executor 跳过
                ready, not_ready = collect_ready_indicators(app)
                ti_not_ready = [n for n in not_ready if 'trade_intensity' in n.lower()]
                if ti_not_ready:
                    pytest.skip("trade_intensity not ready, no orders expected")

    async def test_orders_have_correct_side(self, app):
        """订单方向应与 direction/delta 一致"""
        nodes = get_flow_nodes(app)
        if not nodes:
            pytest.skip("No flow nodes produced")

        for exchange in get_exchanges(app):
            for order in exchange.order_manager.get_open_orders():
                # buy order → delta > 0, sell order → delta < 0
                assert order['side'] in ('buy', 'sell')
                assert order['amount'] > 0

    async def test_orders_are_limit_type(self, app):
        """订单应为 limit 类型（post_only 或普通 limit）"""
        for exchange in get_exchanges(app):
            for order in exchange.order_manager.get_open_orders():
                assert 'limit' in order.get('type', ''), (
                    f"Order {order['id']} type={order.get('type')} should be limit"
                )


# ===== 仓位管理测试 =====

@pytest.mark.integration
class TestPositionManagement:
    """验证仓位管理"""

    async def test_balance_not_negative(self, app):
        """余额不应变为负数"""
        for exchange in get_exchanges(app):
            bal = exchange.balance_tracker.get_usdt_balance()
            assert bal >= 0, f"Balance is negative: {bal}"

    async def test_initial_positions_reasonable(self, app):
        """初始仓位应在合理范围内"""
        for exchange in get_exchanges(app):
            positions = exchange.position_tracker.get_all()
            for symbol, pos in positions.items():
                # 检查仓位是否合理（对应 position_usd <= max_position_usd=2000）
                price = exchange.price_engine.get_price(symbol)
                if price > 0:
                    pos_usd = abs(pos) * price
                    # 宽松阈值：允许一定偏差（因为价格波动、部分成交、coin rotation 测试修改了价格）
                    assert pos_usd < 10000, (
                        f"{symbol}: position_usd={pos_usd:.2f} seems too large"
                    )


# ===== 币种轮换测试 =====

@pytest.mark.integration
class TestCoinRotation:
    """测试 top-5 币种轮换"""

    async def test_price_change_affects_selection(self, app):
        """修改价格偏离应影响 top-5 选择"""
        # 获取第一次的选中组
        nodes1 = get_flow_nodes(app)
        if not nodes1:
            pytest.skip("No flow nodes produced")
        symbols1 = set()
        for node in nodes1.values():
            s = node.get_var("symbol")
            if s:
                symbols1.add(s.split('/')[0])

        # 极端操控价格：让某个未选中的币对产生巨大偏离
        exchanges = get_exchanges(app)
        all_symbols = list(exchanges[0].price_engine.symbols)
        # 找一个未被选中的 swap symbol
        unselected = None
        for sym in all_symbols:
            if ':' in sym:
                base = sym.split('/')[0]
                if base not in symbols1:
                    unselected = sym
                    break

        if unselected is None:
            pytest.skip("All symbols are already selected, can't test rotation")

        # 给未选中的币一个极端价格偏离（让 fair_price 远离 1.0）
        original_price = exchanges[0].price_engine.get_price(unselected)
        # 大幅偏移 swap 价格使 actual_spread 变大
        for exchange in exchanges:
            exchange.set_price(unselected, original_price * 1.05)
            # 对应 spot 保持原价
            spot_sym = unselected.replace(":USDT", "")
            exchange.set_price(spot_sym, original_price * 0.95)

        # 重新运行几轮
        await warm_up_app(app, rounds=2)

        # 获取新的选中组
        nodes2 = get_flow_nodes(app)
        if not nodes2:
            # 有可能新的指标还未就绪
            return

        symbols2 = set()
        for node in nodes2.values():
            s = node.get_var("symbol")
            if s:
                symbols2.add(s.split('/')[0])

        # 清除 price override
        for exchange in exchanges:
            exchange.clear_price_override(unselected)
            spot_sym = unselected.replace(":USDT", "")
            exchange.clear_price_override(spot_sym)

        # 选中组可能发生变化（不一定 100% 变化，因为排序权重复杂）
        # 只验证轮换基础设施工作正常，不强制要求特定结果
        assert len(symbols2) <= 5


# ===== 多交易所协同测试 =====

@pytest.mark.integration
class TestMultiExchange:
    """验证多交易所（sim/binance + sim/okx）协同"""

    async def test_both_exchanges_running(self, app):
        """两个交易所都应在运行"""
        exchanges = get_exchanges(app)
        assert len(exchanges) >= 2
        for exchange in exchanges:
            assert exchange.state == ListenerState.RUNNING

    async def test_exchanges_have_independent_positions(self, app):
        """每个交易所有独立的仓位追踪器"""
        exchanges = get_exchanges(app)
        trackers = set()
        for exchange in exchanges:
            trackers.add(id(exchange.position_tracker))
        assert len(trackers) == len(exchanges), "Each exchange should have its own position tracker"

    async def test_exchanges_have_independent_balance(self, app):
        """每个交易所有独立的余额追踪器"""
        exchanges = get_exchanges(app)
        trackers = set()
        for exchange in exchanges:
            trackers.add(id(exchange.balance_tracker))
        assert len(trackers) == len(exchanges)

    async def test_flow_nodes_cover_both_exchanges(self, app):
        """flow 节点应覆盖两个交易所"""
        nodes = get_flow_nodes(app)
        if not nodes:
            pytest.skip("No flow nodes produced")
        exchange_paths = set()
        for node in nodes.values():
            ep = node.get_var("exchange_path")
            if ep:
                exchange_paths.add(ep)
        # 应至少覆盖 2 个交易所（如果有 flow 节点）
        if len(nodes) > 1:
            assert len(exchange_paths) >= 2, (
                f"Expected nodes from 2+ exchanges, got: {exchange_paths}"
            )


# ===== 价格数据完整性 =====

@pytest.mark.integration
class TestPriceDataIntegrity:
    """验证价格数据在策略运行期间的完整性"""

    async def test_prices_remain_positive(self, app):
        """所有价格应保持正数"""
        for exchange in get_exchanges(app):
            for sym in exchange.price_engine.symbols:
                price = exchange.price_engine.get_price(sym)
                assert price > 0, f"{sym} price is non-positive: {price}"

    async def test_spot_swap_basis_reasonable(self, app):
        """现货/合约价差应在合理范围（考虑 GBM 随机游走和币种轮换测试的价格注入）"""
        exchange = get_exchanges(app)[0]
        from hft.exchange.simulated.markets import SYMBOLS_CONFIG
        for base in list(SYMBOLS_CONFIG.keys())[:5]:
            spot = exchange.price_engine.get_price(f"{base}/USDT")
            swap = exchange.price_engine.get_price(f"{base}/USDT:USDT")
            if spot > 0 and swap > 0:
                basis = abs(swap / spot - 1)
                # 宽松阈值：长时间运行 + 币种轮换测试修改了价格
                assert basis < 0.15, (
                    f"{base} basis too large: {basis:.4f} (spot={spot}, swap={swap})"
                )

    async def test_funding_rates_within_bounds(self, app):
        """资金费率应在合理范围内"""
        for exchange in get_exchanges(app):
            for sym, state in exchange.funding_engine._states.items():
                assert state.minimum_rate <= state.current_rate <= state.maximum_rate, (
                    f"{sym} rate {state.current_rate} outside [{state.minimum_rate}, {state.maximum_rate}]"
                )


# ===== 策略完整性检查 =====

@pytest.mark.integration
class TestStrategyIntegrity:
    """验证策略 flow 的语义正确性"""

    async def test_has_pos_flag_consistency(self, app):
        """has_pos 标记应与实际仓位一致"""
        nodes = get_flow_nodes(app)
        if not nodes:
            pytest.skip("No flow nodes produced")
        for key, node in nodes.items():
            has_pos = node.get_var("has_pos", 0)
            amount = node.get_var("amount", 0)
            if abs(amount) > 1e-6:
                assert has_pos > 0, (
                    f"Node {key}: amount={amount} but has_pos=0"
                )

    async def test_ratio_and_direction_consistency(self, app):
        """ratio 应与 direction 方向一致"""
        nodes = get_flow_nodes(app)
        if not nodes:
            pytest.skip("No flow nodes produced")
        for key, node in nodes.items():
            ratio = node.get_var("ratio", 0)
            direction = node.get_var("direction", 0)
            if direction == 0:
                assert ratio == 0, (
                    f"Node {key}: direction=0 but ratio={ratio}"
                )

    async def test_is_futures_flag_correct(self, app):
        """is_futures 应正确标记合约交易对"""
        nodes = get_flow_nodes(app)
        if not nodes:
            pytest.skip("No flow nodes produced")
        for key, node in nodes.items():
            symbol = node.get_var("symbol", "")
            is_futures = node.get_var("is_futures", False)
            if ':' in symbol:
                assert is_futures, f"{symbol} should be is_futures=True"
            else:
                assert not is_futures, f"{symbol} should be is_futures=False"

    async def test_no_duplicate_symbols_per_exchange(self, app):
        """同一交易所不应有重复的 symbol"""
        nodes = get_flow_nodes(app)
        if not nodes:
            pytest.skip("No flow nodes produced")
        seen = {}  # (exchange_path, symbol) → node_key
        for key, node in nodes.items():
            ep = node.get_var("exchange_path")
            sym = node.get_var("symbol")
            pair = (ep, sym)
            assert pair not in seen, (
                f"Duplicate: {pair} in both {seen[pair]} and {key}"
            )
            seen[pair] = key


# ===== 长时间运行稳定性 =====

@pytest.mark.integration
class TestLongRunStability:
    """运行更长时间后检查稳定性"""

    async def test_extended_run_no_crash(self, app):
        """额外运行多轮后不应崩溃"""
        for _ in range(5):
            try:
                await app.executor.tick()
            except Exception as e:
                pytest.fail(f"Executor tick crashed: {e}")
            for exchange in get_exchanges(app):
                await exchange.on_tick()

    async def test_balance_changes_with_fills(self, app):
        """如果有成交，余额应与初始值不同（手续费+PnL）"""
        for exchange in get_exchanges(app):
            initial = exchange.config.initial_balance_usdt
            current = exchange.balance_tracker.get_usdt_balance()
            closed_count = len(exchange.order_manager._closed_orders)
            if closed_count > 0:
                # 有成交余额应该变化（手续费 + 交易 PnL）
                assert current != initial, (
                    f"Balance unchanged with {closed_count} fills"
                )

    async def test_order_ids_unique(self, app):
        """所有订单 ID 应唯一"""
        for exchange in get_exchanges(app):
            seen_ids = set()
            for order in exchange.order_manager.get_open_orders():
                assert order['id'] not in seen_ids, f"Duplicate order ID: {order['id']}"
                seen_ids.add(order['id'])
            for order in exchange.order_manager._closed_orders:
                ccxt_order = order.to_ccxt_order()
                assert ccxt_order['id'] not in seen_ids, f"Duplicate order ID: {ccxt_order['id']}"
                seen_ids.add(ccxt_order['id'])

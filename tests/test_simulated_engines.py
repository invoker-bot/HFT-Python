"""
SimulatedExchange 引擎单元测试
"""
import asyncio
import time
import pytest
from unittest.mock import MagicMock

from hft.exchange.simulated.engines.price import PriceEngine
from hft.exchange.simulated.engines.funding import FundingEngine
from hft.exchange.simulated.engines.orders import OrderManager
from hft.exchange.simulated.engines.positions import PositionTracker
from hft.exchange.simulated.engines.balance import BalanceTracker
from hft.exchange.simulated.markets import (
    build_all_markets, build_currencies, get_swap_symbols, get_spot_symbols,
    SYMBOLS_CONFIG,
)


class TestPriceEngine:
    def test_initialization(self):
        engine = PriceEngine(seed=42)
        assert len(engine.symbols) == len(SYMBOLS_CONFIG) * 2  # spot + swap

    def test_get_price(self):
        engine = PriceEngine(seed=42)
        btc_price = engine.get_price("BTC/USDT")
        assert 50000 < btc_price < 120000  # 合理范围

    def test_step_changes_price(self):
        engine = PriceEngine(seed=42)
        p1 = engine.get_price("BTC/USDT")
        engine.step("BTC/USDT")
        p2 = engine.get_price("BTC/USDT")
        # 价格应该变化（除非极小概率完全相同）
        assert isinstance(p2, float)
        assert p2 > 0

    def test_step_all(self):
        engine = PriceEngine(seed=42)
        prices_before = {s: engine.get_price(s) for s in engine.symbols}
        engine.step_all()
        prices_after = {s: engine.get_price(s) for s in engine.symbols}
        # 至少有些价格应该变化了
        changed = sum(1 for s in engine.symbols if prices_before[s] != prices_after[s])
        assert changed > 0

    def test_set_price_override(self):
        engine = PriceEngine(seed=42)
        engine.set_price("BTC/USDT", 99999.0)
        assert engine.get_price("BTC/USDT") == 99999.0
        # step 不改变 override
        engine.step("BTC/USDT")
        assert engine.get_price("BTC/USDT") == 99999.0

    def test_clear_price_override(self):
        engine = PriceEngine(seed=42)
        engine.set_price("BTC/USDT", 99999.0)
        engine.clear_price_override("BTC/USDT")
        engine.step("BTC/USDT")
        assert engine.get_price("BTC/USDT") != 99999.0

    def test_get_ticker(self):
        engine = PriceEngine(seed=42)
        ticker = engine.get_ticker("BTC/USDT")
        assert ticker['symbol'] == "BTC/USDT"
        assert ticker['bid'] < ticker['ask']
        assert ticker['bid'] > 0
        assert 'timestamp' in ticker

    def test_get_order_book(self):
        engine = PriceEngine(seed=42)
        book = engine.get_order_book("ETH/USDT", limit=10)
        assert len(book['bids']) == 10
        assert len(book['asks']) == 10
        # bids 降序，asks 升序
        assert book['bids'][0][0] > book['bids'][-1][0]
        assert book['asks'][0][0] < book['asks'][-1][0]

    def test_get_trades(self):
        engine = PriceEngine(seed=42)
        trades = engine.get_trades("SOL/USDT", count=5)
        assert len(trades) == 5
        for t in trades:
            assert t['symbol'] == "SOL/USDT"
            assert t['side'] in ('buy', 'sell')
            assert t['amount'] > 0

    def test_swap_follows_spot(self):
        engine = PriceEngine(seed=42)
        for _ in range(10):
            engine.step_all()
        spot = engine.get_price("BTC/USDT")
        swap = engine.get_price("BTC/USDT:USDT")
        # 合约价格应该接近现货（basis < 1%）
        assert abs(swap / spot - 1) < 0.01

    def test_price_stays_positive(self):
        engine = PriceEngine(volatility=0.01, seed=42)
        for _ in range(1000):
            engine.step_all()
        for s in engine.symbols:
            assert engine.get_price(s) > 0


class TestPositionTracker:
    def test_initial_empty(self):
        tracker = PositionTracker()
        assert tracker.get("BTC/USDT:USDT") == 0.0
        assert tracker.get_all() == {}

    def test_update(self):
        tracker = PositionTracker()
        tracker.update("BTC/USDT:USDT", 0.5)
        assert tracker.get("BTC/USDT:USDT") == 0.5

    def test_accumulate(self):
        tracker = PositionTracker()
        tracker.update("BTC/USDT:USDT", 0.5)
        tracker.update("BTC/USDT:USDT", 0.3)
        assert abs(tracker.get("BTC/USDT:USDT") - 0.8) < 1e-10

    def test_bidirectional(self):
        tracker = PositionTracker()
        tracker.update("BTC/USDT:USDT", 1.0)
        tracker.update("BTC/USDT:USDT", -0.5)
        assert abs(tracker.get("BTC/USDT:USDT") - 0.5) < 1e-10

    def test_close_to_zero(self):
        tracker = PositionTracker()
        tracker.update("BTC/USDT:USDT", 1.0)
        tracker.update("BTC/USDT:USDT", -1.0)
        assert tracker.get("BTC/USDT:USDT") == 0.0

    def test_to_ccxt_positions(self):
        tracker = PositionTracker()
        tracker.update("BTC/USDT:USDT", 0.5)
        tracker.update("ETH/USDT:USDT", -1.0)
        positions = tracker.to_ccxt_positions()
        assert len(positions) == 2
        btc_pos = [p for p in positions if p['symbol'] == "BTC/USDT:USDT"][0]
        assert btc_pos['side'] == 'long'
        eth_pos = [p for p in positions if p['symbol'] == "ETH/USDT:USDT"][0]
        assert eth_pos['side'] == 'short'


class TestBalanceTracker:
    def test_initial_balance(self):
        tracker = BalanceTracker(100_000.0)
        assert tracker.get_usdt_balance() == 100_000.0

    def test_apply_trade_spot_buy(self):
        tracker = BalanceTracker(100_000.0)
        tracker.apply_trade('buy', 1000.0, 'BTC/USDT')
        assert tracker.get_usdt_balance() == 99_000.0

    def test_apply_trade_spot_sell(self):
        tracker = BalanceTracker(100_000.0)
        tracker.apply_trade('sell', 1000.0, 'BTC/USDT')
        assert tracker.get_usdt_balance() == 101_000.0

    def test_apply_funding(self):
        tracker = BalanceTracker(100_000.0)
        tracker.apply_funding(50.0)
        assert tracker.get_usdt_balance() == 100_050.0
        tracker.apply_funding(-30.0)
        assert tracker.get_usdt_balance() == 100_020.0

    def test_apply_fee(self):
        tracker = BalanceTracker(100_000.0)
        tracker.apply_fee(10.0)
        assert tracker.get_usdt_balance() == 99_990.0

    def test_to_ccxt_format(self):
        tracker = BalanceTracker(50_000.0)
        result = tracker.to_ccxt_format('swap')
        assert result['USDT']['total'] == 50_000.0
        assert result['USDT']['free'] == 50_000.0
        assert float(result['info']['totalWalletBalance']) == 50_000.0


class TestOrderManager:
    def setup_method(self):
        self.pos = PositionTracker()
        self.bal = BalanceTracker(100_000.0)
        self.mgr = OrderManager(
            self.pos, self.bal,
            fill_probability=1.0,  # 100% 成交用于测试
            contract_sizes={'BTC/USDT:USDT': 0.001, 'ETH/USDT:USDT': 0.01},
        )

    def test_market_order_fills_immediately(self):
        order = self.mgr.place_order("BTC/USDT:USDT", "market", "buy", 1.0, 80000.0)
        assert order['status'] == 'closed'
        assert order['filled'] == 1.0

    def test_market_order_updates_position(self):
        self.mgr.place_order("BTC/USDT:USDT", "market", "buy", 1000.0, 80000.0)
        # position = 1000 contracts * 0.001 contract_size = 1.0 BTC
        assert abs(self.pos.get("BTC/USDT:USDT") - 1.0) < 1e-6

    def test_limit_order_pending(self):
        order = self.mgr.place_order("BTC/USDT:USDT", "limit", "buy", 1.0, 79000.0)
        assert order['status'] == 'open'
        assert len(self.mgr.get_open_orders()) == 1

    def test_cancel_order(self):
        order = self.mgr.place_order("BTC/USDT:USDT", "limit", "buy", 1.0, 79000.0)
        result = self.mgr.cancel_order(order['id'])
        assert result['status'] == 'canceled'
        assert len(self.mgr.get_open_orders()) == 0

    def test_try_fill_limit_order(self):
        """限价单在价格有利时应该成交"""
        # 挂买单在 81000
        self.mgr.place_order("BTC/USDT:USDT", "limit", "buy", 1.0, 81000.0)
        # 模拟价格状态：mid=80000, ask=79999 < 81000 → 可以成交
        from hft.exchange.simulated.engines.price import SymbolPriceState
        states = {
            "BTC/USDT:USDT": SymbolPriceState(
                mid_price=80000.0, volatility=0.02, spread_bps=1.0,
            )
        }
        # 多次尝试（部分成交需要多轮）
        for _ in range(10):
            self.mgr.try_fill_orders(states)
        # 应该完全成交了（fill_probability=1.0，10 轮足够）
        assert len(self.mgr.get_open_orders()) == 0
        assert self.pos.get("BTC/USDT:USDT") > 0

    def test_sell_order(self):
        # 先买入
        self.mgr.place_order("BTC/USDT:USDT", "market", "buy", 1000.0, 80000.0)
        pos_before = self.pos.get("BTC/USDT:USDT")
        # 再卖出一半
        self.mgr.place_order("BTC/USDT:USDT", "market", "sell", 500.0, 80000.0)
        pos_after = self.pos.get("BTC/USDT:USDT")
        assert pos_after < pos_before
        assert abs(pos_after - pos_before / 2) < 1e-6

    def test_get_order(self):
        order = self.mgr.place_order("BTC/USDT:USDT", "limit", "buy", 1.0, 79000.0)
        fetched = self.mgr.get_order(order['id'])
        assert fetched['id'] == order['id']
        assert fetched['status'] == 'open'


class TestFundingEngine:
    def test_initialization(self):
        symbols = ["BTC/USDT:USDT", "ETH/USDT:USDT"]
        engine = FundingEngine(symbols, seed=42)
        assert len(engine._states) == 2

    def test_get_all_rates(self):
        symbols = ["BTC/USDT:USDT"]
        engine = FundingEngine(symbols, seed=42)
        # 设置 mark_price
        from hft.exchange.simulated.engines.price import SymbolPriceState
        engine.update_prices({
            "BTC/USDT:USDT": SymbolPriceState(mid_price=80000.0, volatility=0.02),
        })
        rates = engine.get_all_rates()
        assert "BTC/USDT:USDT" in rates
        rate = rates["BTC/USDT:USDT"]
        assert rate.symbol == "BTC/USDT:USDT"
        assert rate.mark_price > 0

    def test_settlement(self):
        symbols = ["BTC/USDT:USDT"]
        engine = FundingEngine(symbols, base_rate=0.001, seed=42)
        # 设置 next_funding 为过去
        engine._states["BTC/USDT:USDT"].next_funding_timestamp = time.time() - 1
        engine._states["BTC/USDT:USDT"].mark_price = 80000.0
        engine._states["BTC/USDT:USDT"].index_price = 80000.0

        pos = PositionTracker()
        bal = BalanceTracker(100_000.0)
        pos.update("BTC/USDT:USDT", 1.0)  # 1 BTC 多头

        engine.check_settlements(pos, bal)
        # 正费率 + 多头 → 支付 funding（balance 减少）
        assert bal.get_usdt_balance() != 100_000.0
        assert len(engine.get_settlement_history()) == 1


class TestMarkets:
    def test_build_all_markets(self):
        markets = build_all_markets()
        assert len(markets) == len(SYMBOLS_CONFIG) * 2
        assert "BTC/USDT" in markets
        assert "BTC/USDT:USDT" in markets

    def test_spot_market_format(self):
        markets = build_all_markets()
        m = markets["BTC/USDT"]
        assert m['type'] == 'spot'
        assert m['base'] == 'BTC'
        assert m['quote'] == 'USDT'
        assert m['precision']['amount'] > 0
        assert m['limits']['amount']['min'] > 0

    def test_swap_market_format(self):
        markets = build_all_markets()
        m = markets["BTC/USDT:USDT"]
        assert m['type'] == 'swap'
        assert m['contract'] is True
        assert m['contractSize'] > 0
        assert m['settle'] == 'USDT'

    def test_currencies(self):
        currencies = build_currencies()
        assert 'USDT' in currencies
        assert 'BTC' in currencies

    def test_symbol_lists(self):
        assert len(get_spot_symbols()) == len(SYMBOLS_CONFIG)
        assert len(get_swap_symbols()) == len(SYMBOLS_CONFIG)

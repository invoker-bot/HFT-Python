"""
TickerDataSource 单元测试

Feature 0006: Indicator 与 DataSource 统一架构
"""
# pylint: disable=import-outside-toplevel,protected-access
import time

from hft.indicator.datasource import TickerDataSource, TickerData


class TestTickerData:
    """TickerData 测试"""

    def test_from_ccxt(self):
        """测试从 ccxt 格式创建"""
        ccxt_data = {
            "symbol": "BTC/USDT:USDT",
            "timestamp": 1700000000000,  # 毫秒
            "last": 50000.0,
            "bid": 49999.0,
            "ask": 50001.0,
            "high": 51000.0,
            "low": 49000.0,
            "baseVolume": 1000.0,
            "quoteVolume": 50000000.0,
        }

        ticker = TickerData.from_ccxt(ccxt_data)

        assert ticker.symbol == "BTC/USDT:USDT"
        assert ticker.timestamp == 1700000000.0  # 转换为秒
        assert ticker.last == 50000.0
        assert ticker.bid == 49999.0
        assert ticker.ask == 50001.0

    def test_from_ccxt_with_none_values(self):
        """测试处理 None 值"""
        ccxt_data = {
            "symbol": "BTC/USDT:USDT",
            "timestamp": 1700000000000,
            "last": None,
            "bid": None,
            "ask": None,
        }

        ticker = TickerData.from_ccxt(ccxt_data)

        assert ticker.last == 0.0
        assert ticker.bid == 0.0
        assert ticker.ask == 0.0


class TestTickerDataSource:
    """TickerDataSource 测试"""

    def test_init(self):
        """测试初始化"""
        ds = TickerDataSource(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
        )

        assert ds.exchange_class == "okx"
        assert ds.symbol == "BTC/USDT:USDT"
        assert ds.mode == "watch"
        assert ds._ready_condition == "timeout < 10"

    def test_init_fetch_mode(self):
        """测试 fetch 模式初始化"""
        ds = TickerDataSource(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            mode="fetch",
            interval=1.0,
        )

        assert ds.mode == "fetch"
        assert ds.interval == 1.0

    def test_calculate_vars_empty(self):
        """测试无数据时 calculate_vars"""
        ds = TickerDataSource(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
        )

        vars = ds.calculate_vars(direction=1)
        assert vars == {}

    def test_calculate_vars_with_data(self):
        """测试有数据时 calculate_vars"""
        ds = TickerDataSource(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
        )

        ticker = TickerData(
            symbol="BTC/USDT:USDT",
            timestamp=time.time(),
            last=50000.0,
            bid=49999.0,
            ask=50001.0,
        )
        ds._data.append(ticker.timestamp, ticker)

        vars = ds.calculate_vars(direction=1)

        assert vars["last"] == 50000.0
        assert vars["bid"] == 49999.0
        assert vars["ask"] == 50001.0
        assert vars["mid"] == 50000.0
        assert abs(vars["spread"] - 0.00004) < 0.00001


# ============================================================
# 回归测试（Issue 0003）
# ============================================================

class TestRegressionIssue0003:
    """Issue 0003 回归测试"""

    def test_from_ccxt_timestamp_none_fallback(self):
        """
        回归测试：TickerData.from_ccxt timestamp=None 时回退到 time.time()

        Issue 0003 P0: timestamp 缺失或为 None 时应使用当前时间，
        且结果为 float 秒（非毫秒）。
        """
        before = time.time()

        ccxt_data = {
            "symbol": "BTC/USDT:USDT",
            "timestamp": None,
            "last": 50000.0,
        }
        ticker = TickerData.from_ccxt(ccxt_data)

        after = time.time()

        # timestamp 应在 before 和 after 之间
        assert before <= ticker.timestamp <= after
        # 应为秒级（非毫秒）
        assert ticker.timestamp < 1e12

    def test_from_ccxt_timestamp_missing_fallback(self):
        """
        回归测试：TickerData.from_ccxt timestamp 缺失时回退到 time.time()
        """
        before = time.time()

        ccxt_data = {
            "symbol": "BTC/USDT:USDT",
            "last": 50000.0,
            # 没有 timestamp 字段
        }
        ticker = TickerData.from_ccxt(ccxt_data)

        after = time.time()

        assert before <= ticker.timestamp <= after
        assert ticker.timestamp < 1e12


class TestRegressionFeature0006Phase2:
    """Feature 0006 Phase 2 回归测试：DataSource 迁移修复"""

    def test_trades_datasource_pickle_compatible(self):
        """
        回归测试：TradesDataSource 可以被 pickle 序列化

        Feature 0006 Phase 2: 使用 _never_duplicate 函数替代 lambda，
        确保 CacheListener 可以序列化整个 Listener 树。
        """
        import pickle
        from hft.indicator.datasource import TradesDataSource

        ds = TradesDataSource(exchange_class="okx", symbol="BTC/USDT:USDT")

        # 序列化和反序列化应该成功
        data = pickle.dumps(ds)
        ds2 = pickle.loads(data)

        assert ds2.name == ds.name
        assert ds2.exchange_class == ds.exchange_class
        assert ds2.symbol == ds.symbol

    def test_trade_data_timestamp_none_fallback(self):
        """
        回归测试：TradeData.from_ccxt timestamp=None 时回退到 time.time()
        """
        from hft.indicator.datasource import TradeData

        before = time.time()
        ccxt_data = {
            "symbol": "BTC/USDT:USDT",
            "timestamp": None,
            "price": 50000.0,
            "amount": 1.0,
            "side": "buy",
        }
        trade = TradeData.from_ccxt(ccxt_data)
        after = time.time()

        assert before <= trade.timestamp <= after
        assert trade.timestamp < 1e12

    def test_orderbook_data_timestamp_none_fallback(self):
        """
        回归测试：OrderBookData.from_ccxt timestamp=None 时回退到 time.time()
        """
        from hft.indicator.datasource import OrderBookData

        before = time.time()
        ccxt_data = {
            "symbol": "BTC/USDT:USDT",
            "timestamp": None,
            "bids": [[50000.0, 1.0]],
            "asks": [[50001.0, 1.0]],
        }
        ob = OrderBookData.from_ccxt(ccxt_data)
        after = time.time()

        assert before <= ob.timestamp <= after
        assert ob.timestamp < 1e12

    def test_candle_data_timestamp_none_fallback(self):
        """
        回归测试：CandleData.from_ccxt timestamp=None 时回退到 time.time()
        """
        from hft.indicator.datasource import CandleData

        before = time.time()
        # ccxt OHLCV 格式: [timestamp, open, high, low, close, volume]
        ccxt_data = [None, 50000.0, 51000.0, 49000.0, 50500.0, 1000.0]
        candle = CandleData.from_ccxt(ccxt_data)
        after = time.time()

        assert before <= candle.timestamp <= after
        assert candle.timestamp < 1e12

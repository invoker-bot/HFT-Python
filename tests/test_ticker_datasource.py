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

        ticker = TickerData.from_ccxt(ccxt_data, contract_size=1.0)

        assert ticker.timestamp == 1700000000.0  # 转换为秒
        assert ticker.last == 50000.0
        assert ticker.bid == 49999.0
        assert ticker.ask == 50001.0

    def test_from_ccxt_with_contract_size(self):
        """测试 contract_size 应用到 baseVolume"""
        ccxt_data = {
            "symbol": "BTC/USDT:USDT",
            "timestamp": 1700000000000,
            "last": 50000.0,
            "bid": 49999.0,
            "ask": 50001.0,
            "baseVolume": 1000.0,
            "quoteVolume": 50000000.0,
        }

        ticker = TickerData.from_ccxt(ccxt_data, contract_size=0.01)

        assert ticker.amount == 1000.0 * 0.01

    def test_mid_price(self):
        """测试中间价计算"""
        ticker = TickerData(
            timestamp=time.time(),
            last=50000.0,
            bid=49999.0,
            ask=50001.0,
        )
        assert ticker.mid_price == 50000.0


class TestRegressionIssue0003:
    """Issue 0003 回归测试"""
    pass


class TestRegressionFeature0006Phase2:
    """Feature 0006 Phase 2 回归测试：DataSource 迁移修复"""

    def test_trades_datasource_pickle_compatible(self):
        """
        回归测试：TradesDataSource 可以被 pickle 序列化
        """
        import pickle
        from hft.indicator.datasource import TradesDataSource

        ds = TradesDataSource(exchange_class="okx", symbol="BTC/USDT:USDT")

        # 序列化和反序列化应该成功
        data = pickle.dumps(ds)
        ds2 = pickle.loads(data)

        assert ds2.name == ds.name

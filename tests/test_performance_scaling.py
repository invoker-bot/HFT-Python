"""
性能测试套件

测试目标：
1. Strategy 处理复杂度为 O(n)
2. Executor 处理复杂度为 O(1)
3. 内存消耗为 O(n*m)，不随时间增长
4. 网络请求去重
5. 资源正确释放
"""
import time
import pytest
from hft.exchange.demo.mock_exchange import MockExchange, MockExchangeConfig


class TestMockExchange:
    """测试 MockExchange 基础功能"""

    def test_load_markets(self):
        """测试生成模拟交易对"""
        config = MockExchangeConfig(path="demo/mock", num_markets=50)
        exchange = MockExchange(config)

        markets = exchange.load_markets()

        assert len(markets) == 50
        assert "MOCK0/USDT" in markets
        assert "MOCK49/USDT" in markets

    def test_fetch_ticker(self):
        """测试获取 ticker 数据"""
        config = MockExchangeConfig(path="demo/mock", num_markets=10)
        exchange = MockExchange(config)
        exchange.load_markets()

        ticker = exchange.fetch_ticker("MOCK0/USDT")

        assert ticker['symbol'] == "MOCK0/USDT"
        assert 'bid' in ticker
        assert 'ask' in ticker
        assert ticker['bid'] < ticker['ask']

    def test_api_call_recording(self):
        """测试 API 调用记录"""
        config = MockExchangeConfig(path="demo/mock", num_markets=10)
        exchange = MockExchange(config)

        exchange.load_markets()
        exchange.fetch_ticker("MOCK0/USDT")
        exchange.fetch_ticker("MOCK1/USDT")

        assert exchange.get_api_call_count('load_markets') == 1
        assert exchange.get_api_call_count('fetch_ticker') == 2

    def test_fake_time(self):
        """测试时间加速"""
        config = MockExchangeConfig(path="demo/mock", num_markets=10)
        exchange = MockExchange(config)

        initial_time = exchange.get_current_time()
        exchange.advance_time(100.0)
        new_time = exchange.get_current_time()

        assert new_time - initial_time == 100.0


class TestAPICallComplexity:
    """测试 API 调用复杂度"""

    def test_fetch_tickers_called_once(self):
        """验证 fetch_tickers 只调用一次，不随 n 增长"""
        for n in [50, 200]:
            config = MockExchangeConfig(path="demo/mock", num_markets=n)
            exchange = MockExchange(config)
            exchange.load_markets()
            exchange.clear_api_calls()

            # 调用 fetch_tickers
            exchange.fetch_tickers()

            # 验证：只调用一次
            assert exchange.get_api_call_count('fetch_tickers') == 1

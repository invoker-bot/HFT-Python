"""
Mock Exchange - 用于性能测试

提供模拟的交易所实现，支持：
- 生成任意数量的模拟交易对
- 记录所有 API 调用
- Fake time 时间加速
- 模拟 ticker/orderbook/trades 数据
"""
import asyncio
import time
from collections import defaultdict
from typing import Optional

from ..base import BaseExchange, MarketTradingPair, TradeType
from ..config import BaseExchangeConfig


class MockExchangeConfig(BaseExchangeConfig):
    """Mock Exchange 配置"""
    num_markets: int = 100  # 模拟交易对数量


class MockExchange(BaseExchange):
    """
    Mock 交易所，用于性能测试

    特点：
    - 返回大量模拟的 markets/tickers/orderbook 数据
    - 支持 watch_ticker/watch_orderbook 等异步方法
    - 记录所有 API 调用次数和参数
    - 支持 fake time 加速时间流逝
    """
    class_name = "mock"

    def __init__(self, config: MockExchangeConfig):
        super().__init__(config)
        self.num_markets = config.num_markets
        self.api_calls = []  # 记录所有 API 调用: [(method, args, kwargs, timestamp)]
        self.fake_time = time.time()  # 模拟时间
        self.time_multiplier = 1.0  # 时间加速倍数
        self._watch_tasks = {}  # 记录 watch 任务

    def _record_api_call(self, method: str, *args, **kwargs):
        """记录 API 调用"""
        self.api_calls.append({
            'method': method,
            'args': args,
            'kwargs': kwargs,
            'timestamp': self.get_current_time()
        })

    def get_current_time(self) -> float:
        """获取当前模拟时间"""
        return self.fake_time

    def advance_time(self, seconds: float):
        """推进模拟时间"""
        self.fake_time += seconds * self.time_multiplier

    def load_markets(self, reload: bool = False) -> dict:
        """
        生成 n 个模拟交易对

        Returns:
            markets dict: {symbol: market_info}
        """
        self._record_api_call('load_markets', reload=reload)

        if self._markets and not reload:
            return self._markets

        # 生成模拟交易对
        self._markets = {}
        self._market_trading_pairs = {}

        for i in range(self.num_markets):
            symbol = f"MOCK{i}/USDT"
            market_id = f"MOCK{i}USDT"

            # 创建 market info
            market = {
                'id': market_id,
                'symbol': symbol,
                'base': f'MOCK{i}',
                'quote': 'USDT',
                'type': 'spot',
                'spot': True,
                'active': True,
                'precision': {
                    'amount': 8,
                    'price': 2
                },
                'limits': {
                    'amount': {'min': 0.001, 'max': 10000},
                    'price': {'min': 0.01, 'max': 100000}
                }
            }

            self._markets[symbol] = market

            # 创建 MarketTradingPair
            trading_pair = MarketTradingPair(
                exchange=self.config.path,
                base=f'MOCK{i}',
                quote='USDT',
                trade_type=TradeType.SPOT,
                id=symbol
            )
            self._market_trading_pairs[symbol] = trading_pair

        return self._markets

    def fetch_ticker(self, symbol: str) -> dict:
        """
        返回模拟 ticker 数据

        Args:
            symbol: 交易对符号

        Returns:
            ticker dict
        """
        self._record_api_call('fetch_ticker', symbol)

        # 生成模拟 ticker 数据
        base_price = 100.0 + hash(symbol) % 1000
        timestamp = self.get_current_time() * 1000

        return {
            'symbol': symbol,
            'timestamp': timestamp,
            'datetime': None,
            'high': base_price * 1.05,
            'low': base_price * 0.95,
            'bid': base_price * 0.999,
            'ask': base_price * 1.001,
            'last': base_price,
            'close': base_price,
            'baseVolume': 1000.0,
            'quoteVolume': base_price * 1000.0,
            'info': {}
        }

    def fetch_tickers(self, symbols: Optional[list[str]] = None) -> dict:
        """
        批量获取 ticker 数据

        Args:
            symbols: 交易对列表，None 表示所有交易对

        Returns:
            {symbol: ticker} dict
        """
        self._record_api_call('fetch_tickers', symbols)

        if symbols is None:
            symbols = list(self._markets.keys())

        return {symbol: self.fetch_ticker(symbol) for symbol in symbols}

    async def watch_ticker(self, symbol: str) -> dict:
        """
        模拟 watch ticker（异步订阅）

        Args:
            symbol: 交易对符号

        Returns:
            ticker dict
        """
        self._record_api_call('watch_ticker', symbol)

        # 记录 watch 任务
        task_key = f'watch_ticker:{symbol}'
        if task_key not in self._watch_tasks:
            self._watch_tasks[task_key] = True

        # 模拟异步等待
        await asyncio.sleep(0.01)

        return self.fetch_ticker(symbol)

    def get_api_call_count(self, method: str) -> int:
        """获取指定方法的调用次数"""
        return sum(1 for call in self.api_calls if call['method'] == method)

    def get_active_watch_count(self) -> int:
        """获取活跃的 watch 任务数量"""
        return len(self._watch_tasks)

    def clear_api_calls(self):
        """清空 API 调用记录"""
        self.api_calls.clear()

    def medal_fetch_funding_rates(self, symbols: Optional[list[str]] = None) -> list:
        """模拟获取资金费率"""
        self._record_api_call('medal_fetch_funding_rates', symbols)
        return []

    def medal_fetch_funding_rates_history(self, symbol: str, since: Optional[int] = None, limit: Optional[int] = None) -> list:
        """模拟获取资金费率历史"""
        self._record_api_call('medal_fetch_funding_rates_history', symbol, since, limit)
        return []

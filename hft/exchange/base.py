"""
交易所基类

提供统一的交易所 API 封装，支持：
- 市场数据（ticker, orderbook, trades, ohlcv）
- 交易方法（下单、撤单、查询）
- 账户方法（余额、持仓）
- 期货方法（杠杆、保证金模式）
- 资金费率
- WebSocket 订阅
- 状态持久化
"""
import os
import math
import time
import asyncio
import pickle
import logging
from abc import abstractmethod
from datetime import datetime, timedelta
from functools import cached_property
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional, ClassVar, TYPE_CHECKING
from ccxt.pro import Exchange
from ccxt.base.errors import InvalidOrder
from cachetools import TTLCache
from cachetools_async import cached
from ..core.listener import Listener

if TYPE_CHECKING:
    from .config import BaseExchangeConfig


logger = logging.getLogger(__name__)


def sign(x: float) -> int:
    """返回数值的符号"""
    if x > 0:
        return 1
    elif x < 0:
        return -1
    return 0


@dataclass
class FundingRate:
    """资金费率数据"""
    exchange: str
    symbol: str                         # 交易对 (如 BTC/USDT:USDT)
    funding_rate: float                 # 当前资金费率
    next_funding_rate: Optional[float]  # 预测下次资金费率
    funding_timestamp: float            # 下次结算时间戳
    funding_interval_hours: int         # 结算间隔（小时）
    mark_price: float                   # 标记价格
    index_price: float                  # 指数价格
    min_funding_rate: float = -0.03     # 最小资金费率
    max_funding_rate: float = 0.03      # 最大资金费率

    @property
    def seconds_until_funding(self) -> float:
        """距离下次结算的秒数"""
        return max(self.funding_timestamp - time.time(), 0.0)

    @property
    def daily_funding_rate(self) -> float:
        """日化资金费率"""
        return self.funding_rate * (24 / self.funding_interval_hours)

    @property
    def annual_funding_rate(self) -> float:
        """年化资金费率"""
        return self.daily_funding_rate * 365

    @property
    def basis(self) -> float:
        """基差 (mark - index) / index"""
        if self.index_price == 0:
            return 0.0
        return (self.mark_price - self.index_price) / self.index_price


@dataclass
class FundingRateBill:
    """资金费率账单"""
    id: str
    symbol: str
    funding_time: float
    funding_amount: float


class TickHistory:
    """时间序列数据历史记录"""

    def __init__(self, max_size: int = 10000):
        self._data: list[tuple[float, float]] = []  # [(timestamp, value), ...]
        self._max_size = max_size

    def append(self, timestamp: float, value: float) -> None:
        """添加数据点"""
        self._data.append((timestamp, value))
        if len(self._data) > self._max_size:
            self._data = self._data[-self._max_size:]

    def shrink(self, before_timestamp: float) -> None:
        """删除指定时间戳之前的数据"""
        self._data = [(t, v) for t, v in self._data if t >= before_timestamp]

    @property
    def current_time(self) -> float:
        """最新数据时间戳"""
        if not self._data:
            return 0.0
        return self._data[-1][0]

    @property
    def best_time(self) -> float:
        """最早数据时间戳"""
        if not self._data:
            return 0.0
        return self._data[0][0]

    @property
    def current_value(self) -> Optional[float]:
        """最新值"""
        if not self._data:
            return None
        return self._data[-1][1]

    def get_range(self, start: float, end: float, min_count: int = 1) -> Optional[float]:
        """获取时间范围内的数据覆盖率"""
        count = sum(1 for t, _ in self._data if start <= t <= end)
        if count < min_count:
            return None
        return count / max(1, (end - start))

    def get_interpolate(self, timestamps: list[float]) -> list[float]:
        """在指定时间戳插值"""
        import numpy as np
        if not self._data:
            return [0.0] * len(timestamps)
        times = [t for t, _ in self._data]
        values = [v for _, v in self._data]
        return list(np.interp(timestamps, times, values))


class BaseExchange(Listener):
    """
    交易所基类

    提供统一的交易所 API 封装
    """
    class_name: ClassVar[str] = "base_exchange"

    def __init__(self, config: "BaseExchangeConfig"):
        super().__init__(name=config.class_name if hasattr(config, 'class_name') else "exchange")
        self.config = config

        # 内部状态
        self._swaps: Optional[dict] = None
        self._markets: Optional[dict] = None

        # 价格缓存
        self._index_prices_cache: dict[str, TickHistory] = defaultdict(TickHistory)
        self._mark_prices_cache: dict[str, TickHistory] = defaultdict(TickHistory)
        self._funding_rates_cache: dict[str, TickHistory] = defaultdict(TickHistory)

    @property
    def exchange(self) -> Exchange:
        """获取底层 ccxt 交易所实例"""
        return self.config.ccxt_instance

    @property
    def exchange_id(self) -> str:
        """交易所 ID"""
        return self.exchange.id

    # ========== 生命周期 ==========

    async def on_start(self) -> None:
        """启动时加载市场数据"""
        self.load_state()
        await self.load_time_diff()
        await self.load_markets()

    async def on_stop(self) -> None:
        """停止时保存状态"""
        self.save_state()
        await self.close()

    async def tick_callback(self) -> bool:
        """每 tick 刷新数据"""
        await self.load_time_diff()
        await self.load_swaps()
        return True

    async def on_health_check(self) -> None:
        """健康检查"""
        self._health = self.ready

    # ========== 市场数据方法 ==========

    async def fetch_ticker(self, symbol: str) -> dict:
        """
        获取 ticker 数据

        Returns:
            {
                'symbol': 'BTC/USDT',
                'timestamp': 1678886400000,
                'high': 27500.00,
                'low': 26800.00,
                'bid': 27100.00,
                'ask': 27105.00,
                'last': 27100.00,
                'baseVolume': 1234.56,
                'quoteVolume': 33567890.12,
                ...
            }
        """
        return await self.exchange.fetch_ticker(symbol)

    async def fetch_tickers(self, symbols: Optional[list[str]] = None) -> dict:
        """获取多个 ticker"""
        return await self.exchange.fetch_tickers(symbols)

    async def fetch_order_book(self, symbol: str, limit: Optional[int] = None) -> dict:
        """
        获取订单簿

        Returns:
            {
                'symbol': 'BTC/USDT',
                'bids': [[price, amount], ...],
                'asks': [[price, amount], ...],
                'timestamp': 1678886400000,
                ...
            }
        """
        return await self.exchange.fetch_order_book(symbol, limit)

    async def fetch_trades(
        self,
        symbol: str,
        since: Optional[int] = None,
        limit: Optional[int] = None
    ) -> list:
        """获取成交记录"""
        return await self.exchange.fetch_trades(symbol, since, limit)

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = '1m',
        since: Optional[int] = None,
        limit: Optional[int] = None
    ) -> list:
        """
        获取 K 线数据

        Returns:
            [[timestamp, open, high, low, close, volume], ...]
        """
        return await self.exchange.fetch_ohlcv(symbol, timeframe, since, limit)

    # ========== 交易方法 ==========

    async def place_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        price: Optional[float] = None,
        params: Optional[dict] = None,
    ) -> Optional[dict]:
        """
        下单

        Args:
            symbol: 交易对
            order_type: 'market' 或 'limit'
            side: 'buy' 或 'sell'
            amount: 数量
            price: 价格（限价单必填）
            params: 额外参数

        Returns:
            订单信息
        """
        if self._swaps is None:
            await self.load_swaps()

        swap = self._swaps.get(symbol)
        if swap is None:
            logger.warning(f"[{self.class_name}] Symbol {symbol} not found in swaps")
            return None

        # 精度处理
        precision = swap["precision"]['amount']
        contract_size = float(swap.get('contractSize', 1))
        if contract_size > 1e-8:
            amount = amount / contract_size

        precision_decimals = round(-math.log10(precision)) if precision > 0 else 0
        aligned_amount = round(amount, precision_decimals)

        # 最小数量检查
        limit_amount_min = swap['limits']['amount']['min'] or precision
        limit_cost_min = swap['limits']['cost']['min'] or 5

        if abs(aligned_amount) < limit_amount_min:
            return None

        if price and abs(aligned_amount * price) < limit_cost_min:
            return None

        # 下单
        if not self.config.debug:
            try:
                order = await self.exchange.create_order(
                    symbol, order_type, side, abs(aligned_amount),
                    price=price, params=params or {}
                )
                logger.info(
                    f"[{self.class_name}] Placed {side} {order_type} order: "
                    f"{symbol} {aligned_amount} @ {price or 'market'}"
                )
                return order
            except InvalidOrder as e:
                logger.error(f"[{self.class_name}] Invalid order: {e}")
                return None
        else:
            logger.info(
                f"[{self.class_name}] [DEBUG] Would place {side} {order_type} order: "
                f"{symbol} {aligned_amount} @ {price or 'market'}"
            )
            return None

    async def place_order_smart(
        self,
        symbol: str,
        order_type: str,
        amount: float,
        position_amount: float,
        price: Optional[float] = None,
    ) -> Optional[dict]:
        """
        智能下单（自动判断买卖方向和减仓）

        Args:
            symbol: 交易对
            order_type: 'market' 或 'limit'
            amount: 数量（正数买入，负数卖出）
            position_amount: 当前持仓数量
            price: 价格
        """
        if self._swaps is None:
            await self.load_swaps()

        swap = self._swaps.get(symbol)
        if swap is None:
            return None

        precision = swap["precision"]['amount']
        contract_size = float(swap.get('contractSize', 1))
        if contract_size > 1e-8:
            amount = amount / contract_size

        precision_decimals = round(-math.log10(precision)) if precision > 0 else 0
        aligned_amount = round(amount, precision_decimals)

        # 判断是否减仓
        params = {}
        if position_amount * aligned_amount < -1e-6:
            # 反向操作 = 减仓
            aligned_amount = sign(aligned_amount) * min(
                abs(position_amount) + abs(precision),
                abs(aligned_amount)
            )
            if abs(aligned_amount) < precision:
                return None
            params = {"reduceOnly": True}

        side = "buy" if aligned_amount > 0 else "sell"
        return await self.place_order(symbol, order_type, side, abs(aligned_amount), price, params)

    async def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> dict:
        """撤销订单"""
        return await self.exchange.cancel_order(order_id, symbol)

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> list:
        """撤销所有订单"""
        return await self.exchange.cancel_all_orders(symbol)

    async def fetch_order(self, order_id: str, symbol: Optional[str] = None) -> dict:
        """查询订单"""
        return await self.exchange.fetch_order(order_id, symbol)

    async def fetch_open_orders(
        self,
        symbol: Optional[str] = None,
        since: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list:
        """查询未完成订单"""
        return await self.exchange.fetch_open_orders(symbol, since, limit)

    async def fetch_closed_orders(
        self,
        symbol: Optional[str] = None,
        since: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list:
        """查询已完成订单"""
        return await self.exchange.fetch_closed_orders(symbol, since, limit)

    # ========== 账户方法 ==========

    async def fetch_balance(self) -> dict:
        """
        获取账户余额

        Returns:
            {
                'BTC': {'free': 1.5, 'used': 0.5, 'total': 2.0},
                'USDT': {'free': 10000.00, 'used': 5000.00, 'total': 15000.00},
                ...
            }
        """
        return await self.exchange.fetch_balance()

    async def fetch_positions(self, symbols: Optional[list[str]] = None) -> list:
        """
        获取持仓

        Returns:
            [
                {
                    'symbol': 'BTC/USDT:USDT',
                    'side': 'long',
                    'contracts': 0.5,
                    'entryPrice': 27000.00,
                    'unrealizedPnl': 100.00,
                    'leverage': 10,
                    ...
                },
                ...
            ]
        """
        return await self.exchange.fetch_positions(symbols)

    async def fetch_position(self, symbol: str) -> Optional[dict]:
        """获取单个持仓"""
        positions = await self.fetch_positions([symbol])
        return positions[0] if positions else None

    async def fetch_positions_usd_value(
        self,
        symbols: Optional[list[str]] = None
    ) -> dict[str, dict]:
        """
        获取持仓的 USD 估值

        Returns:
            {
                'BTC/USDT:USDT': {
                    'symbol': 'BTC/USDT:USDT',
                    'side': 'long',
                    'contracts': 0.5,
                    'notional': 13500.00,        # 仓位价值 (USD)
                    'unrealizedPnl': 100.00,     # 未实现盈亏 (USD)
                    'markPrice': 27000.00,
                    'entryPrice': 26800.00,
                    'leverage': 10,
                },
                ...
                '_total': {
                    'notional': 15000.00,        # 总仓位价值
                    'unrealizedPnl': 150.00,     # 总未实现盈亏
                    'long_notional': 13500.00,   # 多头仓位价值
                    'short_notional': 1500.00,   # 空头仓位价值
                }
            }
        """
        positions = await self.fetch_positions(symbols)
        result = {}
        total_notional = 0.0
        total_pnl = 0.0
        long_notional = 0.0
        short_notional = 0.0

        for pos in positions:
            contracts = pos.get('contracts', 0)
            if contracts == 0:
                continue

            symbol = pos['symbol']
            side = pos.get('side', 'long' if contracts > 0 else 'short')

            # 优先使用 ccxt 返回的 notional
            notional = pos.get('notional')
            if notional is None:
                # 手动计算: contracts * contractSize * markPrice
                contract_size = float(pos.get('contractSize', 1) or 1)
                mark_price = float(pos.get('markPrice', 0) or 0)
                notional = abs(contracts) * contract_size * mark_price

            notional = abs(float(notional or 0))
            unrealized_pnl = float(pos.get('unrealizedPnl', 0) or 0)

            result[symbol] = {
                'symbol': symbol,
                'side': side,
                'contracts': contracts,
                'notional': notional,
                'unrealizedPnl': unrealized_pnl,
                'markPrice': pos.get('markPrice'),
                'entryPrice': pos.get('entryPrice'),
                'leverage': pos.get('leverage'),
                'liquidationPrice': pos.get('liquidationPrice'),
                'percentage': pos.get('percentage'),  # PnL 百分比
            }

            total_notional += notional
            total_pnl += unrealized_pnl
            if side == 'long':
                long_notional += notional
            else:
                short_notional += notional

        result['_total'] = {
            'notional': total_notional,
            'unrealizedPnl': total_pnl,
            'long_notional': long_notional,
            'short_notional': short_notional,
            'position_count': len(result),
        }

        return result

    async def fetch_account_usd_value(self) -> float:
        """
        获取账户 USD 价值（稳定币余额 + 未实现盈亏）
        """
        total = 0.0
        stablecoins = {'USDT', 'USDC', 'BUSD', 'DAI', 'TUSD', 'USDP', 'USD'}

        # 1. 稳定币余额
        balance = await self.fetch_balance()
        for currency in stablecoins:
            if currency in balance and isinstance(balance[currency], dict):
                total += float(balance[currency].get('total', 0) or 0)

        # 2. 仓位未实现盈亏
        try:
            positions = await self.fetch_positions()
            for pos in positions:
                if pos.get('contracts', 0) != 0:
                    total += float(pos.get('unrealizedPnl', 0) or 0)
        except Exception:
            pass

        return total

    async def fetch_my_trades(
        self,
        symbol: Optional[str] = None,
        since: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list:
        """获取我的成交记录"""
        return await self.exchange.fetch_my_trades(symbol, since, limit)

    # ========== 期货方法 ==========

    async def set_leverage(self, symbol: str, leverage: int) -> dict:
        """设置杠杆"""
        return await self.exchange.set_leverage(leverage, symbol)

    async def set_margin_mode(self, symbol: str, margin_mode: str) -> dict:
        """
        设置保证金模式

        Args:
            symbol: 交易对
            margin_mode: 'cross' 或 'isolated'
        """
        return await self.exchange.set_margin_mode(margin_mode, symbol)

    async def initialize_symbol(self, symbol: str, leverage: Optional[int] = None) -> None:
        """初始化交易对（设置杠杆和保证金模式）"""
        if self._swaps is None:
            await self.load_swaps()

        swap = self._swaps.get(symbol)
        if swap is None:
            return

        max_leverage = swap['limits']['leverage']['max'] or 125
        target_leverage = min(leverage or self.config.leverage or 10, max_leverage)

        try:
            await self.set_margin_mode(symbol, self.config.margin_mode or 'cross')
            await self.set_leverage(symbol, target_leverage)
            logger.info(f"[{self.class_name}] Initialized {symbol} with {target_leverage}x leverage")
        except Exception as e:
            logger.warning(f"[{self.class_name}] Failed to initialize {symbol}: {e}")

    # ========== 资金费率方法 ==========

    async def fetch_funding_rate(self, symbol: str) -> dict:
        """获取资金费率"""
        return await self.exchange.fetch_funding_rate(symbol)

    async def fetch_funding_rate_history(
        self,
        symbol: str,
        since: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list:
        """获取历史资金费率"""
        return await self.exchange.fetch_funding_rate_history(symbol, since, limit)

    async def fetch_funding_rates(self) -> dict[str, FundingRate]:
        """
        获取所有交易对的资金费率

        子类应该覆盖此方法
        """
        return {}

    async def fetch_funding_rates_history(self) -> list[FundingRateBill]:
        """
        获取资金费率账单

        子类应该覆盖此方法
        """
        return []

    # ========== 转账方法 ==========

    async def transfer(
        self,
        currency: str,
        amount: float,
        from_account: str,
        to_account: str,
    ) -> dict:
        """
        内部转账

        Args:
            currency: 币种
            amount: 数量
            from_account: 来源账户 ('spot', 'swap', 'future')
            to_account: 目标账户
        """
        return await self.exchange.transfer(currency, amount, from_account, to_account)

    # ========== WebSocket 方法 ==========

    async def watch_ticker(self, symbol: str) -> dict:
        """订阅 ticker"""
        return await self.exchange.watch_ticker(symbol)

    async def watch_order_book(self, symbol: str, limit: Optional[int] = None) -> dict:
        """订阅订单簿"""
        return await self.exchange.watch_order_book(symbol, limit)

    async def watch_trades(self, symbol: str) -> list:
        """订阅成交"""
        return await self.exchange.watch_trades(symbol)

    async def watch_ohlcv(self, symbol: str, timeframe: str = '1m') -> list:
        """订阅 K 线"""
        return await self.exchange.watch_ohlcv(symbol, timeframe)

    async def watch_orders(self, symbol: Optional[str] = None) -> list:
        """订阅订单更新"""
        return await self.exchange.watch_orders(symbol)

    async def watch_positions(self, symbols: Optional[list[str]] = None) -> list:
        """订阅持仓更新"""
        if hasattr(self.exchange, 'watch_positions'):
            return await self.exchange.watch_positions(symbols)
        return []

    # ========== 工具方法 ==========

    @cached(TTLCache(maxsize=32, ttl=300))
    async def load_time_diff(self) -> None:
        """加载时间差"""
        await self.exchange.load_time_difference()

    @cached(TTLCache(maxsize=32, ttl=300))
    async def load_markets(self, reload: bool = False) -> dict:
        """加载市场信息"""
        self._markets = await self.exchange.load_markets(reload)
        return self._markets

    @cached(TTLCache(maxsize=32, ttl=300))
    async def load_swaps(self) -> dict:
        """加载永续合约市场"""
        markets = await self.load_markets()
        self._swaps = {
            f"{item['base']}/{item['quote']}:{item['settle'] or item['quote']}": item
            for item in markets.values()
            if item.get('swap')
        }
        return self._swaps

    def market(self, symbol: str) -> Optional[dict]:
        """获取市场信息"""
        if self._markets:
            return self._markets.get(symbol)
        return None

    async def close(self) -> None:
        """关闭连接"""
        await self.exchange.close()

    # ========== 状态持久化 ==========

    def _get_cache_path(self) -> str:
        """缓存文件路径"""
        return f"data/{self.class_name}_cache.pkl"

    def save_state(self) -> None:
        """保存缓存到磁盘"""
        cache_path = self._get_cache_path()
        state = {
            'index_prices_cache': dict(self._index_prices_cache),
            'mark_prices_cache': dict(self._mark_prices_cache),
            'funding_rates_cache': dict(self._funding_rates_cache),
        }
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, 'wb') as f:
                pickle.dump(state, f)
            logger.info(f"[{self.class_name}] Saved cache to {cache_path}")
        except Exception as e:
            logger.warning(f"[{self.class_name}] Failed to save cache: {e}")

    def load_state(self) -> None:
        """从磁盘加载缓存"""
        cache_path = self._get_cache_path()
        if not os.path.exists(cache_path):
            return
        try:
            with open(cache_path, 'rb') as f:
                state = pickle.load(f)
            for key, value in state.get('index_prices_cache', {}).items():
                self._index_prices_cache[key] = value
            for key, value in state.get('mark_prices_cache', {}).items():
                self._mark_prices_cache[key] = value
            for key, value in state.get('funding_rates_cache', {}).items():
                self._funding_rates_cache[key] = value
            logger.info(f"[{self.class_name}] Loaded cache from {cache_path}")
        except Exception as e:
            logger.warning(f"[{self.class_name}] Failed to load cache: {e}")

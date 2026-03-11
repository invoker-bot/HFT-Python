"""
SimulatedExchange - 模拟交易所

完整模拟交易所实现，不依赖 ccxt 和网络。
通过 SimulatedCCXTExchange 桩对象拦截所有 ccxt 调用。
"""
import asyncio
import time
import logging
from collections import defaultdict
from functools import cached_property
from typing import ClassVar, Optional, TYPE_CHECKING

from ..base import BaseExchange, FundingRate, FundingRateBill, MarketTradingPair, TradeType
from ..config import BaseExchangeConfig
from ..listeners import (ExchangeStateListener, ExchangePositionListener,
                         ExchangeBalanceListener, ExchangeOrderBillListener,
                         ExchangeFundingRateBillListener)
from .engines import PriceEngine, FundingEngine, OrderManager, PositionTracker, BalanceTracker
from .markets import (build_all_markets, build_currencies, get_swap_symbols,
                      SYMBOLS_CONFIG)

if TYPE_CHECKING:
    from ...core.app.base import AppCore

logger = logging.getLogger(__name__)


class SimulatedCCXTExchange:
    """
    ccxt 桩对象

    拦截 BaseExchange 通过 self.exchanges[key] 发起的所有 ccxt 调用，
    委托给 SimulatedExchange 的内部引擎。
    """

    def __init__(self, exchange_id: str, exchange_type: str):
        self.id = exchange_id
        self.type = exchange_type
        # 在 SimulatedExchange.initialize() 中注入
        self._order_manager: Optional[OrderManager] = None
        self._balance_tracker: Optional[BalanceTracker] = None
        self._position_tracker: Optional[PositionTracker] = None
        self._contract_sizes: dict[str, float] = {}

    # ===== 订单 =====

    async def create_order(self, symbol, type, side, amount, price=None, params=None):
        async with self._order_manager._lock:
            return self._order_manager.place_order(symbol, type, side, amount, price, params)

    async def create_orders(self, order_params_list):
        results = []
        async with self._order_manager._lock:
            for p in order_params_list:
                r = self._order_manager.place_order(
                    p['symbol'], p['type'], p['side'], p['amount'],
                    p.get('price'), p.get('params'),
                )
                results.append(r)
        return results

    async def cancel_order(self, order_id, symbol=None):
        async with self._order_manager._lock:
            return self._order_manager.cancel_order(order_id)

    async def cancel_orders(self, order_ids, symbol=None):
        async with self._order_manager._lock:
            return self._order_manager.cancel_orders(order_ids, symbol)

    async def fetch_order(self, order_id, symbol=None):
        return self._order_manager.get_order(order_id)

    async def fetch_open_orders(self, symbol=None, since=None, limit=None):
        return self._order_manager.get_open_orders(symbol)

    async def fetch_closed_orders(self, symbol=None, since=None, limit=None):
        return [o.to_ccxt_order() for o in self._order_manager._closed_orders
                if symbol is None or o.symbol == symbol]

    async def watch_orders(self):
        return await self._order_manager.wait_for_updates()

    async def un_watch_orders(self):
        pass

    # ===== 余额 =====

    async def fetch_balance(self):
        if self._balance_tracker is None:
            return {'total': {}, 'free': {}, 'used': {}, 'info': {}}
        return self._balance_tracker.to_ccxt_format(self.type)

    async def watch_balance(self):
        await asyncio.sleep(0.1)
        return await self.fetch_balance()

    # ===== 持仓 =====

    async def fetch_positions(self, symbols=None):
        if self._position_tracker is None:
            return []
        return self._position_tracker.to_ccxt_positions(self._contract_sizes)

    async def watch_positions(self):
        await asyncio.sleep(0.1)
        return await self.fetch_positions()

    async def fetch_position(self, symbol):
        positions = await self.fetch_positions()
        return [p for p in positions if p['symbol'] == symbol]

    async def watch_position(self, symbol):
        await asyncio.sleep(1.0)
        return await self.fetch_position(symbol)

    async def un_watch_positions(self, symbols=None):
        pass

    # ===== 杠杆/保证金 =====

    async def set_leverage(self, leverage, symbol):
        pass

    async def set_margin_mode(self, mode, symbol, params=None):
        pass

    async def set_position_mode(self, hedged=False, symbol=None):
        pass

    # ===== 其他 =====

    async def close(self):
        pass

    async def load_markets(self, reload=False):
        return {}

    async def load_time_difference(self):
        pass

    async def fetch_currencies(self):
        return {}

    async def fetch_ticker(self, symbol):
        return {}

    async def fetch_my_trades(self, symbol=None, since=None, limit=None):
        return []

    async def fetch_funding_rate(self, symbol):
        return {}

    async def fetch_funding_rate_history(self, symbol=None, since=None, limit=None):
        return []

    async def transfer(self, currency, amount, from_account, to_account):
        pass


class SimulatedExchangeConfig(BaseExchangeConfig):
    """模拟交易所配置"""
    class_name: ClassVar[str] = "sim_base"

    # 模拟参数
    initial_balance_usdt: float = 100_000.0
    price_volatility: float = 0.001
    order_fill_probability: float = 0.5
    funding_base_rate: float = 0.0001
    funding_interval_hours: int = 8
    price_seed: Optional[int] = None

    @classmethod
    def get_class_type(cls):
        return SimulatedExchange

    @cached_property
    def ccxt_instances(self) -> dict[str, SimulatedCCXTExchange]:
        """返回桩对象，不创建真实 ccxt 连接"""
        return {
            t: SimulatedCCXTExchange(self.path or 'sim', t)
            for t in (self.support_types or ['swap'])
        }

    def ccxt_config_dicts(self) -> dict[str, dict]:
        return {}


class SimulatedExchange(BaseExchange):
    """
    模拟交易所

    完整模拟所有交易所功能：市场数据、下单、持仓、余额、资金费率。
    """
    class_name: ClassVar[str] = "sim_base"
    __pickle_exclude__ = {
        *BaseExchange.__pickle_exclude__,
        'price_engine', 'funding_engine', 'order_manager',
        'position_tracker', 'balance_tracker',
        '_sim_markets', '_sim_currencies', '_sim_contract_sizes',
    }

    def initialize(self, **kwargs):
        # 在 super().initialize() 之前准备市场数据（listeners 需要）
        self._sim_markets = build_all_markets()
        self._sim_currencies = build_currencies()
        self._sim_contract_sizes: dict[str, float] = {}
        for symbol, market in self._sim_markets.items():
            if market.get('contractSize'):
                self._sim_contract_sizes[symbol] = float(market['contractSize'])

        super().initialize(**kwargs)

        # 创建引擎
        self.price_engine = PriceEngine(
            volatility=self.config.price_volatility,
            seed=self.config.price_seed,
        )
        self.funding_engine = FundingEngine(
            swap_symbols=get_swap_symbols(),
            base_rate=self.config.funding_base_rate,
            interval_hours=self.config.funding_interval_hours,
        )
        self.position_tracker = PositionTracker()
        self.balance_tracker = BalanceTracker(self.config.initial_balance_usdt)
        self.order_manager = OrderManager(
            position_tracker=self.position_tracker,
            balance_tracker=self.balance_tracker,
            fill_probability=self.config.order_fill_probability,
            contract_sizes=self._sim_contract_sizes,
            rng=self.price_engine._rng,
            price_engine=self.price_engine,
        )

        # 注入引擎到 ccxt 桩对象
        for stub in self.config.ccxt_instances.values():
            stub._order_manager = self.order_manager
            stub._balance_tracker = self.balance_tracker
            stub._position_tracker = self.position_tracker
            stub._contract_sizes = self._sim_contract_sizes

    # ===== 连接生命周期 =====

    async def open(self):
        """不需要真实连接"""
        pass

    async def close(self):
        """不需要关闭连接"""
        pass

    async def load_time_diff(self):
        """不需要时间同步"""
        pass

    async def on_start(self):
        """启动时加载市场数据"""
        # 跳过 BaseExchange.on_start() 中的 open() 和 on_tick()
        # 直接初始化市场数据
        await self._markets.update(self._sim_markets)
        await self._currencies.update(self._sim_currencies)
        # 初始化持仓缓存（空仓）
        await self._positions.update({})
        # 初始化余额缓存
        for key in self.config.ccxt_instances:
            balance_data = self.balance_tracker.to_ccxt_format(key)
            transformed = {
                asset: info for asset, info in balance_data.items()
                if isinstance(info, dict) and 'free' in info
            }
            await self._balances[key].update(transformed)
        # 初始化 funding 价格
        self.funding_engine.update_prices(
            {s: self.price_engine.get_state(s) for s in self.price_engine.symbols}
        )

    async def on_stop(self):
        """停止"""
        pass

    async def on_tick(self):
        """每 tick 推进模拟"""
        # 推进价格
        self.price_engine.step_all()
        # 更新 funding 引擎价格
        self.funding_engine.update_prices(
            {s: self.price_engine.get_state(s) for s in self.price_engine.symbols}
        )
        # 检查 funding 结算
        self.funding_engine.check_settlements(self.position_tracker, self.balance_tracker)
        # 尝试成交挂单
        async with self.order_manager._lock:
            self.order_manager.try_fill_orders(
                {s: self.price_engine.get_state(s) for s in self.price_engine.symbols}
            )

    # ===== 市场数据 =====

    def load_markets(self, reload: bool = False) -> dict:
        """同步版本的市场数据加载"""
        return self._sim_markets

    async def load_markets_internal(self):
        return self._sim_markets, time.time()

    async def fetch_currencies(self):
        return self._sim_currencies

    async def fetch_currencies_internal(self):
        return self._sim_currencies, time.time()

    async def fetch_ticker(self, symbol: str) -> dict:
        ticker = self.price_engine.get_ticker(symbol)
        self.event.emit("ticker:update", self, symbol, ticker)
        return ticker

    async def watch_ticker(self, symbol: str) -> dict:
        await asyncio.sleep(0.01)  # 模拟 websocket 延迟
        self.price_engine.step(symbol)
        return await self.fetch_ticker(symbol)

    async def un_watch_ticker(self, symbol: str):
        pass

    async def fetch_order_book(self, symbol: str, limit: Optional[int] = None) -> dict:
        order_book = self.price_engine.get_order_book(symbol, limit or 20)
        self.event.emit("order_book:update", self, symbol, order_book)
        return order_book

    async def watch_order_book(self, symbol: str, limit: Optional[int] = None) -> dict:
        await asyncio.sleep(0.01)
        return await self.fetch_order_book(symbol, limit)

    async def un_watch_order_book(self, symbol: str):
        pass

    async def fetch_trades(self, symbol: str, since=None, limit=None) -> list:
        trades = self.price_engine.get_trades(symbol, count=limit or 3)
        self.event.emit("trades:update", self, symbol, trades)
        return trades

    async def watch_trades(self, symbol: str) -> list:
        await asyncio.sleep(0.01)
        return await self.fetch_trades(symbol)

    async def un_watch_trades(self, symbol: str):
        pass

    async def fetch_ohlcv(self, symbol: str, timeframe='1m', since=None, limit=None) -> list:
        # 生成简单的 OHLCV 数据
        state = self.price_engine.get_state(symbol)
        mid = state.mid_price
        now = time.time()
        rng = self.price_engine._rng
        bars = []
        for i in range(limit or 10):
            ts = int((now - (limit or 10 - i) * 60) * 1000)
            noise = mid * 0.001
            o = mid + rng.gauss(0, noise)
            c = mid + rng.gauss(0, noise)
            h = max(o, c) + abs(rng.gauss(0, noise))
            l = min(o, c) - abs(rng.gauss(0, noise))
            v = 100 + rng.random() * 200
            bars.append([ts, o, h, l, c, v])
        return bars

    async def watch_ohlcv(self, symbol: str, timeframe='1m') -> list:
        await asyncio.sleep(1.0)
        return await self.fetch_ohlcv(symbol, timeframe, limit=1)

    async def un_watch_ohlcv(self, symbol: str, timeframe='1m'):
        pass

    # ===== 资金费率 =====

    async def medal_fetch_funding_rates_internal(self) -> dict[str, FundingRate]:
        return self.funding_engine.get_all_rates()

    async def medal_fetch_funding_rates_history(self) -> list[FundingRateBill]:
        history = self.funding_engine.get_settlement_history()
        return [
            FundingRateBill(
                id=h['id'],
                symbol=h['symbol'],
                funding_time=h['funding_time'],
                funding_amount=h['funding_amount'],
            )
            for h in history
        ]

    # ===== 交易量 =====

    async def medal_fetch_ticker_volumes_internal(self) -> dict[str, float]:
        """返回模拟的 24h 交易量"""
        volumes = {}
        for base, config in SYMBOLS_CONFIG.items():
            # 交易量与价格正相关
            base_vol = config['price'] * 10000  # 大约 $10k * price
            volumes[f"{base}/USDT"] = base_vol
            volumes[f"{base}/USDT:USDT"] = base_vol * 2  # 合约通常交易量更大
        return volumes

    # ===== 余额 =====

    async def medal_fetch_balance_usd(self, ccxt_instance_key: str) -> float:
        return self.balance_tracker.get_usdt_balance()

    async def medal_fetch_total_balance_usd(self) -> float:
        return self.balance_tracker.get_usdt_balance()

    # ===== 持仓 =====

    async def medal_fetch_positions_internal(self):
        positions = self.position_tracker.get_all()
        return positions, time.time()

    async def medal_watch_positions(self) -> dict[str, float]:
        await asyncio.sleep(1.0)
        positions = self.position_tracker.get_all()
        await self.medal_cache_positions(positions)
        return positions

    # ===== 工具方法 =====

    def set_price(self, symbol: str, price: float):
        """手动注入价格（用于测试）"""
        self.price_engine.set_price(symbol, price)

    def clear_price_override(self, symbol: str):
        """清除价格覆盖"""
        self.price_engine.clear_price_override(symbol)

    def advance(self, n_steps: int = 1):
        """快进模拟 n 步（用于测试，同步调用内部引擎，不涉及 asyncio）"""
        for _ in range(n_steps):
            self.price_engine.step_all()
            price_states = {s: self.price_engine.get_state(s) for s in self.price_engine.symbols}
            self.funding_engine.update_prices(price_states)
            self.funding_engine.check_settlements(self.position_tracker, self.balance_tracker)
            self.order_manager.try_fill_orders(price_states)

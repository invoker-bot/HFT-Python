"""
PriceEngine - 价格模拟引擎

基于 GBM (Geometric Brownian Motion) 随机游走生成价格序列，
支持手动注入价格覆盖。
"""
import math
import random
import time
from dataclasses import dataclass, field
from typing import Optional

from ..markets import SYMBOLS_CONFIG, get_spread_bps


@dataclass
class SymbolPriceState:
    """单个交易对的价格状态"""
    mid_price: float
    volatility: float           # 年化波动率
    drift: float = 0.0          # 年化漂移
    spread_bps: float = 5.0     # half-spread (basis points)
    last_step_time: float = 0.0
    override_price: Optional[float] = None  # 手动注入的价格（sticky）
    # 现货/合约基差
    basis: float = 0.0          # swap_price = spot_price * (1 + basis)
    basis_drift: float = 0.0    # 基差漂移速度


class PriceEngine:
    """
    价格模拟引擎

    特性：
    - GBM 随机游走
    - set_price() 手动注入（sticky 直到 clear）
    - 现货/合约联动（共享 base price，合约加 basis）
    - 生成 ticker/orderbook/trades 数据
    """

    def __init__(self, volatility: float = 0.001, seed: Optional[int] = None):
        """
        Args:
            volatility: 每 tick 的基础波动率（非年化）
            seed: 随机种子（可选，用于可重现测试）
        """
        self._states: dict[str, SymbolPriceState] = {}
        self._base_volatility = volatility
        self._rng = random.Random(seed)
        self._tick_count = 0
        self._initialize()

    def _initialize(self):
        """从市场配置初始化所有交易对价格"""
        now = time.time()
        for base, config in SYMBOLS_CONFIG.items():
            price = float(config['price'])
            vol = config['vol']
            spread = get_spread_bps(base)

            # 初始 basis：微小随机偏移
            basis = self._rng.gauss(0, 0.0001)

            # 现货
            spot_symbol = f"{base}/USDT"
            self._states[spot_symbol] = SymbolPriceState(
                mid_price=price,
                volatility=vol,
                spread_bps=spread,
                last_step_time=now,
            )
            # 合约
            swap_symbol = f"{base}/USDT:USDT"
            self._states[swap_symbol] = SymbolPriceState(
                mid_price=price * (1 + basis),
                volatility=vol,
                spread_bps=spread * 0.8,  # 合约 spread 通常更小
                last_step_time=now,
                basis=basis,
            )

    def step(self, symbol: str) -> SymbolPriceState:
        """对单个交易对执行一步 GBM"""
        state = self._states.get(symbol)
        if state is None:
            raise KeyError(f"Unknown symbol: {symbol}")
        if state.override_price is not None:
            state.mid_price = state.override_price
            state.last_step_time = time.time()
            return state

        # GBM: dS/S = μdt + σ√dt * Z
        dt = self._base_volatility  # 使用 tick 级别波动率
        z = self._rng.gauss(0, 1)
        state.mid_price *= math.exp(
            (state.drift - 0.5 * state.volatility ** 2) * dt
            + state.volatility * math.sqrt(dt) * z
        )
        state.last_step_time = time.time()
        return state

    def step_all(self):
        """推进所有交易对价格"""
        self._tick_count += 1
        # 先推进所有现货
        for base in SYMBOLS_CONFIG:
            spot_symbol = f"{base}/USDT"
            self.step(spot_symbol)

        # 合约跟随现货 + basis 漂移
        for base in SYMBOLS_CONFIG:
            spot_symbol = f"{base}/USDT"
            swap_symbol = f"{base}/USDT:USDT"
            spot_state = self._states[spot_symbol]
            swap_state = self._states[swap_symbol]

            if swap_state.override_price is not None:
                swap_state.mid_price = swap_state.override_price
                swap_state.last_step_time = time.time()
                continue

            # basis 均值回归 + 随机漂移
            swap_state.basis *= 0.999  # 缓慢回归到 0
            swap_state.basis += self._rng.gauss(0, 0.00005)
            swap_state.basis = max(-0.01, min(0.01, swap_state.basis))  # 限制范围

            swap_state.mid_price = spot_state.mid_price * (1 + swap_state.basis)
            swap_state.last_step_time = time.time()

    def get_price(self, symbol: str) -> float:
        """获取当前 mid price"""
        state = self._states.get(symbol)
        if state is None:
            raise KeyError(f"Unknown symbol: {symbol}")
        return state.mid_price

    def get_state(self, symbol: str) -> SymbolPriceState:
        """获取价格状态"""
        state = self._states.get(symbol)
        if state is None:
            raise KeyError(f"Unknown symbol: {symbol}")
        return state

    def set_price(self, symbol: str, price: float):
        """手动注入价格（sticky 直到 clear）"""
        state = self._states.get(symbol)
        if state is None:
            raise KeyError(f"Unknown symbol: {symbol}")
        state.override_price = price
        state.mid_price = price

    def clear_price_override(self, symbol: str):
        """清除手动注入的价格"""
        state = self._states.get(symbol)
        if state is not None:
            state.override_price = None

    def clear_all_overrides(self):
        """清除所有价格覆盖"""
        for state in self._states.values():
            state.override_price = None

    def get_ticker(self, symbol: str) -> dict:
        """生成 ccxt 格式的 ticker 数据"""
        state = self._states[symbol]
        mid = state.mid_price
        half_spread = mid * state.spread_bps / 10000
        bid = mid - half_spread
        ask = mid + half_spread
        ts = int(time.time() * 1000)

        return {
            'symbol': symbol,
            'timestamp': ts,
            'datetime': None,
            'high': mid * 1.01,
            'low': mid * 0.99,
            'bid': bid,
            'ask': ask,
            'last': max(mid + self._rng.gauss(0, half_spread * 0.3), mid * 0.001),
            'close': mid,
            'baseVolume': 1000.0 + self._rng.random() * 500,
            'quoteVolume': mid * (1000.0 + self._rng.random() * 500),
            'info': {},
        }

    def get_order_book(self, symbol: str, limit: int = 20) -> dict:
        """生成合成订单簿"""
        state = self._states[symbol]
        mid = state.mid_price
        half_spread = mid * state.spread_bps / 10000
        ts = int(time.time() * 1000)

        bids = []
        asks = []
        for i in range(limit):
            level_offset = half_spread * (1 + i * 0.5)
            amount = (100 + self._rng.random() * 200) / mid  # 大约 $100-300 每档
            bids.append([mid - level_offset, amount])
            asks.append([mid + level_offset, amount])

        return {
            'symbol': symbol,
            'timestamp': ts,
            'datetime': None,
            'bids': bids,
            'asks': asks,
            'nonce': self._tick_count,
        }

    def get_trades(self, symbol: str, count: int = 3) -> list[dict]:
        """生成合成成交记录"""
        state = self._states[symbol]
        mid = state.mid_price
        half_spread = mid * state.spread_bps / 10000
        now = time.time()
        trades = []

        for i in range(count):
            side = 'buy' if self._rng.random() > 0.5 else 'sell'
            price = mid + self._rng.gauss(0, half_spread * 0.5)
            amount = (10 + self._rng.random() * 50) / mid
            ts = now - (count - i) * 0.1  # 每 0.1s 一笔

            trades.append({
                'id': f"sim-trade-{int(ts * 1000)}-{i}",
                'symbol': symbol,
                'timestamp': int(ts * 1000),
                'datetime': None,
                'side': side,
                'price': price,
                'amount': amount,
                'cost': price * amount,
                'info': {},
            })
        return trades

    @property
    def symbols(self) -> list[str]:
        """所有交易对"""
        return list(self._states.keys())

"""
FundingEngine - 资金费率模拟引擎

模拟 8 小时周期的资金费率结算。
"""
import time
import random
import logging
from dataclasses import dataclass
from typing import Optional

from .positions import PositionTracker
from .balance import BalanceTracker

logger = logging.getLogger(__name__)


@dataclass
class FundingState:
    """单个合约的资金费率状态"""
    symbol: str
    current_rate: float              # 当前费率
    next_funding_timestamp: float    # 下次结算时间
    interval_hours: int              # 结算间隔
    base_rate: float                 # 基础费率
    mark_price: float = 0.0         # 标记价格
    index_price: float = 0.0        # 指数价格
    minimum_rate: float = -0.03     # 最小费率
    maximum_rate: float = 0.03      # 最大费率


class FundingEngine:
    """
    资金费率模拟引擎

    特性：
    - 费率均值回归
    - 8h 周期结算
    - 结算时调整余额
    - 生成 FundingRate 数据
    """

    # 结算历史最大保留条数
    MAX_SETTLEMENT_HISTORY = 500

    def __init__(
        self,
        swap_symbols: list[str],
        base_rate: float = 0.0001,
        interval_hours: int = 8,
        seed: Optional[int] = None,
    ):
        self._states: dict[str, FundingState] = {}
        self._settlement_history: list[dict] = []
        self._rng = random.Random(seed)
        self._initialize(swap_symbols, base_rate, interval_hours)

    def _initialize(self, symbols: list[str], base_rate: float, interval_hours: int):
        now = time.time()
        # 对齐到下一个结算边界
        interval_secs = interval_hours * 3600
        next_funding = (now // interval_secs + 1) * interval_secs

        for symbol in symbols:
            # 初始费率：base_rate 附近随机偏移
            rate = base_rate + self._rng.gauss(0, base_rate * 0.5)
            self._states[symbol] = FundingState(
                symbol=symbol,
                current_rate=rate,
                next_funding_timestamp=next_funding,
                interval_hours=interval_hours,
                base_rate=base_rate,
            )

    def update_prices(self, price_states: dict):
        """从价格引擎同步 mark/index 价格"""
        for symbol, state in self._states.items():
            price_state = price_states.get(symbol)
            if price_state is None:
                continue
            # mark_price 略偏离 mid（模拟费率影响）
            state.mark_price = price_state.mid_price * (1 + state.current_rate * 0.01)
            state.index_price = price_state.mid_price

    def check_settlements(
        self,
        position_tracker: PositionTracker,
        balance_tracker: BalanceTracker,
    ):
        """检查并执行资金费率结算"""
        now = time.time()
        for symbol, state in self._states.items():
            if now < state.next_funding_timestamp:
                continue

            position = position_tracker.get(symbol)
            if abs(position) > 1e-9 and state.index_price > 0:
                # funding = -position * rate * index_price
                # 多头支付正费率，空头收取正费率（使用 index_price 符合交易所惯例）
                funding_amount = -position * state.current_rate * state.index_price
                balance_tracker.apply_funding(funding_amount)

                self._settlement_history.append({
                    'id': f"sim-funding-{symbol}-{int(now)}",
                    'symbol': symbol,
                    'funding_time': now,
                    'funding_amount': funding_amount,
                    'rate': state.current_rate,
                    'position': position,
                    'mark_price': state.mark_price,
                })

                logger.debug(
                    "Funding settled: %s rate=%.6f pos=%.4f amount=%.4f",
                    symbol, state.current_rate, position, funding_amount,
                )

            # 推进到下一次结算
            state.next_funding_timestamp += state.interval_hours * 3600

            # 费率均值回归 + 随机漂移
            old_rate = state.current_rate
            state.current_rate = (
                state.base_rate
                + 0.7 * (state.current_rate - state.base_rate)
                + self._rng.gauss(0, state.base_rate * 0.3)
            )
            # 限制在合理范围内
            state.current_rate = max(state.minimum_rate, min(state.maximum_rate, state.current_rate))

        # 修剪结算历史
        if len(self._settlement_history) > self.MAX_SETTLEMENT_HISTORY:
            self._settlement_history = self._settlement_history[
                len(self._settlement_history) - self.MAX_SETTLEMENT_HISTORY // 2:
            ]

    def get_funding_rate(self, symbol: str) -> Optional[dict]:
        """获取指定交易对的 FundingRate 数据（兼容 base.FundingRate 格式）"""
        state = self._states.get(symbol)
        if state is None:
            return None

        base = symbol.split('/')[0]
        return {
            'exchange': 'simulated',
            'symbol': symbol,
            'timestamp': time.time(),
            'expiry': None,
            'base_funding_rate': state.base_rate,
            'next_funding_rate': state.current_rate,
            'next_funding_timestamp': state.next_funding_timestamp,
            'funding_interval_hours': state.interval_hours,
            'mark_price': state.mark_price,
            'mark_price_timestamp': time.time(),
            'index_price': state.index_price,
            'index_price_timestamp': time.time(),
            'minimum_funding_rate': state.minimum_rate,
            'maximum_funding_rate': state.maximum_rate,
        }

    def get_all_rates(self) -> dict:
        """获取所有费率数据（用于 medal_fetch_funding_rates_internal）"""
        from ...base import FundingRate
        result = {}
        now = time.time()
        for symbol, state in self._states.items():
            result[symbol] = FundingRate(
                exchange='simulated',
                symbol=symbol,
                timestamp=now,
                expiry=None,
                base_funding_rate=state.base_rate,
                next_funding_rate=state.current_rate,
                next_funding_timestamp=state.next_funding_timestamp,
                funding_interval_hours=state.interval_hours,
                mark_price=state.mark_price or 1.0,
                mark_price_timestamp=now,
                index_price=state.index_price or 1.0,
                index_price_timestamp=now,
                minimum_funding_rate=state.minimum_rate,
                maximum_funding_rate=state.maximum_rate,
            )
        return result

    def get_settlement_history(self, symbol: Optional[str] = None) -> list[dict]:
        """获取结算历史"""
        if symbol is None:
            return list(self._settlement_history)
        return [h for h in self._settlement_history if h['symbol'] == symbol]

    def reset(self):
        """重置"""
        self._settlement_history.clear()
        self._states.clear()

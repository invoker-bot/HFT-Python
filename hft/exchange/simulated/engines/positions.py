"""
PositionTracker - 仓位追踪器

维护 {symbol: amount} 映射，正数表示多头，负数表示空头。
支持加权平均入场价追踪。
"""
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional


@dataclass
class TradeResult:
    """交易结果，包含已实现盈亏信息"""
    realized_pnl: float = 0.0  # 已实现 PnL（仅 swap 减仓/平仓时有值）


class PositionTracker:
    """仓位追踪器"""

    def __init__(self):
        self._positions: dict[str, float] = defaultdict(float)
        self._entry_prices: dict[str, float] = defaultdict(float)

    def update(self, symbol: str, delta: float, fill_price: float = 0.0) -> TradeResult:
        """更新仓位，返回交易结果（含已实现 PnL）

        入场价逻辑：
        - 开仓/加仓：加权平均更新入场价
        - 减仓：入场价不变，计算已实现 PnL
        - 平仓/反转：清零后按新方向重算入场价
        """
        old_pos = self._positions[symbol]
        new_pos = old_pos + delta
        result = TradeResult()

        if abs(new_pos) < 1e-12:
            # 完全平仓
            if abs(old_pos) > 1e-12 and fill_price > 0 and self._entry_prices[symbol] > 0:
                result.realized_pnl = (fill_price - self._entry_prices[symbol]) * old_pos
            new_pos = 0.0
            self._entry_prices[symbol] = 0.0
        elif old_pos == 0.0 or abs(old_pos) < 1e-12:
            # 新建仓位
            self._entry_prices[symbol] = fill_price
        elif (old_pos > 0 and delta > 0) or (old_pos < 0 and delta < 0):
            # 加仓：加权平均入场价
            if fill_price > 0:
                old_entry = self._entry_prices[symbol]
                self._entry_prices[symbol] = (
                    (old_entry * abs(old_pos) + fill_price * abs(delta)) / abs(new_pos)
                )
        elif (old_pos > 0 and delta < 0) or (old_pos < 0 and delta > 0):
            # 减仓或反转
            closed_amount = min(abs(delta), abs(old_pos))
            if fill_price > 0 and self._entry_prices[symbol] > 0:
                # 计算已实现 PnL：(exit_price - entry_price) * closed_position
                # old_pos > 0 做多平仓：PnL = (fill_price - entry) * closed_amount
                # old_pos < 0 做空平仓：PnL = (entry - fill_price) * closed_amount = (fill_price - entry) * (-closed_amount) ... 用 sign
                sign = 1.0 if old_pos > 0 else -1.0
                result.realized_pnl = (fill_price - self._entry_prices[symbol]) * closed_amount * sign

            if abs(new_pos) < 1e-12:
                # 完全平仓
                new_pos = 0.0
                self._entry_prices[symbol] = 0.0
            elif (new_pos > 0) != (old_pos > 0):
                # 方向反转：用 fill_price 作为新入场价
                self._entry_prices[symbol] = fill_price
            # else: 减仓但未反转，入场价不变

        self._positions[symbol] = new_pos
        return result

    def get(self, symbol: str) -> float:
        """获取指定交易对仓位"""
        return self._positions.get(symbol, 0.0)

    def get_entry_price(self, symbol: str) -> float:
        """获取指定交易对入场价"""
        return self._entry_prices.get(symbol, 0.0)

    def get_all(self) -> dict[str, float]:
        """获取所有仓位"""
        return dict(self._positions)

    def to_ccxt_positions(self, contract_sizes: dict[str, float] | None = None) -> list[dict]:
        """转换为 ccxt Position 格式"""
        result = []
        for symbol, amount in self._positions.items():
            if abs(amount) < 1e-12:
                continue
            cs = 1.0
            if contract_sizes:
                cs = contract_sizes.get(symbol, 1.0)
            contracts = abs(amount) / cs if cs else abs(amount)
            result.append({
                'symbol': symbol,
                'side': 'long' if amount > 0 else 'short',
                'contracts': contracts,
                'contractSize': cs,
                'entryPrice': self._entry_prices.get(symbol, 0.0),
                'unrealizedPnl': 0.0,
                'leverage': 10,
                'markPrice': 0.0,
                'liquidationPrice': 0.0,
                'info': {},
            })
        return result

    def reset(self):
        """重置所有仓位"""
        self._positions.clear()
        self._entry_prices.clear()

"""
PositionTracker - 仓位追踪器

维护 {symbol: amount} 映射，正数表示多头，负数表示空头。
"""
from collections import defaultdict


class PositionTracker:
    """仓位追踪器"""

    def __init__(self):
        self._positions: dict[str, float] = defaultdict(float)

    def update(self, symbol: str, delta: float):
        """更新仓位"""
        self._positions[symbol] += delta
        if abs(self._positions[symbol]) < 1e-12:
            self._positions[symbol] = 0.0

    def get(self, symbol: str) -> float:
        """获取指定交易对仓位"""
        return self._positions.get(symbol, 0.0)

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
                'entryPrice': 0.0,
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

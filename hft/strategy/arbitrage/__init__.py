"""
套利策略模块

支持三种套利模式：
- SWAP_SWAP: 跨交易所合约套利（资金费率差）
- SPOT_SWAP: 现货-合约套利（同/跨交易所）
- SPOT_SPOT: 现货搬运套利（跨交易所价差）
"""
from .types import ArbitrageType, TradingPair, ArbitragePair, PairState
from .config import ArbitrageConfig
from .strategy import ArbitrageStrategy

__all__ = [
    "ArbitrageType",
    "TradingPair",
    "ArbitragePair",
    "PairState",
    "ArbitrageConfig",
    "ArbitrageStrategy",
]

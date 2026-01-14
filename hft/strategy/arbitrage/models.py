"""
套利策略数据模型

定义套利配对、评分等核心数据结构。
"""
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional


class ArbitrageMode(Enum):
    """套利模式"""
    CROSS_EXCHANGE_SWAP = "cross_exchange_swap"  # 跨交易所合约套利
    SPOT_SWAP = "spot_swap"                       # 现货-合约套利
    SPOT_TRANSFER = "spot_transfer"               # 现货搬运套利


@dataclass
class ExchangeSymbolInfo:
    """交易所交易对信息"""
    exchange_path: str           # 交易所配置路径 (okx/main)
    exchange_class: str          # 交易所类名 (okx)
    symbol: str                  # 交易对 (BTC/USDT:USDT 或 BTC/USDT)
    base: str                    # 基础货币 (BTC)
    quote: str                   # 计价货币 (USDT)
    is_spot: bool                # 是否现货
    price: float = 0.0           # 当前价格
    funding_rate: float = 0.0    # 资金费率（现货为0）
    funding_rate_annual: float = 0.0  # 年化资金费率


@dataclass
class ArbitragePair:
    """
    套利配对

    表示一个套利机会，包含两条腿（leg1 和 leg2）。
    通常 leg1 是做多方/买入方，leg2 是做空方/卖出方。
    """
    mode: ArbitrageMode
    base: str                    # 基础货币 (BTC, ETH)
    quote: str                   # 计价货币 (USDT)

    # Leg 1 (通常是做多方/买入方)
    leg1_exchange: str           # 交易所路径 (okx/main)
    leg1_symbol: str             # 交易对 (BTC/USDT:USDT 或 BTC/USDT)
    leg1_is_spot: bool           # 是否现货
    leg1_price: float            # 当前价格
    leg1_funding_rate: float     # 资金费率（现货为0）

    # Leg 2 (通常是做空方/卖出方)
    leg2_exchange: str
    leg2_symbol: str
    leg2_is_spot: bool
    leg2_price: float
    leg2_funding_rate: float

    # 评分相关
    funding_diff: float = 0.0          # 资金费率差（年化）
    price_spread: float = 0.0          # 价差比例
    transfer_fee: float = 0.0          # 转账费用比例（仅现货搬运）
    transfer_available: bool = True    # 是否可转账

    @property
    def estimated_profit_annual(self) -> float:
        """
        估计年化利润率

        不同模式的利润来源：
        - CROSS_EXCHANGE_SWAP: 资金费率差
        - SPOT_SWAP: 资金费率 + 基差收敛
        - SPOT_TRANSFER: 价差 - 转账费
        """
        if self.mode == ArbitrageMode.CROSS_EXCHANGE_SWAP:
            return self.funding_diff
        elif self.mode == ArbitrageMode.SPOT_SWAP:
            # 假设基差在一定周期内收敛
            return self.funding_diff + self.price_spread * 12  # 假设月度收敛
        else:  # SPOT_TRANSFER
            return self.price_spread - self.transfer_fee

    @property
    def avg_price(self) -> float:
        """平均价格"""
        return (self.leg1_price + self.leg2_price) / 2

    def __repr__(self) -> str:
        return (
            f"ArbitragePair({self.mode.value}, {self.base}, "
            f"leg1={self.leg1_exchange}:{self.leg1_symbol}, "
            f"leg2={self.leg2_exchange}:{self.leg2_symbol}, "
            f"funding_diff={self.funding_diff:.4f}, spread={self.price_spread:.4f})"
        )


@dataclass
class ScoredPair:
    """
    带评分的配对

    包含套利配对和计算出的评分、方向。
    """
    pair: ArbitragePair
    score: float
    direction: int   # 1: long leg1/short leg2, -1: opposite

    @property
    def mode(self) -> ArbitrageMode:
        return self.pair.mode

    @property
    def base(self) -> str:
        return self.pair.base

    def __repr__(self) -> str:
        dir_str = "L1/S2" if self.direction == 1 else "S1/L2"
        return f"ScoredPair({self.pair.base}, score={self.score:.4f}, dir={dir_str})"


@dataclass
class ArbitragePosition:
    """
    套利仓位

    记录当前持有的套利配对仓位。
    """
    pair: ArbitragePair
    direction: int               # 1: long leg1/short leg2, -1: opposite
    position_usd: float          # 仓位大小（USD）
    entry_time: float            # 开仓时间
    entry_score: float           # 开仓时的评分
    leg1_entry_price: float      # leg1 开仓价格
    leg2_entry_price: float      # leg2 开仓价格

    @property
    def holding_time(self) -> float:
        """持仓时间（秒）"""
        import time
        return time.time() - self.entry_time

"""
套利策略数据结构

定义套利相关的核心数据类型：
- TradingPair: 单个交易对（交易所 + symbol）
- ArbitragePair: 套利对（两个交易对的组合）
- PairState: 套利对的持仓状态
"""
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional
import time


class ArbitrageType(Enum):
    """套利类型"""
    SWAP_SWAP = "swap_swap"           # 跨交易所合约套利（资金费率差）
    SPOT_SWAP = "spot_swap"           # 现货-合约套利（同/跨交易所）
    SPOT_SPOT = "spot_spot"           # 现货搬运套利（跨交易所价差）


@dataclass
class TradingPair:
    """
    交易对 - 单个交易所的单个交易对

    Example:
        TradingPair(
            exchange_path="binance/main",
            symbol="ETH/USDT",
            trade_type="spot",
            base="ETH",
            quote="USDT"
        )
    """
    exchange_path: str      # 交易所配置路径，如 "okx/main"
    symbol: str             # 交易对，如 "BTC/USDT:USDT"
    trade_type: str         # "spot" 或 "swap"
    base: str               # 基础币种，如 "BTC"
    quote: str              # 计价币种，如 "USDT"

    # 市场数据（运行时填充）
    price: float = 0.0                      # 当前价格
    funding_rate: Optional[float] = None    # 资金费率（仅 swap）
    funding_interval: int = 8               # 资金费率间隔（小时）
    next_funding_time: float = 0.0          # 下次结算时间戳

    @property
    def key(self) -> tuple[str, str]:
        """返回用于 TargetPositions 的 key"""
        return (self.exchange_path, self.symbol)

    @property
    def id(self) -> str:
        """唯一标识符"""
        return f"{self.exchange_path}:{self.symbol}"

    def __hash__(self):
        return hash((self.exchange_path, self.symbol))

    def __eq__(self, other):
        if isinstance(other, TradingPair):
            return self.exchange_path == other.exchange_path and self.symbol == other.symbol
        return False


@dataclass
class ArbitragePair:
    """
    套利对 - 两个交易对的组合

    套利对由两条腿组成，分别对应做多和做空的交易对。
    评分基于资金费率差、价差等因素计算。

    Example:
        ArbitragePair(
            leg1=TradingPair("binance/main", "ETH/USDT", "spot", "ETH", "USDT"),
            leg2=TradingPair("okx/main", "ETH/USDT:USDT", "swap", "ETH", "USDT"),
            arb_type=ArbitrageType.SPOT_SWAP
        )
    """
    leg1: TradingPair       # 第一条腿
    leg2: TradingPair       # 第二条腿
    arb_type: ArbitrageType = field(default=ArbitrageType.SWAP_SWAP)

    # 评分（运行时计算）
    score: float = 0.0
    estimated_annual_profit: float = 0.0    # 预估年化收益率

    # 方向：1 表示 leg1 做多/leg2 做空，-1 表示相反
    direction: int = 1

    # 现货搬运特有
    transfer_fee: float = 0.0               # 转账费用
    transfer_possible: bool = True          # 是否可转账

    @property
    def base(self) -> str:
        """基础币种"""
        return self.leg1.base

    @property
    def quote(self) -> str:
        """计价币种"""
        return self.leg1.quote

    @property
    def id(self) -> str:
        """
        唯一标识符

        格式: {base}:{leg1_exchange}-{leg2_exchange}:{arb_type}
        例如: BTC:binance/main-okx/main:swap_swap
        """
        return f"{self.base}:{self.leg1.exchange_path}-{self.leg2.exchange_path}:{self.arb_type.value}"

    @property
    def funding_diff(self) -> float:
        """资金费率差（leg1 - leg2）"""
        f1 = self.leg1.funding_rate or 0.0
        f2 = self.leg2.funding_rate or 0.0
        return f1 - f2

    @property
    def funding_diff_annual(self) -> float:
        """资金费率差年化"""
        # 使用较小的 funding_interval
        interval = min(
            self.leg1.funding_interval or 8,
            self.leg2.funding_interval or 8
        )
        return abs(self.funding_diff) * (365 * 24 / interval)

    @property
    def price_diff(self) -> float:
        """价差（leg1 - leg2）"""
        return self.leg1.price - self.leg2.price

    @property
    def price_diff_ratio(self) -> float:
        """价差比例"""
        avg_price = (self.leg1.price + self.leg2.price) / 2
        if avg_price <= 0:
            return 0.0
        return self.price_diff / avg_price

    @property
    def long_leg(self) -> TradingPair:
        """做多的腿"""
        return self.leg1 if self.direction == 1 else self.leg2

    @property
    def short_leg(self) -> TradingPair:
        """做空的腿"""
        return self.leg2 if self.direction == 1 else self.leg1

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        if isinstance(other, ArbitragePair):
            return self.id == other.id
        return False


@dataclass
class PairState:
    """
    套利对的持仓状态

    追踪已入场的套利对的状态，用于实现滞后控制。
    """
    pair: ArbitragePair
    entry_score: float              # 入场时的评分
    entry_time: float = field(default_factory=time.time)  # 入场时间戳
    entry_prices: tuple[float, float] = (0.0, 0.0)        # 入场价格 (leg1, leg2)

    @property
    def hold_hours(self) -> float:
        """持有时间（小时）"""
        return (time.time() - self.entry_time) / 3600

    @property
    def hold_seconds(self) -> float:
        """持有时间（秒）"""
        return time.time() - self.entry_time

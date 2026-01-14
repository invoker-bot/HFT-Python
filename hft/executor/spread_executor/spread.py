"""
Spread 计算模块

用于限价单的价差计算：
- FixedSpread: 固定点差
- StdSpread: 基于标准差的点差
- ASSpread: Avellaneda-Stoikov 模型点差
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
import math


@dataclass
class SpreadResult:
    """点差计算结果"""
    bid_spread: float      # 买单点差（相对于中间价的百分比，正数表示低于中间价）
    ask_spread: float      # 卖单点差（相对于中间价的百分比，正数表示高于中间价）
    mid_price: float       # 中间价
    confidence: float = 1.0  # 置信度 0-1

    @property
    def bid_price(self) -> float:
        """买单价格"""
        return self.mid_price * (1 - self.bid_spread)

    @property
    def ask_price(self) -> float:
        """卖单价格"""
        return self.mid_price * (1 + self.ask_spread)

    @property
    def total_spread(self) -> float:
        """总点差"""
        return self.bid_spread + self.ask_spread


class BaseSpread(ABC):
    """点差计算基类"""

    @abstractmethod
    def calculate(
        self,
        mid_price: float,
        side: str,  # 'buy' or 'sell'
        volatility: Optional[float] = None,
        inventory: Optional[float] = None,
        **kwargs
    ) -> SpreadResult:
        """
        计算点差

        Args:
            mid_price: 中间价
            side: 订单方向
            volatility: 波动率（可选）
            inventory: 库存/仓位（可选）

        Returns:
            SpreadResult
        """
        ...

    def get_order_price(
        self,
        mid_price: float,
        side: str,
        volatility: Optional[float] = None,
        inventory: Optional[float] = None,
        **kwargs
    ) -> float:
        """获取订单价格"""
        result = self.calculate(mid_price, side, volatility, inventory, **kwargs)
        if side == 'buy':
            return result.bid_price
        return result.ask_price


class FixedSpread(BaseSpread):
    """
    固定点差

    始终使用固定的百分比点差
    """

    def __init__(self, spread_pct: float = 0.001):
        """
        Args:
            spread_pct: 点差百分比，默认 0.1%
        """
        self.spread_pct = spread_pct

    def calculate(
        self,
        mid_price: float,
        side: str,
        volatility: Optional[float] = None,
        inventory: Optional[float] = None,
        **kwargs
    ) -> SpreadResult:
        return SpreadResult(
            bid_spread=self.spread_pct,
            ask_spread=self.spread_pct,
            mid_price=mid_price,
            confidence=1.0
        )


class StdSpread(BaseSpread):
    """
    基于标准差的点差

    点差 = base_spread + std_multiplier * volatility
    """

    def __init__(
        self,
        base_spread: float = 0.0005,
        std_multiplier: float = 1.0,
        min_spread: float = 0.0001,
        max_spread: float = 0.01,
    ):
        """
        Args:
            base_spread: 基础点差
            std_multiplier: 标准差乘数
            min_spread: 最小点差
            max_spread: 最大点差
        """
        self.base_spread = base_spread
        self.std_multiplier = std_multiplier
        self.min_spread = min_spread
        self.max_spread = max_spread

    def calculate(
        self,
        mid_price: float,
        side: str,
        volatility: Optional[float] = None,
        inventory: Optional[float] = None,
        **kwargs
    ) -> SpreadResult:
        # 计算点差
        vol = volatility or 0.0
        spread = self.base_spread + self.std_multiplier * vol

        # 限制范围
        spread = max(self.min_spread, min(self.max_spread, spread))

        # 置信度基于波动率
        confidence = 1.0 if vol > 0 else 0.5

        return SpreadResult(
            bid_spread=spread,
            ask_spread=spread,
            mid_price=mid_price,
            confidence=confidence
        )


class ASSpread(BaseSpread):
    """
    Avellaneda-Stoikov 模型点差

    经典做市商模型，考虑：
    - 波动率
    - 库存风险
    - 风险厌恶系数

    公式：
    reservation_price = mid_price - inventory * gamma * volatility^2 * T
    optimal_spread = gamma * volatility^2 * T + 2/gamma * ln(1 + gamma/k)

    简化版本：
    bid_spread = base_spread + gamma * volatility - inventory_adjustment
    ask_spread = base_spread + gamma * volatility + inventory_adjustment
    """

    def __init__(
        self,
        gamma: float = 0.1,           # 风险厌恶系数
        kappa: float = 1.5,           # 订单到达强度
        base_spread: float = 0.0005,  # 基础点差
        min_spread: float = 0.0001,
        max_spread: float = 0.02,
        inventory_impact: float = 0.5,  # 库存影响系数
    ):
        """
        Args:
            gamma: 风险厌恶系数，越大点差越大
            kappa: 订单到达强度参数
            base_spread: 基础点差
            min_spread: 最小点差
            max_spread: 最大点差
            inventory_impact: 库存对点差的影响系数
        """
        self.gamma = gamma
        self.kappa = kappa
        self.base_spread = base_spread
        self.min_spread = min_spread
        self.max_spread = max_spread
        self.inventory_impact = inventory_impact

    def calculate(
        self,
        mid_price: float,
        side: str,
        volatility: Optional[float] = None,
        inventory: Optional[float] = None,
        **kwargs
    ) -> SpreadResult:
        vol = volatility or 0.01  # 默认 1% 波动率
        inv = inventory or 0.0

        # 计算基础点差（来自 AS 模型）
        # optimal_spread ≈ gamma * sigma^2 + 2/gamma * ln(1 + gamma/kappa)
        vol_component = self.gamma * vol * vol
        order_component = 0.0
        if self.gamma > 0:
            order_component = (2 / self.gamma) * math.log(1 + self.gamma / self.kappa)

        base = self.base_spread + vol_component + order_component

        # 库存调整
        # 如果持有多头（inventory > 0），应该更愿意卖出（降低 ask spread）
        # 如果持有空头（inventory < 0），应该更愿意买入（降低 bid spread）
        inventory_adj = self.inventory_impact * inv * vol

        bid_spread = base + inventory_adj   # 多头时买单点差增大
        ask_spread = base - inventory_adj   # 多头时卖单点差减小

        # 限制范围
        bid_spread = max(self.min_spread, min(self.max_spread, bid_spread))
        ask_spread = max(self.min_spread, min(self.max_spread, ask_spread))

        # 置信度
        confidence = 0.8 if vol > 0 else 0.5

        return SpreadResult(
            bid_spread=bid_spread,
            ask_spread=ask_spread,
            mid_price=mid_price,
            confidence=confidence
        )


class DynamicSpread(BaseSpread):
    """
    动态点差

    根据市场深度和订单簿不平衡动态调整
    """

    def __init__(
        self,
        base_spread: float = 0.0005,
        depth_factor: float = 0.1,    # 深度影响因子
        imbalance_factor: float = 0.2,  # 不平衡影响因子
        min_spread: float = 0.0001,
        max_spread: float = 0.02,
    ):
        self.base_spread = base_spread
        self.depth_factor = depth_factor
        self.imbalance_factor = imbalance_factor
        self.min_spread = min_spread
        self.max_spread = max_spread

    def calculate(
        self,
        mid_price: float,
        side: str,
        volatility: Optional[float] = None,
        inventory: Optional[float] = None,
        depth: Optional[float] = None,
        imbalance: Optional[float] = None,
        **kwargs
    ) -> SpreadResult:
        """
        Args:
            depth: 订单簿深度（USD 价值）
            imbalance: 订单簿不平衡 (-1 到 1，正数表示买盘强)
        """
        vol = volatility or 0.01
        dep = depth or 100000
        imb = imbalance or 0.0

        # 基础点差
        spread = self.base_spread + vol

        # 深度调整：深度越大，点差越小
        depth_adj = -self.depth_factor * math.log(dep / 10000) / 10
        spread += depth_adj

        # 不平衡调整
        bid_adj = -self.imbalance_factor * imb
        ask_adj = self.imbalance_factor * imb

        bid_spread = max(self.min_spread, min(self.max_spread, spread + bid_adj))
        ask_spread = max(self.min_spread, min(self.max_spread, spread + ask_adj))

        return SpreadResult(
            bid_spread=bid_spread,
            ask_spread=ask_spread,
            mid_price=mid_price,
            confidence=0.7
        )


__all__ = [
    "SpreadResult",
    "BaseSpread",
    "FixedSpread",
    "StdSpread",
    "ASSpread",
    "DynamicSpread",
]

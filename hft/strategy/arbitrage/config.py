"""
套利策略配置

配置示例 (conf/strategy/arbitrage/main.yaml):
    class_name: arbitrage
    name: Cross Exchange Arbitrage
    interval: 10.0

    exchanges:
      - okx/main
      - binance/main

    base_currencies:
      - BTC
      - ETH
      - SOL

    quote_currency: USDT

    enable_swap_swap: true
    enable_spot_swap: true
    enable_spot_spot: false

    per_pair_usd: 2000.0
    max_pairs: 5

    entry_threshold: 0.10
    exit_threshold: 0.05
"""
from typing import ClassVar, Type, TYPE_CHECKING
from functools import cached_property
from pydantic import Field
from ..config import BaseStrategyConfig

if TYPE_CHECKING:
    from .strategy import ArbitrageStrategy


class ArbitrageConfig(BaseStrategyConfig):
    """
    套利策略配置

    Attributes:
        exchanges: 交易所配置路径列表
        base_currencies: 允许的基础币种，空为全部
        quote_currency: 计价币种
        enable_*: 各套利模式开关
        entry_threshold: 入场阈值（年化收益率）
        exit_threshold: 退出阈值（年化收益率）
        per_pair_usd: 每个套利对的仓位（USD）
        max_pairs: 最大同时持有的套利对数量
    """
    class_name: ClassVar[str] = "arbitrage"

    # === 交易所配置 ===
    exchanges: list[str] = Field(
        ...,
        description="交易所配置路径列表，如 ['okx/main', 'binance/main']"
    )

    # === 交易对过滤 ===
    base_currencies: list[str] = Field(
        default_factory=list,
        description="允许的基础币种，空为全部。如 ['BTC', 'ETH', 'SOL']"
    )
    quote_currency: str = Field(
        "USDT",
        description="计价币种"
    )

    # === 套利模式开关 ===
    enable_swap_swap: bool = Field(
        True,
        description="启用跨交易所合约套利（资金费率差）"
    )
    enable_spot_swap: bool = Field(
        True,
        description="启用现货-合约套利（同/跨交易所）"
    )
    enable_spot_spot: bool = Field(
        False,
        description="启用现货搬运套利（跨交易所价差）"
    )

    # === 阈值配置（滞后控制）===
    entry_threshold: float = Field(
        0.10,
        description="入场阈值，预估年化收益率 > 此值时入场 (0.10 = 10%)"
    )
    exit_threshold: float = Field(
        0.05,
        description="退出阈值，预估年化收益率 < 此值时退出 (0.05 = 5%)"
    )

    # === 仓位管理 ===
    per_pair_usd: float = Field(
        1000.0,
        description="每个套利对的仓位（USD）"
    )
    max_pairs: int = Field(
        10,
        description="最大同时持有的套利对数量"
    )

    # === 执行参数 ===
    speed: float = Field(
        0.5,
        description="执行紧急度 [0.0, 1.0]"
    )

    # === 高级配置 ===
    min_funding_interval: int = Field(
        8,
        description="最小资金费率间隔（小时），用于年化计算"
    )

    @classmethod
    def get_class_type(cls) -> Type["ArbitrageStrategy"]:
        from .strategy import ArbitrageStrategy
        return ArbitrageStrategy

    @cached_property
    def instance(self) -> "ArbitrageStrategy":
        from .strategy import ArbitrageStrategy
        return ArbitrageStrategy(config=self)

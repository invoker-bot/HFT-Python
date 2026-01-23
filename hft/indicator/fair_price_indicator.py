"""FairPriceIndicator - 公平价格指标

用于 MarketNeutralPositions 策略，返回交易对的公平价格（mid_price）。

Feature 0013: MarketNeutralPositions 策略
"""
import time
from typing import TYPE_CHECKING, Any, Dict, Optional

from .base import BaseIndicator

if TYPE_CHECKING:
    from ..core.app.core import AppCore


class FairPriceIndicator(BaseIndicator[float]):
    """
    公平价格指标

    特性：
    - 返回 mid_price 作为公平价格（trading_pair_std_price）
    - 支持返回 None（mask 机制，当价格数据不可用时）
    - 注入到 trading_pair_class scope
    - 依赖 TickerDataSource 提供的数据

    注意：
    - 标准化（最小价格 = 1.0）在 Strategy 层完成
    - 本 Indicator 只负责返回原始 mid_price
    """

    def __init__(
        self,
        exchange_class: str,
        symbol: str,
        ready_condition: Optional[str] = "timeout < 30",
        **kwargs,
    ):
        """
        Args:
            exchange_class: 交易所类名（如 "okx"）
            symbol: 交易对（如 "ETH/USDT"）
            ready_condition: 就绪条件表达式
        """
        name = f"FairPrice:{exchange_class}:{symbol}"
        super().__init__(
            name=name,
            window=0,  # 不需要历史数据（单值指标）
            ready_condition=ready_condition,
            expire_seconds=30.0,  # 30 秒过期
            interval=None,  # 事件驱动
        )
        self._exchange_class = exchange_class
        self._symbol = symbol
        self._mid_price: Optional[float] = None
        self._last_update: float = 0.0
        # Feature 0012: 注入到 trading_pair_class 层级（所有 exchange 共享）
        self.scope_level = "trading_pair_class"

    @property
    def exchange_class(self) -> str:
        """交易所类名"""
        return self._exchange_class

    @property
    def symbol(self) -> str:
        """交易对"""
        return self._symbol

    def _get_ticker_datasource(self):
        """
        获取 TickerDataSource 实例

        通过 IndicatorGroup 获取对应的 TickerDataSource。
        """
        if self.root is None:
            return None
        indicator_group = getattr(self.root, 'indicator_group', None)
        if indicator_group is None:
            return None
        # 获取 ticker indicator（约定 indicator_id 为 "ticker"）
        return indicator_group.get_indicator(
            "ticker", self._exchange_class, self._symbol
        )

    def _update_from_ticker(self) -> bool:
        """
        从 TickerDataSource 更新 mid_price

        Returns:
            True: 更新成功
            False: 更新失败或数据不可用
        """
        ticker_ds = self._get_ticker_datasource()
        if ticker_ds is None:
            return False

        if not ticker_ds.is_ready():
            return False

        try:
            # 从 TickerDataSource 获取变量
            ticker_vars = ticker_ds.calculate_vars(direction=0)
            mid = ticker_vars.get("mid")

            if mid is not None and mid > 0:
                self._mid_price = mid
                self._last_update = time.time()
                # 存储到 data 中（用于 ready 检查）
                self._data.append(self._last_update, mid)
                return True

        except Exception as e:
            self.logger.warning(
                "Failed to get mid_price from ticker for %s:%s: %s",
                self._exchange_class, self._symbol, e
            )

        return False

    def is_ready(self) -> bool:
        """
        检查是否就绪

        FairPriceIndicator 依赖 TickerDataSource，需要确保有有效的 mid_price。
        """
        # 先尝试更新数据
        self._update_from_ticker()

        # 检查是否有数据
        if self._mid_price is None:
            return False

        # 使用基类的 ready 检查
        return super().is_ready()

    def calculate_vars(self, direction: int = 0) -> Dict[str, Any]:
        """
        计算变量

        Args:
            direction: 方向（未使用）

        Returns:
            变量字典：{"trading_pair_std_price": mid_price or None}
        """
        # 尝试更新数据
        self._update_from_ticker()

        # 返回 mid_price 作为 trading_pair_std_price
        # None 表示数据不可用（mask 机制）
        return {"trading_pair_std_price": self._mid_price}

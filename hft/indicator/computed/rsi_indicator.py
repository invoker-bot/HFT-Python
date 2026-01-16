"""
RSI 相对强弱指标

Feature 0005: Executor 动态条件与变量注入机制
Feature 0006: 计算类 Indicator 支持 requires 标记
"""
from typing import Any, Optional, TYPE_CHECKING
import time

import numpy as np

from ..base import BaseIndicator

if TYPE_CHECKING:
    from ..datasource.ohlcv_datasource import OHLCVDataSource


class RSIIndicator(BaseIndicator[float]):
    """
    RSI 相对强弱指标

    从 OHLCV 计算 RSI。

    requires 行为（Feature 0005）：
    - 被 Executor requires 依赖时：on_tick() 定期计算并缓存到 _data
    - 未被依赖时：calculate_vars() lazy 按需计算
    """

    def __init__(
        self,
        exchange_class: str,
        symbol: str,
        ohlcv: str = "ohlcv",
        period: int = 14,
        ready_condition: Optional[str] = None,
        **kwargs,
    ):
        name = f"RSI:{exchange_class}:{symbol}"
        # interval=60 表示每 60 秒 tick 一次（仅在被 requires 时有效）
        super().__init__(
            name=name,
            interval=60.0,
            ready_condition=ready_condition,
            window=3600,  # 保留 1 小时的 RSI 历史
            **kwargs,
        )
        self._exchange_class = exchange_class
        self._symbol = symbol
        self._ohlcv_id = ohlcv
        self._period = period

        # 缓存（用于 lazy 计算）
        self._cached_rsi: Optional[float] = None
        self._cache_timestamp: float = 0.0
        self._cache_ttl: float = 60.0  # 缓存 60 秒

    def _get_ohlcv_indicator(self) -> Optional["OHLCVDataSource"]:
        """获取 OHLCV 数据源"""
        if self.root is None:
            return None
        indicator_group = getattr(self.root, 'indicator_group', None)
        if indicator_group is None:
            return None
        return indicator_group.query_indicator(
            self._ohlcv_id,
            self._exchange_class,
            self._symbol,
        )

    def _calculate_rsi(self, closes: list[float]) -> float:
        """计算 RSI"""
        if len(closes) < self._period + 1:
            return 50.0  # 数据不足返回中性值

        prices = np.array(closes)
        deltas = np.diff(prices)

        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gain = np.mean(gains[-self._period:])
        avg_loss = np.mean(losses[-self._period:])

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return float(rsi)

    async def on_tick(self) -> bool:
        """
        定期更新 RSI（仅在被 requires 时调用）

        如果未被 requires 依赖，此方法不会被调用（interval 被忽略）。
        """
        # 只有被 requires 依赖时才定期更新
        if not self.is_required:
            return False

        ohlcv_indicator = self._get_ohlcv_indicator()
        if ohlcv_indicator is None or not ohlcv_indicator.is_ready():
            return False

        closes = [c.close for c in ohlcv_indicator._data]
        rsi = self._calculate_rsi(closes)

        # 缓存到 _data
        now = time.time()
        self._data.append(rsi, timestamp=now)

        # 更新 lazy 缓存
        self._cached_rsi = rsi
        self._cache_timestamp = now

        return False

    def ready_internal(self) -> bool:
        """
        覆盖 ready_internal() 实现

        要求至少有 1 个 RSI 值缓存到 _data。
        """
        # 如果被 requires 依赖，检查 _data
        if self.is_required:
            return len(self._data) > 0

        # 如果未被依赖，检查依赖的 OHLCV 是否 ready
        ohlcv_indicator = self._get_ohlcv_indicator()
        if ohlcv_indicator is None:
            return False
        return ohlcv_indicator.is_ready()

    def calculate_vars(self, direction: int) -> dict[str, Any]:
        """
        返回 RSI 变量

        requires 行为：
        - 被依赖时：从 _data 读取最新值（on_tick 定期更新）
        - 未被依赖时：lazy 按需计算，缓存 60 秒
        """
        # 如果被 requires 依赖，从 _data 读取
        if self.is_required and len(self._data) > 0:
            return {"rsi": self._data.latest}

        # lazy 模式：检查缓存
        now = time.time()
        if self._cached_rsi is not None and now - self._cache_timestamp < self._cache_ttl:
            return {"rsi": self._cached_rsi}

        # 缓存失效，重新计算
        ohlcv_indicator = self._get_ohlcv_indicator()
        if ohlcv_indicator is None or not ohlcv_indicator.is_ready():
            return {"rsi": 50.0}

        closes = [c.close for c in ohlcv_indicator._data]
        rsi = self._calculate_rsi(closes)

        # 更新缓存
        self._cached_rsi = rsi
        self._cache_timestamp = now

        return {"rsi": rsi}

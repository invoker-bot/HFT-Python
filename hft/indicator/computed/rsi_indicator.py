"""
RSI 相对强弱指标

Feature 0006: Indicator 与 DataSource 统一架构

从 OHLCVDataSource 计算 RSI，使用 TA-Lib。
"""
import time
from typing import Any, Optional

import numpy as np
import talib

from ..base import BaseIndicator
from ..datasource.ohlcv_datasource import OHLCVDataSource


class RSIIndicator(BaseIndicator):
    """
    RSI 相对强弱指标

    从 OHLCVDataSource 计算 RSI。
    通过 scope 获取 OHLCVDataSource 实例。
    """
    supported_scope = None

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self._period: int = kwargs.get("period", 14)
        # 缓存
        self._cached_rsi: Optional[float] = None
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 60.0

    def _get_ohlcv_ds(self) -> Optional[OHLCVDataSource]:
        """获取 OHLCV 数据源"""
        if self.root is None:
            return None
        return self.root.query_indicator(OHLCVDataSource, self.scope)

    def _compute_rsi(self) -> Optional[float]:
        """使用 TA-Lib 计算 RSI"""
        ds = self._get_ohlcv_ds()
        if ds is None or not ds.ready:
            return None

        # data_list: list[tuple[CandleData, float]]
        data_list = ds.data.data_list
        if len(data_list) < self._period + 1:
            return 50.0  # 数据不足返回中性值

        closes = np.array(
            [candle.close for candle, _ts in data_list],
            dtype=np.float64,
        )
        rsi = talib.RSI(closes, timeperiod=self._period)

        last = rsi[-1]
        if np.isnan(last):
            return 50.0

        return float(last)

    def get_vars(self) -> dict[str, Any]:
        """返回 RSI 变量，带缓存"""
        now = time.time()
        if self._cached_rsi is not None and now - self._cache_ts < self._cache_ttl:
            return {"rsi": self._cached_rsi}

        result = self._compute_rsi()
        if result is None:
            return {"rsi": 50.0}

        self._cached_rsi = result
        self._cache_ts = now
        return {"rsi": result}

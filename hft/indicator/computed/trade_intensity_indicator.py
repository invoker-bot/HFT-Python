"""
交易强度指标 - 用于 AS 做市策略

基于历史成交数据估计订单到达率参数 kappa (k)。

原理：
- 收集一段时间内的成交数据
- 统计不同价格偏离下的成交量分布
- 拟合指数衰减模型：λ(δ) = A * exp(-k * δ)
- 取对数后线性回归：log(λ) = log(A) - k * δ
"""
import math
import time
import logging
import numpy as np
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional
import matplotlib.pyplot as plt

from ..base import BaseIndicator

if TYPE_CHECKING:
    from ..datasource.trades_datasource import TradeData, TradesDataSource


logger = logging.getLogger(__name__)


@dataclass
class IntensityResult:
    """交易强度计算结果"""
    average_price: float
    average_std: float
    buy_k: float
    buy_b: float
    buy_correlation: float
    sell_k: float
    sell_b: float
    sell_correlation: float


class TradeIntensityCalculator:
    def __init__(self, sub_range_seconds = 15, total_range_seconds = 600,
                 precision=20, precision_std_range=1.0, min_trades=50, min_correlation=0.6):
        self._min_trades = min_trades
        self._sub_range_seconds = sub_range_seconds
        self._total_range_seconds = total_range_seconds
        self._precision = precision
        self._precision_std_range = precision_std_range
        self._min_correlation = min_correlation
        self.result: Optional[IntensityResult] = None

    def total_amount(self, trades: list[tuple['TradeData', float]]) -> float:
        return sum([abs(item[0].amount) for item in trades])

    def polyfit(self, x, y):
        k, b = np.polyfit(x, y, 1)
        correlation = abs(float(np.corrcoef(x, y)[0, 1]))
        if correlation < self._min_correlation:
            logger.warning("correlation too low: %f", correlation)
            k = 0
        return k, b, correlation

    def calculate(self, trades: list[tuple['TradeData', float]]):
        if len(trades) < self._min_trades:
            return
        average_price = sum([abs(item[0].price * item[0].amount) for item in trades]) / self.total_amount(trades)
        average_std = sum([abs(item[0].price - average_price) * abs(item[0].amount) for item in trades]) / self.total_amount(trades)
        # 可近似使用当前标准差
        x = np.linspace(-self._precision_std_range * average_std, self._precision_std_range * average_std, num=2 * self._precision + 1)
        y = np.zeros_like(x)
        start_time = last_timestamp = trades[0][0].timestamp
        start_price = last_price = trades[0][0].price
        for trade in trades:
            while trade[0].timestamp - start_time > self._sub_range_seconds:
                start_time += self._sub_range_seconds
                a = abs(start_time - last_timestamp) # , 1e-6)
                b = abs(trade[0].timestamp - last_timestamp) # , 1e-6)
                start_price = (a * trade[0].price + b * last_price) / (a + b)
            price_diff = trade[0].price - start_price
            mapped_x = round(self._precision * price_diff / (self._precision_std_range * average_std))
            if price_diff > 0:
                start_idx, end_idx = self._precision + 1, min(self._precision + 1 + mapped_x, 2 * self._precision + 1)
            else:
                start_idx, end_idx = max(self._precision + mapped_x, 0), self._precision
            y[start_idx:end_idx] += abs(trade[0].amount)
            last_price = trade[0].price
            last_timestamp = trade[0].timestamp
        log_y = np.log(np.maximum(y, 1e-9))
        buy_x = x[1:self._precision - 1]
        buy_y = log_y[1:self._precision - 1]
        buy_k, buy_b, buy_correlation = self.polyfit(buy_x, buy_y)
        sell_x = x[self._precision + 2:-1]
        sell_y = log_y[self._precision + 2:-1]
        sell_k, sell_b, sell_correlation = self.polyfit(sell_x, sell_y)
        self.result = IntensityResult(
            average_price=average_price,
            average_std=average_std,
            buy_k=buy_k,
            buy_b=buy_b,
            buy_correlation=buy_correlation,
            sell_k=sell_k,
            sell_b=sell_b,
            sell_correlation=sell_correlation,
        )
        return x, log_y

    # TODO: 注入 functions
    def get_buy_spread(self, gamma, q = 0):  # q是仓位，为正是多仓
        return 0.5 * (1 - q) * gamma * self.result.average_std, self.result.average_std * (1 / gamma) * math.log1p(gamma/max(abs(self.result.buy_k) * self.result.average_std, 1e-6))

    def get_sell_spread(self, gamma, q = 0):  # q是仓位，为正是多仓
        return 0.5 * (1 + q) * gamma * self.result.average_std, self.result.average_std * (1 / gamma) * math.log1p(gamma/max(abs(self.result.sell_k) * self.result.average_std, 1e-6))

    def plot(self, x, log_y):
        if self.result is None:
            logger.warning("No result to plot")
            return
        plt.plot(x, log_y, 'o')
        log_y_pred = np.zeros_like(log_y)
        buy_x = x[0:self._precision]
        log_y_pred[0:self._precision] = self.result.buy_k * buy_x + self.result.buy_b
        sell_x = x[self._precision:]
        log_y_pred[self._precision:] = self.result.sell_k * sell_x + self.result.sell_b
        plt.plot(x, log_y_pred)
        plt.show()


class TradeIntensityIndicator(BaseIndicator):
    """
    交易强度指标 - 用于 AS 做市策略

    从 TradesDataSource 获取成交数据，通过 TradeIntensityCalculator 计算
    订单到达率参数（buy_k, sell_k 等）。
    """
    supported_scope = None

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        window = kwargs.get("window", 600.0)
        self._calculator = TradeIntensityCalculator(
            sub_range_seconds=kwargs.get("sub_range_seconds", 15.0),
            total_range_seconds=window,
            precision=kwargs.get("precision", 20),
            precision_std_range=kwargs.get("precision_std_range", 1.0),
            min_correlation=kwargs.get("min_correlation", 0.6),
            min_trades=kwargs.get("min_trades", 50),
        )
        # 缓存
        self._cached_result: Optional[IntensityResult] = None
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 10.0

    @property
    def calculator(self) -> TradeIntensityCalculator:
        """获取内部计算器（用于访问 get_buy_spread/get_sell_spread 等方法）"""
        return self._calculator

    def _get_trades_ds(self) -> Optional["TradesDataSource"]:
        """获取 Trades 数据源"""
        if self.root is None:
            return None
        from ..datasource.trades_datasource import TradesDataSource
        return self.root.query_indicator(TradesDataSource, self.scope)

    def _get_recent_trades(self) -> list[tuple["TradeData", float]]:
        """获取窗口内的 trades"""
        ds = self._get_trades_ds()
        if ds is None or not ds.ready:
            return []

        now = time.time()
        cutoff = now - self._calculator._total_range_seconds
        return [
            item for item in ds.data.data_list
            if item[1] >= cutoff
        ]

    def _compute_result(self) -> Optional[IntensityResult]:
        """调用 calculator 计算结果"""
        trades = self._get_recent_trades()
        if not trades:
            return None
        self._calculator.calculate(trades)
        return self._calculator.result

    def _empty_vars(self) -> dict[str, Any]:
        return {
            "buy_k": 0.0,
            "sell_k": 0.0,
            "buy_b": 0.0,
            "sell_b": 0.0,
            "buy_correlation": 0.0,
            "sell_correlation": 0.0,
        }

    def _result_to_vars(self, result: IntensityResult) -> dict[str, Any]:
        return {
            "buy_k": result.buy_k,
            "sell_k": result.sell_k,
            "buy_b": result.buy_b,
            "sell_b": result.sell_b,
            "buy_correlation": result.buy_correlation,
            "sell_correlation": result.sell_correlation,
        }

    def get_vars(self) -> dict[str, Any]:
        """返回交易强度变量，带缓存"""
        now = time.time()
        if self._cached_result is not None and now - self._cache_ts < self._cache_ttl:
            return self._result_to_vars(self._cached_result)

        result = self._compute_result()
        if result is None:
            return self._empty_vars()

        self._cached_result = result
        self._cache_ts = now
        return self._result_to_vars(result)

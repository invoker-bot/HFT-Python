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
from functools import cached_property
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional
import numpy as np
import matplotlib.pyplot as plt
from ...core.cache_decorator import instance_cache_sync
from ..base import BaseTradingPairClassDataIndicator

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


class TradeIntensityIndicator(BaseTradingPairClassDataIndicator):
    __pickle_exclude__ = {*BaseTradingPairClassDataIndicator.__pickle_exclude__, "_calculator", "_cached_result", "_cache_ts", "_cache_ttl"}
    DEFAULT_IS_ARRAY = None

    @cached_property
    def trades_indicator(self) -> Optional["TradesDataSource"]:
        app_core = self.root
        return app_core.query_indicator(self.trade_indicator_id, self.scope)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.result: Optional[IntensityResult] = None

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self._min_trades = kwargs.get("min_trades", 50)
        self._sub_range_seconds = kwargs.get("sub_range_seconds", 15)
        self._total_range_seconds = kwargs.get("total_range_seconds", 600)
        self._precision = kwargs.get("precision", 20)
        self._precision_std_range = kwargs.get("precision_std_range", 1.0)
        self._min_correlation = kwargs.get("min_correlation", 0.6)
        self.trade_indicator_id = kwargs["trade_indicator_id"]

    def total_amount(self, trades: list[tuple['TradeData', float]]) -> float:
        return sum([abs(item[0].amount) for item in trades])

    def polyfit(self, x, y):
        k, b = np.polyfit(x, y, 1)
        correlation = abs(float(np.corrcoef(x, y)[0, 1]))
        if correlation < self._min_correlation:
            logger.warning("correlation too low: %f", correlation)
            k = 0
        return k, b, correlation

    @instance_cache_sync(ttl=60)
    def calculate(self):
        # if self.result is not None:
        #     print("using result:", self.result)
        if not self.trades_indicator.ready:
            return
        trades = self.trades_indicator.data.data_list
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
        # print(len(trades), "trades")
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
        print("result:", self.result)
        return x, log_y

    # TODO: 注入 functions
    def get_buy_spread(self, gamma, q = 0):  # q是仓位，为正是多仓
        a, b = 0.5 * (1 - q) * gamma * self.result.average_std, self.result.average_std * (1 / gamma) * math.log1p(gamma/max(abs(self.result.buy_k) * self.result.average_std, 1e-6))
        # print("buy spread:", a, b, self.result.average_std, self.result.buy_k)
        return a + b

    def get_sell_spread(self, gamma, q = 0):  # q是仓位，为正是多仓
        a, b = 0.5 * (1 + q) * gamma * self.result.average_std, self.result.average_std * (1 / gamma) * math.log1p(gamma/max(abs(self.result.sell_k) * self.result.average_std, 1e-6))
        # print("sell spread:", a, b, self.result.average_std, self.result.sell_k)
        return a + b

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

    async def on_tick(self):
        # print("calculating trade intensity...")
        import time
        dl = self.trades_indicator.data.data_list
        print("len trades:", len(dl), "start time:", (time.time() - dl[0][0].timestamp) if len(dl) > 0 else None, "end time:", (time.time() - dl[-1][0].timestamp) if len(dl) > 0 else None)
        self.calculate()

    def get_vars(self) -> dict[str, Any]:
        """返回交易强度变量，带缓存"""
        result = self.result
        if result is None:
            raise ValueError("Result not ready")
        return {
            "trade_intensity_average_price": result.average_price,
            "trade_intensity_average_std": result.average_std,
            "trade_intensity_buy_k": result.buy_k,
            "trade_intensity_buy_b": result.buy_b,
            "trade_intensity_buy_correlation": result.buy_correlation,
            "trade_intensity_sell_k": result.sell_k,
            "trade_intensity_sell_b": result.sell_b,
            "trade_intensity_sell_correlation": result.sell_correlation,
        }
    # TODO: ready机制、functions注入

    @property
    def ready(self) -> bool:
        return self.result is not None

    def get_functions(self):
        return {
            "get_buy_spread": self.get_buy_spread,
            "get_sell_spread": self.get_sell_spread,
        }

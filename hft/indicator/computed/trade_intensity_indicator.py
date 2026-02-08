"""
交易强度指标 - 用于 AS 做市策略

基于历史成交数据估计订单到达率参数 kappa (k)。

原理：
- 收集一段时间内的成交数据
- 统计不同价格偏离下的成交量分布
- 拟合指数衰减模型：λ(δ) = A * exp(-k * δ)
- 取对数后线性回归：log(λ) = log(A) - k * δ

使用示例：
    intensity = trading_pair.query_indicator(
        TradeIntensityIndicator,
        total_range_seconds=600.0,
    )
    if intensity:
        result = intensity.get_value()
        if result and result.is_valid:
            spread = calculator.get_optimal_spread("buy", gamma=0.1)
"""
import math
import time
import logging
import numpy as np
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional
import matplotlib.pyplot as plt
if TYPE_CHECKING:
    from ..core.healthy_data import HealthyDataArray
    from .datasource.trades_datasource import TradeData, TradesDataSource


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


class TradeIntensityIndicator(LazyIndicator[IntensityResult]):
    """
    交易强度指标 - 用于 AS 做市策略

    将 TradeIntensityCalculator 封装为 LazyIndicator，
    挂载到 TradingPairDataSource，享受统一的生命周期管理。

    使用示例：
        intensity = trading_pair.query_indicator(
            TradeIntensityIndicator,
            total_range_seconds=600.0,
        )
        if intensity:
            result = intensity.get_value()
            if result and result.is_valid:
                inv_adj, arr_adj = intensity.get_optimal_spread("buy", gamma=0.1)
    """
    depends_on = ["trades", "order_book"]

    def __init__(
        self,
        sub_range_seconds: float = 15.0,
        total_range_seconds: float = 600.0,
        precision: int = 20,
        precision_std_range: float = 2.0,
        min_correlation: float = 0.5,
        min_trades: int = 50,
        name: Optional[str] = None,
        interval: float = 1.0,
        auto_stop_timeout: float = 300.0,
    ):
        """
        初始化交易强度指标

        Args:
            sub_range_seconds: 子区间长度（秒）
            total_range_seconds: 总分析时间范围（秒）
            precision: 价格分桶精度
            precision_std_range: 价格范围（标准差的倍数）
            min_correlation: 最小相关系数阈值
            min_trades: 最少成交笔数
            name: 指标名称
            interval: 更新间隔（秒）
            auto_stop_timeout: 自动停止超时（秒）
        """
        super().__init__(name=name, interval=interval, auto_stop_timeout=auto_stop_timeout)

        self._calculator = TradeIntensityCalculator(
            sub_range_seconds=sub_range_seconds,
            total_range_seconds=total_range_seconds,
            precision=precision,
            precision_std_range=precision_std_range,
            min_correlation=min_correlation,
            min_trades=min_trades,
        )

    @property
    def calculator(self) -> TradeIntensityCalculator:
        """获取内部计算器（用于访问 get_optimal_spread 等方法）"""
        return self._calculator

    @property
    def is_ready(self) -> bool:
        """检查是否有有效的计算结果"""
        return self._value is not None and self._value.is_valid

    async def _update_value(self) -> None:
        """更新指标值"""
        trades_ds = self.get_datasource("trades")
        ob_ds = self.get_datasource("order_book")

        if trades_ds is None:
            return

        # 获取所有成交数据
        trades = trades_ds.get_all()
        if not trades:
            return

        # 获取订单簿（可选）
        ob = None
        if ob_ds is not None:
            ob_data = ob_ds.get_latest()
            if ob_data is not None:
                # 转换为 dict 格式
                ob = {
                    'bids': getattr(ob_data, 'bids', []),
                    'asks': getattr(ob_data, 'asks', []),
                }

        # 更新计算
        self._value = self._calculator.update(trades, ob)

    def get_optimal_spread(
        self,
        side: str,
        gamma: float,
        inventory: float = 0.0,
    ) -> tuple[float, float]:
        """
        计算最优价差（委托给内部计算器）

        Args:
            side: "buy" 或 "sell"
            gamma: 风险厌恶系数
            inventory: 标准化库存（正=多头）

        Returns:
            (inventory_adjustment, arrival_adjustment) 两部分价差
        """
        return self._calculator.get_optimal_spread(side, gamma, inventory)

    @property
    def log_state_dict(self) -> dict:
        base = super().log_state_dict
        if self._value:
            base.update({
                "buy_k": self._value.buy_k,
                "sell_k": self._value.sell_k,
                "imbalance": self._value.imbalance,
                "is_valid": self._value.is_valid,
            })
        return base

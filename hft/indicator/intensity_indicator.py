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
from dataclasses import dataclass
from typing import Optional, Any, TYPE_CHECKING

import numpy as np

from .lazy_indicator import LazyIndicator

if TYPE_CHECKING:
    from ..datasource.trades_datasource import TradeData


def _get_trade_attr(trade: Any, attr: str, default: Any = 0) -> Any:
    """从 trade 对象获取属性，支持 dict 和 TradeData"""
    if hasattr(trade, attr):
        return getattr(trade, attr, default)
    elif isinstance(trade, dict):
        return trade.get(attr, default)
    return default


@dataclass
class IntensityResult:
    """强度计算结果"""
    # 基础统计
    average_price: float = 0.0
    average_std: float = 0.0  # 相对标准差（比例）
    trade_count: int = 0
    total_amount: float = 0.0

    # 买方强度参数
    buy_k: float = 0.0  # 订单到达率衰减参数
    buy_A: float = 0.0  # 基础强度（截距）
    buy_correlation: float = 0.0  # 拟合相关系数

    # 卖方强度参数
    sell_k: float = 0.0
    sell_A: float = 0.0
    sell_correlation: float = 0.0

    # 订单簿不平衡
    imbalance: float = 0.0  # >0 买盘强，<0 卖盘强

    @property
    def is_valid(self) -> bool:
        """检查结果是否有效"""
        return (
            self.trade_count >= 10 and
            self.buy_correlation >= 0.5 and
            self.sell_correlation >= 0.5
        )


class TradeIntensityCalculator:
    """
    交易强度计算器（核心计算逻辑）

    从成交数据估计订单到达率参数，用于 AS 做市策略。
    """

    def __init__(
        self,
        sub_range_seconds: float = 15.0,
        total_range_seconds: float = 600.0,
        precision: int = 20,
        precision_std_range: float = 2.0,
        min_correlation: float = 0.5,
        min_trades: int = 50,
    ):
        """
        初始化计算器

        Args:
            sub_range_seconds: 子区间长度（秒），用于计算价格基准
            total_range_seconds: 总分析时间范围（秒）
            precision: 价格分桶精度（每侧桶数）
            precision_std_range: 价格范围（标准差的倍数）
            min_correlation: 最小相关系数阈值
            min_trades: 最少成交笔数
        """
        self._sub_range_seconds = sub_range_seconds
        self._total_range_seconds = total_range_seconds
        self._precision = precision
        self._precision_std_range = precision_std_range
        self._min_correlation = min_correlation
        self._min_trades = min_trades

        # 结果
        self._result: Optional[IntensityResult] = None
        self._last_calculate_time: float = 0.0

    @property
    def result(self) -> Optional[IntensityResult]:
        """获取计算结果"""
        return self._result

    @property
    def is_ready(self) -> bool:
        """检查是否有有效的计算结果"""
        return self._result is not None and self._result.is_valid

    def update(
        self,
        trades: list,
        order_book: Optional[dict] = None,
    ) -> Optional[IntensityResult]:
        """
        更新计算

        Args:
            trades: 成交列表，每个元素为 TradeData 或 dict
            order_book: 订单簿 {bids: [[price, amount], ...], asks: [...]}

        Returns:
            计算结果，如果数据不足返回 None
        """
        if len(trades) < self._min_trades:
            return None

        # 过滤时间范围内的数据
        now = time.time()
        cutoff = (now - self._total_range_seconds) * 1000  # 转换为毫秒
        trades = [t for t in trades if _get_trade_attr(t, 'timestamp', 0) >= cutoff]

        if len(trades) < self._min_trades:
            return None

        result = self._calculate(trades)

        # 计算订单簿不平衡
        if order_book and result.buy_k > 0 and result.sell_k > 0:
            result.imbalance = self._calculate_imbalance(
                order_book, result.buy_k, result.sell_k, result.average_price
            )

        self._result = result
        self._last_calculate_time = now
        return result

    def _calculate(self, trades: list) -> IntensityResult:
        """
        核心计算逻辑

        Args:
            trades: 成交列表

        Returns:
            IntensityResult
        """
        result = IntensityResult()
        result.trade_count = len(trades)

        # 1. 计算加权平均价格和成交量
        total_value = 0.0
        total_amount = 0.0
        for t in trades:
            price = _get_trade_attr(t, 'price', 0)
            amount = abs(_get_trade_attr(t, 'amount', 0))
            total_value += price * amount
            total_amount += amount

        if total_amount == 0:
            return result

        result.average_price = total_value / total_amount
        result.total_amount = total_amount

        # 2. 计算价格相对标准差
        weighted_dev_sum = 0.0
        for t in trades:
            price = _get_trade_attr(t, 'price', 0)
            amount = abs(_get_trade_attr(t, 'amount', 0))
            # 使用相对价格偏离
            rel_dev = abs(price - result.average_price) / result.average_price
            weighted_dev_sum += rel_dev * amount

        result.average_std = weighted_dev_sum / total_amount
        if result.average_std < 1e-9:
            result.average_std = 0.001  # 默认 0.1%

        # 3. 构建价格分桶
        # x 轴：相对价格偏离，范围 [-precision_std_range * std, +precision_std_range * std]
        x = np.linspace(
            -self._precision_std_range * result.average_std,
            self._precision_std_range * result.average_std,
            num=2 * self._precision + 1
        )
        y = np.zeros_like(x)

        # 4. 统计各桶的成交量
        if len(trades) == 0:
            return result

        # 按时间排序
        sorted_trades = sorted(trades, key=lambda t: _get_trade_attr(t, 'timestamp', 0))

        start_time = _get_trade_attr(sorted_trades[0], 'timestamp', 0)
        start_price = _get_trade_attr(sorted_trades[0], 'price', result.average_price)
        last_timestamp = start_time
        last_price = start_price

        for trade in sorted_trades:
            ts = _get_trade_attr(trade, 'timestamp', 0)
            price = _get_trade_attr(trade, 'price', 0)
            amount = abs(_get_trade_attr(trade, 'amount', 0))

            # 更新子区间起始价格（时间戳为毫秒）
            sub_range_ms = self._sub_range_seconds * 1000
            while ts - start_time > sub_range_ms:
                start_time += sub_range_ms
                # 线性插值计算新的起始价格
                if ts > last_timestamp:
                    a = start_time - last_timestamp
                    b = ts - last_timestamp
                    if b > 0:
                        start_price = (a * price + (b - a) * last_price) / b
                    else:
                        start_price = price

            # 计算相对价格偏离
            if start_price > 0:
                rel_price_diff = (price - start_price) / start_price
            else:
                rel_price_diff = 0

            # 映射到桶索引
            bucket_range = self._precision_std_range * result.average_std
            if bucket_range > 0:
                mapped_idx = int(round(
                    self._precision * rel_price_diff / bucket_range
                ))
            else:
                mapped_idx = 0

            # 累计成交量到对应桶
            # 买方成交（价格下跌）-> 负偏离 -> 左侧桶
            # 卖方成交（价格上涨）-> 正偏离 -> 右侧桶
            if rel_price_diff < 0:
                # 买方吃单，统计到左侧
                start_idx = max(self._precision + mapped_idx, 0)
                end_idx = self._precision
                y[start_idx:end_idx] += amount
            else:
                # 卖方吃单，统计到右侧
                start_idx = self._precision + 1
                end_idx = min(self._precision + 1 + mapped_idx, 2 * self._precision + 1)
                y[start_idx:end_idx] += amount

            last_price = price
            last_timestamp = ts

        # 5. 对数变换并线性回归
        log_y = np.log(np.maximum(y, 1e-9))

        # 买方侧拟合（左侧，排除边界）
        buy_slice = slice(1, self._precision - 1)
        buy_x = x[buy_slice]
        buy_y = log_y[buy_slice]

        if len(buy_x) >= 3:
            result.buy_k, result.buy_A = np.polyfit(buy_x, buy_y, 1)
            result.buy_k = -result.buy_k  # k 应该为正（强度随偏离递减）
            corr_matrix = np.corrcoef(buy_x, buy_y)
            if corr_matrix.shape == (2, 2):
                result.buy_correlation = abs(float(corr_matrix[0, 1]))

            # 如果相关性太低，重置为 0
            if result.buy_correlation < self._min_correlation:
                result.buy_k = 0

        # 卖方侧拟合（右侧，排除边界）
        sell_slice = slice(self._precision + 2, -1)
        sell_x = x[sell_slice]
        sell_y = log_y[sell_slice]

        if len(sell_x) >= 3:
            result.sell_k, result.sell_A = np.polyfit(sell_x, sell_y, 1)
            # 卖方侧 k 本身应该为负（右侧斜率），取绝对值
            result.sell_k = abs(result.sell_k)
            corr_matrix = np.corrcoef(sell_x, sell_y)
            if corr_matrix.shape == (2, 2):
                result.sell_correlation = abs(float(corr_matrix[0, 1]))

            if result.sell_correlation < self._min_correlation:
                result.sell_k = 0

        return result

    def _calculate_imbalance(
        self,
        order_book: dict,
        buy_k: float,
        sell_k: float,
        mid_price: float,
    ) -> float:
        """
        计算订单簿不平衡度

        使用指数加权，距离越远权重越低。

        Args:
            order_book: {bids: [[price, amount], ...], asks: [...]}
            buy_k: 买方衰减参数
            sell_k: 卖方衰减参数
            mid_price: 中间价

        Returns:
            不平衡度，>0 表示买盘强
        """
        bids = order_book.get('bids', [])
        asks = order_book.get('asks', [])

        if not bids or not asks or mid_price <= 0:
            return 0.0

        # 买盘强度（加权求和）
        buy_intensity = 0.0
        if bids:
            best_bid = bids[0][0]
            for price, amount in bids:
                # 使用相对距离
                rel_dist = abs(price - best_bid) / mid_price
                buy_intensity += amount * math.exp(-rel_dist * buy_k)

        # 卖盘强度
        sell_intensity = 0.0
        if asks:
            best_ask = asks[0][0]
            for price, amount in asks:
                rel_dist = abs(price - best_ask) / mid_price
                sell_intensity += amount * math.exp(-rel_dist * sell_k)

        total = buy_intensity + sell_intensity
        if total == 0:
            return 0.0

        return (buy_intensity - sell_intensity) / total

    def get_optimal_spread(
        self,
        side: str,
        gamma: float,
        inventory: float = 0.0,
    ) -> tuple[float, float]:
        """
        计算最优价差

        基于 AS 公式：
        - 库存调整: 0.5 * (1 -/+ q) * γ * σ
        - 到达率调整: σ * (1/γ) * ln(1 + γ/(k*σ))

        Args:
            side: "buy" 或 "sell"
            gamma: 风险厌恶系数
            inventory: 标准化库存（正=多头）

        Returns:
            (inventory_adjustment, arrival_adjustment) 两部分价差
        """
        if self._result is None:
            return 0.0, 0.0

        sigma = self._result.average_std

        if side == "buy":
            k = self._result.buy_k
            # 多头时减少买入意愿，价差更大
            inv_adj = 0.5 * (1 - inventory) * gamma * sigma
        else:
            k = self._result.sell_k
            # 多头时增加卖出意愿，价差更小
            inv_adj = 0.5 * (1 + inventory) * gamma * sigma

        # 到达率调整
        if k > 0 and gamma > 0:
            arrival_adj = sigma * (1 / gamma) * math.log1p(gamma / max(k * sigma, 1e-9))
        else:
            arrival_adj = sigma  # 默认使用波动率

        return inv_adj, arrival_adj


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

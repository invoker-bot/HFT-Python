"""
公允资金费率指标

ExchangeClass 级别，每个交易所平台一个实例。
从 ClickHouse 读取近 30 天的 index_price、mark_price 和 funding_rate 数据，
以 daily_excess_funding_rate = (funding_rate - base_funding_rate) * 24 / interval_hours 为自变量，
以 spread = (index_price - mark_price) / mark_price 为因变量，
通过非均匀分箱 + 样条插值拟合曲线，过 (0, 0) 点。
"""
import logging
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
from scipy.interpolate import UnivariateSpline

from ..base import BaseExchangeClassDataIndicator

logger = logging.getLogger(__name__)

# 非均匀 bin 边界：对数间距，靠近 0 密集
_POSITIVE_EDGES = [0, 0.0001, 0.0002, 0.0005, 0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5]
DEFAULT_BIN_EDGES = sorted(set([-e for e in _POSITIVE_EDGES] + _POSITIVE_EDGES))


@dataclass
class FairFundingRateResult:
    """公允资金费率拟合结果"""
    bin_centers: np.ndarray            # 分箱中心点 (excess_fr 值)
    bin_medians: np.ndarray            # 各箱中位数 (spread)
    bin_counts: np.ndarray             # 各箱数据点数
    spline: UnivariateSpline           # 样条拟合函数: excess_fr -> spread
    data_points_count: int             # 总数据点数
    excess_fr_range: tuple[float, float]  # 有效超额费率范围


class FairFundingRateIndicator(BaseExchangeClassDataIndicator[dict]):
    """
    公允资金费率指标（ExchangeClass 级别）

    从 ClickHouse 历史数据拟合 超额资金费率-价差 曲线，
    提供 predict_fair_spread(excess_fr) 函数供 strategy 使用。

    横轴: daily_excess_fr = (funding_rate - base_funding_rate) * 24 / interval_hours
    纵轴: spread = (index_price - mark_price) / mark_price
    """
    DEFAULT_IS_ARRAY = False
    DEFAULT_DISABLE_SECONDS = None
    DEFAULT_MAX_AGE = 600.0

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.result: Optional[FairFundingRateResult] = None

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self._min_bin_count = kwargs.get("min_bin_count", 1)
        self._days = kwargs.get("days", 30)
        self._smoothing_factor = kwargs.get("smoothing_factor", None)

    @property
    def interval(self) -> float:
        return 180.0

    @property
    def ready(self) -> bool:
        return self.result is not None

    async def _query_data(self) -> list[tuple]:
        """
        从 ClickHouse 分别查询三表，Python 端按 trading_pair 合并。
        返回 (daily_excess_fr, index_price, mark_price) 列表。
        """
        db = self.root.database
        if db is None:
            logger.warning("数据库未配置，无法查询公允资金费率数据")
            return []

        exchange_name = self.exchange_class
        params = {"exchange_name": exchange_name, "days": self._days}

        # 查询日超额费率 = (funding_rate - base_funding_rate) * 24 / interval_hours
        fr_result = await db.connector.query("""
            SELECT trading_pair, timestamp_1min,
                   avg((funding_rate - base_funding_rate) * 24 / interval_hours) as daily_excess_fr
            FROM funding_rate
            WHERE exchange_name = %(exchange_name)s
                AND timestamp >= now() - INTERVAL %(days)s DAY
                AND interval_hours > 0
            GROUP BY trading_pair, timestamp_1min
        """, parameters=params)

        ip_result = await db.connector.query("""
            SELECT trading_pair, timestamp_1min, avg(index_price) as index_price
            FROM index_price
            WHERE exchange_name = %(exchange_name)s
                AND timestamp >= now() - INTERVAL %(days)s DAY
            GROUP BY trading_pair, timestamp_1min
        """, parameters=params)

        mp_result = await db.connector.query("""
            SELECT trading_pair, timestamp_1min, avg(mark_price) as mark_price
            FROM mark_price
            WHERE exchange_name = %(exchange_name)s
                AND timestamp >= now() - INTERVAL %(days)s DAY
            GROUP BY trading_pair, timestamp_1min
        """, parameters=params)

        ip_map = {}
        for tp, ts, price in ip_result.result_rows:
            ip_map[tp] = price
        mp_map = {}
        for tp, ts, price in mp_result.result_rows:
            mp_map[tp] = price

        merged = []
        for tp, ts, efr in fr_result.result_rows:
            ip = ip_map.get(tp)
            mp = mp_map.get(tp)
            if ip is not None and mp is not None and mp != 0:
                merged.append((efr, ip, mp))
        return merged

    def _fit_curve(self, excess_frs: np.ndarray, spreads: np.ndarray) -> Optional[FairFundingRateResult]:
        """非均匀分箱 + 样条插值拟合，过 (0, 0) 点"""
        # 裁剪到 [-0.5, 0.5]
        mask = (excess_frs >= -0.5) & (excess_frs <= 0.5)
        excess_frs = excess_frs[mask]
        spreads = spreads[mask]

        if len(excess_frs) < 5:
            logger.warning("数据点不足 (%d)，无法拟合", len(excess_frs))
            return None

        bin_edges = np.array(DEFAULT_BIN_EDGES)
        num_bins = len(bin_edges) - 1
        bin_indices = np.digitize(excess_frs, bin_edges) - 1
        bin_indices = np.clip(bin_indices, 0, num_bins - 1)

        bin_centers = []
        bin_medians = []
        bin_counts = []

        for i in range(num_bins):
            bin_mask = bin_indices == i
            count = bin_mask.sum()
            center = (bin_edges[i] + bin_edges[i + 1]) / 2
            if count >= self._min_bin_count:
                median = np.median(spreads[bin_mask])
                bin_centers.append(center)
                bin_medians.append(median)
                bin_counts.append(count)

        # 强制加入 (0, 0)
        max_count = max(bin_counts) * 2 if bin_counts else 100
        bin_centers.append(0.0)
        bin_medians.append(0.0)
        bin_counts.append(max_count)

        order = np.argsort(bin_centers)
        bin_centers = np.array(bin_centers)[order]
        bin_medians = np.array(bin_medians)[order]
        bin_counts = np.array(bin_counts)[order]

        # 去重
        _, unique_idx = np.unique(bin_centers, return_index=True)
        bin_centers = bin_centers[unique_idx]
        bin_medians = bin_medians[unique_idx]
        bin_counts = bin_counts[unique_idx]

        if len(bin_centers) < 4:
            logger.warning("有效分箱不足 (%d)，无法拟合样条", len(bin_centers))
            return None

        weights = np.sqrt(bin_counts)
        k = min(3, len(bin_centers) - 1)
        try:
            spline = UnivariateSpline(
                bin_centers, bin_medians, w=weights,
                s=self._smoothing_factor, k=k
            )
        except Exception as e:
            logger.warning("样条拟合失败: %s", e)
            return None

        return FairFundingRateResult(
            bin_centers=bin_centers,
            bin_medians=bin_medians,
            bin_counts=bin_counts,
            spline=spline,
            data_points_count=len(excess_frs),
            excess_fr_range=(float(bin_centers[0]), float(bin_centers[-1])),
        )

    async def on_tick(self) -> bool:
        """定时查询数据并拟合曲线"""
        rows = await self._query_data()
        if not rows:
            return False

        excess_frs = []
        spreads = []
        for efr, idx_price, mk_price in rows:
            if mk_price is None or mk_price == 0 or idx_price is None or efr is None:
                continue
            spread = (idx_price - mk_price) / mk_price
            excess_frs.append(efr)
            spreads.append(spread)

        if not spreads:
            return False

        result = self._fit_curve(np.array(excess_frs), np.array(spreads))
        if result is not None:
            self.result = result
            logger.info(
                "公允资金费率拟合完成: %s, %d 数据点, %d 有效分箱, excess_fr 范围 [%.6f, %.6f]",
                self.exchange_class,
                result.data_points_count,
                len(result.bin_centers),
                result.excess_fr_range[0],
                result.excess_fr_range[1],
            )
            await self.data.update({
                "data_points": result.data_points_count,
                "num_bins": len(result.bin_centers),
                "excess_fr_range": result.excess_fr_range,
            })
        return result is not None

    def predict_fair_spread(self, excess_fr: float) -> float:
        """根据超额资金费率预测公允价差"""
        if self.result is None:
            raise ValueError("拟合结果未就绪")
        clamped = np.clip(excess_fr, self.result.excess_fr_range[0], self.result.excess_fr_range[1])
        return float(self.result.spline(clamped))

    def get_vars(self) -> dict[str, Any]:
        """返回拟合参数"""
        if self.result is None:
            raise ValueError("公允资金费率拟合结果未就绪")
        return {
            "fair_funding_rate_bin_centers": self.result.bin_centers.tolist(),
            "fair_funding_rate_bin_medians": self.result.bin_medians.tolist(),
            "fair_funding_rate_data_points": self.result.data_points_count,
        }

    def get_functions(self) -> dict[str, Any]:
        return {
            "predict_fair_spread": self.predict_fair_spread,
        }

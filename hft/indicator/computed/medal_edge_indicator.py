"""
MedalEdge 指标

Feature 0005: Executor 动态条件与变量注入机制
Feature 0006: 计算类 Indicator 支持 requires 标记

计算 taker 相对于 maker 的百分比优势。
原名 edge，重命名为 medal_edge 以更直观。
"""
import time
from dataclasses import dataclass
from typing import Any, Optional, TYPE_CHECKING

from ..base import BaseIndicator

if TYPE_CHECKING:
    from ..datasource.trades_datasource import TradesDataSource, TradeData


@dataclass
class MedalEdgeData:
    """MedalEdge 数据点"""
    medal_edge: float
    buy_edge: float
    sell_edge: float
    timestamp: float


class MedalEdgeIndicator(BaseIndicator[MedalEdgeData]):
    """
    Medal Edge 指标

    计算 taker 相对于 maker 的百分比优势。

    公式（量纲无关）：
    - 买入：edge = (p_final - vwap_buy) / p_final - taker_fee
    - 卖出：edge = (vwap_sell - p_final) / p_final - taker_fee

    正值表示 taker 有优势，如 0.001 表示 0.1%

    requires 行为（Issue 0006/0007）：
    - 被 Executor requires 依赖时：on_tick() 定期计算并缓存到 _data
    - 未被依赖时：calculate_vars() lazy 按需计算
    """

    def __init__(
        self,
        exchange_class: str,
        symbol: str,
        trades: str = "trades",
        window: float = 60.0,
        taker_fee: float = 0.0005,
        ready_condition: Optional[str] = None,
        **kwargs,
    ):
        name = f"MedalEdge:{exchange_class}:{symbol}"
        super().__init__(
            name=name,
            interval=10.0,  # 每 10 秒 tick 一次（仅在被 requires 时有效）
            ready_condition=ready_condition,
            window=window,
            **kwargs,
        )
        self._exchange_class = exchange_class
        self._symbol = symbol
        self._trades_id = trades
        self._taker_fee = taker_fee

        # 缓存（用于 lazy 计算）
        self._cached_edge: Optional[MedalEdgeData] = None
        self._cache_timestamp: float = 0.0
        self._cache_ttl: float = 10.0  # 缓存 10 秒

    def _get_trades_indicator(self) -> Optional["TradesDataSource"]:
        """获取 Trades 数据源"""
        if self.root is None:
            return None
        indicator_group = getattr(self.root, 'indicator_group', None)
        if indicator_group is None:
            return None
        return indicator_group.query_indicator(
            self._trades_id,
            self._exchange_class,
            self._symbol,
        )

    def _get_recent_trades(self) -> list["TradeData"]:
        """获取窗口内的 trades"""
        trades_indicator = self._get_trades_indicator()
        if trades_indicator is None:
            return []

        now = time.time()
        cutoff = now - self._window
        return [
            t for t in trades_indicator._data
            if t.timestamp >= cutoff
        ]

    def _calculate_edge(
        self,
        trades: list["TradeData"],
        is_buy: bool,
        current_price: float,
    ) -> float:
        """计算 taker 优势"""
        if not trades or current_price <= 0:
            return 0.0

        buy_qty = 0.0
        buy_notional = 0.0
        sell_qty = 0.0
        sell_notional = 0.0

        for trade in trades:
            if trade.side == "buy":
                buy_qty += trade.amount
                buy_notional += trade.cost
            else:
                sell_qty += trade.amount
                sell_notional += trade.cost

        if is_buy:
            if buy_qty <= 0:
                return 0.0
            vwap_buy = buy_notional / buy_qty
            edge = (current_price - vwap_buy) / current_price - self._taker_fee
        else:
            if sell_qty <= 0:
                return 0.0
            vwap_sell = sell_notional / sell_qty
            edge = (vwap_sell - current_price) / current_price - self._taker_fee

        return edge

    def _compute_edge_data(self) -> Optional[MedalEdgeData]:
        """计算 MedalEdge 数据"""
        trades = self._get_recent_trades()
        if not trades:
            return None

        # 使用最新成交价作为当前价格
        current_price = trades[-1].price if trades else 0.0

        buy_edge = self._calculate_edge(trades, True, current_price)
        sell_edge = self._calculate_edge(trades, False, current_price)

        now = time.time()
        return MedalEdgeData(
            medal_edge=buy_edge,  # 默认返回 buy edge，calculate_vars 会根据 direction 选择
            buy_edge=buy_edge,
            sell_edge=sell_edge,
            timestamp=now,
        )

    async def on_tick(self) -> bool:
        """
        定期更新 MedalEdge（仅在被 requires 时调用）

        如果未被 requires 依赖，此方法不会被调用（interval 被忽略）。
        """
        # 只有被 requires 依赖时才定期更新
        if not self.is_required:
            return False

        edge_data = self._compute_edge_data()
        if edge_data is None:
            return False

        # 缓存到 _data
        self._data.append(edge_data.timestamp, edge_data)

        # 更新 lazy 缓存
        self._cached_edge = edge_data
        self._cache_timestamp = edge_data.timestamp

        return False

    def ready_internal(self) -> bool:
        """
        覆盖 ready_internal() 实现（Issue 0006/0007）

        要求至少有 1 个 edge 值缓存到 _data。
        """
        # 如果被 requires 依赖，检查 _data
        if self.is_required:
            return len(self._data) > 0

        # 如果未被依赖，检查依赖的 Trades 是否 ready
        trades_indicator = self._get_trades_indicator()
        if trades_indicator is None:
            return False
        return trades_indicator.is_ready()

    def calculate_vars(self, direction: int) -> dict[str, Any]:
        """
        返回 medal_edge 变量

        requires 行为：
        - 被依赖时：从 _data 读取最新值（on_tick 定期更新）
        - 未被依赖时：lazy 按需计算，缓存 10 秒
        """
        # 如果被 requires 依赖，从 _data 读取
        if self.is_required and len(self._data) > 0:
            edge_data = self._data.latest
            edge = edge_data.buy_edge if direction == 1 else edge_data.sell_edge
            return {
                "medal_edge": edge,
                "medal_buy_edge": edge_data.buy_edge,
                "medal_sell_edge": edge_data.sell_edge,
            }

        # lazy 模式：检查缓存
        now = time.time()
        if self._cached_edge is not None and now - self._cache_timestamp < self._cache_ttl:
            edge = self._cached_edge.buy_edge if direction == 1 else self._cached_edge.sell_edge
            return {
                "medal_edge": edge,
                "medal_buy_edge": self._cached_edge.buy_edge,
                "medal_sell_edge": self._cached_edge.sell_edge,
            }

        # 缓存失效，重新计算
        edge_data = self._compute_edge_data()
        if edge_data is None:
            return {
                "medal_edge": 0.0,
                "medal_buy_edge": 0.0,
                "medal_sell_edge": 0.0,
            }

        # 更新缓存
        self._cached_edge = edge_data
        self._cache_timestamp = edge_data.timestamp

        edge = edge_data.buy_edge if direction == 1 else edge_data.sell_edge
        return {
            "medal_edge": edge,
            "medal_buy_edge": edge_data.buy_edge,
            "medal_sell_edge": edge_data.sell_edge,
        }

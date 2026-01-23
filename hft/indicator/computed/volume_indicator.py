"""
Volume 成交量指标

Feature 0005: Executor 动态条件与变量注入机制
Feature 0006: 计算类 Indicator 支持 requires 标记
Issue 0005: 使用 volume_notional 避免与内置 notional 冲突
"""
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from ..base import BaseIndicator

if TYPE_CHECKING:
    from ..datasource.trades_datasource import TradeData, TradesDataSource


@dataclass
class VolumeData:
    """成交量数据点"""
    volume: float
    buy_volume: float
    sell_volume: float
    volume_notional: float
    buy_volume_notional: float
    sell_volume_notional: float
    timestamp: float


class VolumeIndicator(BaseIndicator[VolumeData]):
    """
    成交量指标

    从 Trades 计算窗口内的成交量。

    requires 行为（Issue 0006/0007）：
    - 被 Executor requires 依赖时：on_tick() 定期计算并缓存到 _data
    - 未被依赖时：calculate_vars() lazy 按需计算
    """

    def __init__(
        self,
        exchange_class: str,
        symbol: str,
        trades: str = "trades",
        window: float = 300.0,
        ready_condition: Optional[str] = None,
        **kwargs,
    ):
        name = f"Volume:{exchange_class}:{symbol}"
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

        # 缓存（用于 lazy 计算）
        self._cached_volume: Optional[VolumeData] = None
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
        return [t for t in trades_indicator._data if t.timestamp >= cutoff]

    def _compute_volume_data(self) -> Optional[VolumeData]:
        """计算成交量数据"""
        trades = self._get_recent_trades()
        if not trades:
            return None

        buy_volume = sum(t.amount for t in trades if t.side == "buy")
        sell_volume = sum(t.amount for t in trades if t.side == "sell")
        buy_notional = sum(t.cost for t in trades if t.side == "buy")
        sell_notional = sum(t.cost for t in trades if t.side == "sell")

        now = time.time()
        return VolumeData(
            volume=buy_volume + sell_volume,
            buy_volume=buy_volume,
            sell_volume=sell_volume,
            volume_notional=buy_notional + sell_notional,
            buy_volume_notional=buy_notional,
            sell_volume_notional=sell_notional,
            timestamp=now,
        )

    async def on_tick(self) -> bool:
        """
        定期更新成交量（仅在被 requires 时调用）

        如果未被 requires 依赖，此方法不会被调用（interval 被忽略）。
        """
        # 只有被 requires 依赖时才定期更新
        if not self.is_required:
            return False

        volume_data = self._compute_volume_data()
        if volume_data is None:
            return False

        # 缓存到 _data
        self._data.append(volume_data.timestamp, volume_data)

        # 更新 lazy 缓存
        self._cached_volume = volume_data
        self._cache_timestamp = volume_data.timestamp

        return False

    def ready_internal(self) -> bool:
        """
        覆盖 ready_internal() 实现（Issue 0006/0007）

        要求至少有 1 个 volume 值缓存到 _data。
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
        返回成交量变量

        Issue 0005: 使用 volume_notional 避免与内置 notional 冲突

        requires 行为：
        - 被依赖时：从 _data 读取最新值（on_tick 定期更新）
        - 未被依赖时：lazy 按需计算，缓存 10 秒
        """
        # 如果被 requires 依赖，从 _data 读取
        if self.is_required and len(self._data) > 0:
            volume_data = self._data.latest
            return {
                "volume": volume_data.volume,
                "buy_volume": volume_data.buy_volume,
                "sell_volume": volume_data.sell_volume,
                "volume_notional": volume_data.volume_notional,
                "buy_volume_notional": volume_data.buy_volume_notional,
                "sell_volume_notional": volume_data.sell_volume_notional,
            }

        # lazy 模式：检查缓存
        now = time.time()
        if self._cached_volume is not None and now - self._cache_timestamp < self._cache_ttl:
            return {
                "volume": self._cached_volume.volume,
                "buy_volume": self._cached_volume.buy_volume,
                "sell_volume": self._cached_volume.sell_volume,
                "volume_notional": self._cached_volume.volume_notional,
                "buy_volume_notional": self._cached_volume.buy_volume_notional,
                "sell_volume_notional": self._cached_volume.sell_volume_notional,
            }

        # 缓存失效，重新计算
        volume_data = self._compute_volume_data()
        if volume_data is None:
            return {
                "volume": 0.0,
                "buy_volume": 0.0,
                "sell_volume": 0.0,
                "volume_notional": 0.0,
                "buy_volume_notional": 0.0,
                "sell_volume_notional": 0.0,
            }

        # 更新缓存
        self._cached_volume = volume_data
        self._cache_timestamp = volume_data.timestamp

        return {
            "volume": volume_data.volume,
            "buy_volume": volume_data.buy_volume,
            "sell_volume": volume_data.sell_volume,
            "volume_notional": volume_data.volume_notional,
            "buy_volume_notional": volume_data.buy_volume_notional,
            "sell_volume_notional": volume_data.sell_volume_notional,
        }

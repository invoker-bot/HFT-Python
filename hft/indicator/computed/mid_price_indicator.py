"""
MidPrice 中间价指标

Feature 0005: Executor 动态条件与变量注入机制
Feature 0006: 计算类 Indicator 支持 requires 标记
Issue 0005: 使用 orderbook_mid_price 避免与执行器注入的 mid_price 冲突
"""
import time
from dataclasses import dataclass
from typing import Any, Optional, TYPE_CHECKING

from ..base import BaseIndicator

if TYPE_CHECKING:
    from ..datasource.orderbook_datasource import OrderBookDataSource


@dataclass
class MidPriceData:
    """中间价数据点"""
    mid_price: float
    best_bid: Optional[float]
    best_ask: Optional[float]
    spread: Optional[float]
    timestamp: float


class MidPriceIndicator(BaseIndicator[MidPriceData]):
    """
    中间价格指标

    从 OrderBook 计算中间价。

    requires 行为（Issue 0006/0007）：
    - 被 Executor requires 依赖时：on_tick() 定期计算并缓存到 _data
    - 未被依赖时：calculate_vars() lazy 按需计算
    """

    def __init__(
        self,
        exchange_class: str,
        symbol: str,
        order_book: str = "order_book",
        ready_condition: Optional[str] = None,
        **kwargs,
    ):
        name = f"MidPrice:{exchange_class}:{symbol}"
        super().__init__(
            name=name,
            interval=5.0,  # 每 5 秒 tick 一次（仅在被 requires 时有效）
            ready_condition=ready_condition,
            window=0,
            **kwargs,
        )
        self._exchange_class = exchange_class
        self._symbol = symbol
        self._order_book_id = order_book

        # 缓存（用于 lazy 计算）
        self._cached_mid_price: Optional[MidPriceData] = None
        self._cache_timestamp: float = 0.0
        self._cache_ttl: float = 5.0  # 缓存 5 秒

    def _get_order_book_indicator(self) -> Optional["OrderBookDataSource"]:
        """获取 OrderBook 数据源"""
        if self.root is None:
            return None
        indicator_group = getattr(self.root, 'indicator_group', None)
        if indicator_group is None:
            return None
        return indicator_group.query_indicator(
            self._order_book_id,
            self._exchange_class,
            self._symbol,
        )

    def _compute_mid_price_data(self) -> Optional[MidPriceData]:
        """计算中间价数据"""
        ob_indicator = self._get_order_book_indicator()
        if ob_indicator is None or not ob_indicator.is_ready():
            return None

        ob = ob_indicator._data.latest
        if ob is None:
            return None

        best_bid = ob.bids[0].price if ob.bids else None
        best_ask = ob.asks[0].price if ob.asks else None
        spread = (best_ask - best_bid) if (best_bid and best_ask) else None

        now = time.time()
        return MidPriceData(
            mid_price=ob.mid_price,
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            timestamp=now,
        )

    async def on_tick(self) -> bool:
        """
        定期更新中间价（仅在被 requires 时调用）

        如果未被 requires 依赖，此方法不会被调用（interval 被忽略）。
        """
        # 只有被 requires 依赖时才定期更新
        if not self.is_required:
            return False

        mid_price_data = self._compute_mid_price_data()
        if mid_price_data is None:
            return False

        # 缓存到 _data
        self._data.append(mid_price_data.timestamp, mid_price_data)

        # 更新 lazy 缓存
        self._cached_mid_price = mid_price_data
        self._cache_timestamp = mid_price_data.timestamp

        return False

    def ready_internal(self) -> bool:
        """
        覆盖 ready_internal() 实现（Issue 0006/0007）

        要求至少有 1 个 mid_price 值缓存到 _data。
        """
        # 如果被 requires 依赖，检查 _data
        if self.is_required:
            return len(self._data) > 0

        # 如果未被依赖，检查依赖的 OrderBook 是否 ready
        ob_indicator = self._get_order_book_indicator()
        if ob_indicator is None:
            return False
        return ob_indicator.is_ready()

    def calculate_vars(self, direction: int) -> dict[str, Any]:
        """
        返回中间价变量

        Issue 0005: 使用 orderbook_mid_price 避免与执行器注入的 mid_price 冲突

        requires 行为：
        - 被依赖时：从 _data 读取最新值（on_tick 定期更新）
        - 未被依赖时：lazy 按需计算，缓存 5 秒
        """
        # 如果被 requires 依赖，从 _data 读取
        if self.is_required and len(self._data) > 0:
            mid_price_data = self._data.latest
            return {
                "orderbook_mid_price": mid_price_data.mid_price,
                "orderbook_best_bid": mid_price_data.best_bid,
                "orderbook_best_ask": mid_price_data.best_ask,
                "orderbook_spread": mid_price_data.spread,
            }

        # lazy 模式：检查缓存
        now = time.time()
        if self._cached_mid_price is not None and now - self._cache_timestamp < self._cache_ttl:
            return {
                "orderbook_mid_price": self._cached_mid_price.mid_price,
                "orderbook_best_bid": self._cached_mid_price.best_bid,
                "orderbook_best_ask": self._cached_mid_price.best_ask,
                "orderbook_spread": self._cached_mid_price.spread,
            }

        # 缓存失效，重新计算
        mid_price_data = self._compute_mid_price_data()
        if mid_price_data is None:
            return {
                "orderbook_mid_price": None,
                "orderbook_best_bid": None,
                "orderbook_best_ask": None,
                "orderbook_spread": None,
            }

        # 更新缓存
        self._cached_mid_price = mid_price_data
        self._cache_timestamp = mid_price_data.timestamp

        return {
            "orderbook_mid_price": mid_price_data.mid_price,
            "orderbook_best_bid": mid_price_data.best_bid,
            "orderbook_best_ask": mid_price_data.best_ask,
            "orderbook_spread": mid_price_data.spread,
        }

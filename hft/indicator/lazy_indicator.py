"""
Lazy Start 指标模块

挂载在 TradingPairDataSource 上的派生指标，支持 lazy_start 生命周期。

特性：
- lazy_start: 初始为 STOPPED，首次 query 时启动
- 自动 query 依赖的数据源，保持其活跃
- 5分钟无访问自动 stop()（保留计算结果）
- 支持多数据源依赖

与 base.py 中 BaseIndicator 的区别：
- base.py: 事件驱动，监听 DataSource 的 update 事件
- lazy.py: 轮询驱动，定期查询数据源并计算

使用示例：
    # 获取指标
    vwap = trading_pair.query_indicator(VWAPIndicator)
    if vwap:
        value = vwap.get_value()

    # 自定义指标（使用字符串 ID）
    class MyIndicator(LazyIndicator[float]):
        depends_on = ["trades", "order_book"]

        async def _update_value(self) -> None:
            trades_ds = self.get_datasource("trades")
            ob_ds = self.get_datasource("order_book")
            # 计算逻辑...
            self._value = result
"""
import time
from abc import abstractmethod
from typing import Optional, Generic, TypeVar, TYPE_CHECKING, ClassVar

from ..core.listener import Listener

if TYPE_CHECKING:
    from .group import IndicatorGroup
    from .base import BaseIndicator as NewBaseIndicator


T = TypeVar('T')  # 指标值类型


def _normalize_data_type(dt: str) -> str:
    """将数据类型统一转换为字符串"""
    return dt


class LazyIndicator(Listener, Generic[T]):
    """
    Lazy Start 指标基类 - 依赖底层数据源计算派生指标

    特性：
    - lazy_start: 初始为 STOPPED，首次 query 时启动
    - 自动 query 依赖的数据源
    - 5分钟无访问自动 stop()（保留计算结果）

    子类需要实现：
    - depends_on: 依赖的数据类型列表（字符串）
    - _update_value(): 计算并更新指标值
    """
    __pickle_exclude__ = (*Listener.__pickle_exclude__,)

    # 延迟启动
    lazy_start: bool = True

    # 依赖的数据类型（子类覆盖），使用字符串
    depends_on: ClassVar[list] = []

    # 默认超时（秒）
    DEFAULT_AUTO_STOP_TIMEOUT: float = 300.0

    def __init__(
        self,
        name: Optional[str] = None,
        interval: float = 1.0,
        auto_stop_timeout: float = DEFAULT_AUTO_STOP_TIMEOUT,
    ):
        if name is None:
            name = self.__class__.__name__
        super().__init__(name=name, interval=interval)

        self._value: Optional[T] = None
        self._last_access: float = 0.0
        self._auto_stop_timeout = auto_stop_timeout

    @property
    def trading_pair(self) -> "TradingPairDataSource":
        """获取所属的 TradingPairDataSource"""
        return self.parent

    @property
    def value(self) -> Optional[T]:
        """获取指标值（不刷新访问时间）"""
        return self._value

    def request_access(self) -> None:
        """刷新访问时间（防止自动 stop）"""
        self._last_access = time.time()

    def should_auto_stop(self) -> bool:
        """是否应该自动 stop"""
        if self._last_access == 0:
            return False
        return time.time() - self._last_access > self._auto_stop_timeout

    def get_datasource(self, data_type: str) -> Optional["NewBaseIndicator"]:
        """
        获取依赖的数据源

        Args:
            data_type: 数据类型字符串（如 "trades", "order_book"）

        会自动刷新数据源的访问时间，保持其活跃。

        使用 IndicatorGroup 获取数据源。
        """
        dt_str = _normalize_data_type(data_type)

        # 通过 IndicatorGroup 获取
        indicator_group = self._get_indicator_group()
        if indicator_group is not None:
            exchange_class, symbol = self._get_exchange_info()
            if exchange_class and symbol:
                indicator = indicator_group.get_indicator(dt_str, exchange_class, symbol)
                if indicator is not None:
                    return indicator

        return None

    def _get_indicator_group(self) -> Optional["IndicatorGroup"]:
        """获取 IndicatorGroup（如果可用）"""
        try:
            root = self.root
            if root is not None and hasattr(root, 'indicator_group'):
                return root.indicator_group
        except Exception:
            pass
        return None

    def _get_exchange_info(self) -> tuple[Optional[str], Optional[str]]:
        """获取 exchange_class 和 symbol"""
        # 从 parent（TradingPairDataSource）获取
        if hasattr(self, 'parent') and self.parent is not None:
            parent = self.parent
            exchange_class = getattr(parent, '_exchange_class', None)
            symbol = getattr(parent, '_symbol', None)
            if exchange_class and symbol:
                return exchange_class, symbol
        return None, None

    def get_value(self) -> Optional[T]:
        """
        获取指标值（刷新访问时间）

        调用此方法会刷新访问时间，防止指标自动 stop。
        """
        self.request_access()
        return self._value

    @abstractmethod
    async def _update_value(self) -> None:
        """
        更新指标值（子类实现）

        在此方法中：
        1. 通过 get_datasource() 获取依赖的数据源
        2. 从数据源获取数据
        3. 计算并设置 self._value
        """
        pass

    async def on_tick(self) -> bool:
        """
        定时回调

        1. 检查是否应该自动 stop
        2. 查询依赖的数据源（保持其活跃）
        3. 更新指标值
        """
        # 检查是否应该自动 stop
        if self.should_auto_stop():
            self.logger.debug("Auto stop indicator: %s", self.name)
            return True  # 信号完成，进入 STOPPED

        # 只有收到过访问请求才计算
        if self._last_access == 0:
            return False  # 继续等待

        # 查询依赖的数据源（保持活跃）
        for dt in self.depends_on:
            ds = self.get_datasource(dt)
            if ds is None:
                dt_str = _normalize_data_type(dt)
                self.logger.warning("Datasource not available: %s", dt_str)
                return False

        # 更新指标值
        try:
            await self._update_value()
        except Exception as e:
            self.logger.exception("Error updating indicator: %s", e)

        return False

    @property
    def log_state_dict(self) -> dict:
        return {
            "value": self._value,
            "last_access": self._last_access,
            "depends_on": [_normalize_data_type(dt) for dt in self.depends_on],
        }


class VWAPIndicator(LazyIndicator[float]):
    """
    成交量加权平均价指标 (Volume Weighted Average Price)

    计算公式：VWAP = Σ(Price × Volume) / Σ(Volume)

    依赖：TradesDataSource
    """
    depends_on = ["trades"]

    def __init__(
        self,
        window: int = 100,
        name: Optional[str] = None,
        interval: float = 1.0,
    ):
        super().__init__(name=name, interval=interval)
        self._window = window

    async def _update_value(self) -> None:
        trades_ds = self.get_datasource("trades")
        if trades_ds is None:
            return

        trades = trades_ds.get_last_n(self._window)
        if not trades:
            return

        total_volume = 0.0
        total_value = 0.0

        for trade in trades:
            price = getattr(trade, 'price', None)
            amount = getattr(trade, 'amount', None)
            if price is None or amount is None:
                continue
            total_volume += amount
            total_value += price * amount

        if total_volume == 0:
            return

        self._value = total_value / total_volume


class SpreadIndicator(LazyIndicator[float]):
    """
    买卖价差指标 (Bid-Ask Spread)

    计算公式：Spread = (Ask - Bid) / Bid

    依赖：OrderBookDataSource
    """
    depends_on = ["order_book"]

    async def _update_value(self) -> None:
        ob_ds = self.get_datasource("order_book")
        if ob_ds is None:
            return

        ob = ob_ds.get_latest()
        if ob is None:
            return

        bids = getattr(ob, 'bids', None)
        asks = getattr(ob, 'asks', None)

        if not bids or not asks:
            return

        # bids/asks 格式: [[price, amount], ...]
        best_bid = bids[0][0] if isinstance(bids[0], (list, tuple)) else bids[0]
        best_ask = asks[0][0] if isinstance(asks[0], (list, tuple)) else asks[0]

        if best_bid == 0:
            return

        self._value = (best_ask - best_bid) / best_bid


class MidPriceIndicator(LazyIndicator[float]):
    """
    中间价指标 (Mid Price)

    计算公式：MidPrice = (Ask + Bid) / 2

    依赖：OrderBookDataSource
    """
    depends_on = ["order_book"]

    async def _update_value(self) -> None:
        ob_ds = self.get_datasource("order_book")
        if ob_ds is None:
            return

        ob = ob_ds.get_latest()
        if ob is None:
            return

        bids = getattr(ob, 'bids', None)
        asks = getattr(ob, 'asks', None)

        if not bids or not asks:
            return

        best_bid = bids[0][0] if isinstance(bids[0], (list, tuple)) else bids[0]
        best_ask = asks[0][0] if isinstance(asks[0], (list, tuple)) else asks[0]

        self._value = (best_ask + best_bid) / 2

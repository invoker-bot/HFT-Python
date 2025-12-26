"""
Indicator 指标基类

监听 DataSource 的更新事件，发出自己的 update 事件
支持指标链式调用（上层指标监听下层指标）
"""
from abc import abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Any, Generic, TypeVar, TYPE_CHECKING
from ..core.listener import Listener

if TYPE_CHECKING:
    from ..datasource.base import BaseDataSource


T = TypeVar('T')  # 数据类型
R = TypeVar('R')  # 结果类型


@dataclass
class IndicatorResult:
    """
    指标计算结果

    Attributes:
        ready: 是否有足够数据计算
        value: 指标值
        bias: 方向偏差 -1.0 (看空) ~ +1.0 (看多)
        confidence: 置信度 0.0 ~ 1.0
        timestamp: 计算时间
        raw_values: 原始计算值
    """
    ready: bool = False
    value: float = 0.0
    bias: float = 0.0                           # -1.0 ~ 1.0
    confidence: float = 0.0                     # 0.0 ~ 1.0
    timestamp: datetime = field(default_factory=datetime.now)
    raw_values: dict[str, Any] = field(default_factory=dict)

    def is_bullish(self, threshold: float = 0.3) -> bool:
        """是否看多"""
        return self.ready and self.bias >= threshold

    def is_bearish(self, threshold: float = 0.3) -> bool:
        """是否看空"""
        return self.ready and self.bias <= -threshold

    def is_neutral(self) -> bool:
        """是否中性"""
        return self.ready and abs(self.bias) < 0.3

    def is_strong(self, confidence_threshold: float = 0.7) -> bool:
        """是否强信号"""
        return self.ready and self.confidence >= confidence_threshold


class BaseIndicator(Listener, Generic[T, R]):
    """
    指标基类

    特性：
    1. 监听 DataSource 的 update 事件
    2. 数据更新时自动计算
    3. 发出自己的 update 事件供上层指标使用
    4. 支持历史数据回溯
    """

    def __init__(
        self,
        name: str,
        datasource: "BaseDataSource[T]",
        period: int = 14,
        interval: float = 0.0,  # 指标不需要 tick，由事件驱动
    ):
        super().__init__(name=name, interval=interval)
        self._datasource = datasource
        self._period = period
        self._last_result: Optional[R] = None
        self._history: list[R] = []
        self._max_history = 1000

        # 监听数据源更新
        self._datasource.on("update", self._on_datasource_update)

    @property
    def datasource(self) -> "BaseDataSource[T]":
        return self._datasource

    @property
    def period(self) -> int:
        return self._period

    @property
    def last_result(self) -> Optional[R]:
        return self._last_result

    @property
    def history(self) -> list[R]:
        return self._history

    @property
    def is_ready(self) -> bool:
        """是否有足够数据计算"""
        return self._datasource.cache_size >= self._period

    def _on_datasource_update(self, data: T) -> None:
        """数据源更新回调"""
        if not self.is_ready:
            return

        try:
            result = self.calculate()
            if result is not None:
                self._last_result = result
                self._history.append(result)
                if len(self._history) > self._max_history:
                    self._history = self._history[-self._max_history:]

                # 发出更新事件
                self.emit("update", result)
        except Exception as e:
            self.emit("error", {"indicator": self.name, "error": str(e)})

    @abstractmethod
    def calculate(self) -> Optional[R]:
        """
        计算指标

        子类实现，返回计算结果
        """
        ...

    async def tick_callback(self) -> bool:
        """指标由事件驱动，不需要 tick"""
        return True


class SimpleIndicator(BaseIndicator[T, IndicatorResult]):
    """
    简单指标基类

    返回标准 IndicatorResult
    """

    @abstractmethod
    def calculate(self) -> Optional[IndicatorResult]:
        ...


class ChainedIndicator(Listener):
    """
    链式指标

    监听另一个指标的 update 事件
    """

    def __init__(
        self,
        name: str,
        source_indicator: BaseIndicator,
        period: int = 14,
    ):
        super().__init__(name=name, interval=0.0)
        self._source = source_indicator
        self._period = period
        self._last_result: Optional[IndicatorResult] = None

        # 监听源指标更新
        self._source.on("update", self._on_source_update)

    @property
    def source(self) -> BaseIndicator:
        return self._source

    @property
    def period(self) -> int:
        return self._period

    @property
    def last_result(self) -> Optional[IndicatorResult]:
        return self._last_result

    @property
    def is_ready(self) -> bool:
        return len(self._source.history) >= self._period

    def _on_source_update(self, result: Any) -> None:
        """源指标更新回调"""
        if not self.is_ready:
            return

        try:
            new_result = self.calculate()
            if new_result is not None:
                self._last_result = new_result
                self.emit("update", new_result)
        except Exception as e:
            self.emit("error", {"indicator": self.name, "error": str(e)})

    @abstractmethod
    def calculate(self) -> Optional[IndicatorResult]:
        """计算指标"""
        ...

    async def tick_callback(self) -> bool:
        return True

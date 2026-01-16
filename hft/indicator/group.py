"""
IndicatorGroup - 指标管理器

Feature 0006: Indicator 与 DataSource 统一架构

层级结构：
- IndicatorGroup: 顶层管理器
  ├── GlobalIndicators: 全局指标容器
  │   └── GlobalFundingRateDataSource, ...
  └── LocalIndicators: 交易对级指标容器
      └── (exchange_class, symbol) -> TradingPairIndicators
          └── TradesDataSource, OrderBookDataSource, MedalEdgeIndicator, ...

设计理念：
- 统一的 query_indicator / get_indicator 接口
- lazy 创建：首次访问时创建
- 自动启动：STOPPED 状态自动 start
- touch 更新：防止过期停止
- ready 检查：根据 ready_condition 判断
"""
import time
import asyncio
from collections import defaultdict
from typing import Any, Optional, Type, TYPE_CHECKING

from ..core.listener import Listener, GroupListener, ListenerState
from .base import BaseIndicator, GlobalIndicator, T

if TYPE_CHECKING:
    from ..core.app.core import AppCore


class TradingPairIndicators(GroupListener):
    """
    交易对级指标容器

    管理特定 (exchange_class, symbol) 的所有指标。

    注意：不使用 lazy_start，创建后由 IndicatorGroup 显式启动。
    """
    __pickle_exclude__ = (*GroupListener.__pickle_exclude__,)

    def __init__(
        self,
        exchange_class: str,
        symbol: str,
    ):
        name = f"{exchange_class}:{symbol}"
        super().__init__(name=name, interval=60.0)
        self._exchange_class = exchange_class
        self._symbol = symbol
        # indicator_id -> BaseIndicator
        self._indicators: dict[str, BaseIndicator] = {}

    @property
    def exchange_class(self) -> str:
        return self._exchange_class

    @property
    def symbol(self) -> str:
        return self._symbol

    # ============================================================
    # 指标管理
    # ============================================================

    def get_indicator(self, indicator_id: str) -> Optional[BaseIndicator]:
        """
        获取指标实例（不管 ready 与否）

        用于订阅事件、访问数据、调试观测。
        """
        return self._indicators.get(indicator_id)

    def register_indicator(self, indicator_id: str, indicator: BaseIndicator) -> None:
        """注册指标"""
        self._indicators[indicator_id] = indicator
        self.add_child(indicator)

    def has_indicator(self, indicator_id: str) -> bool:
        """检查指标是否存在"""
        return indicator_id in self._indicators

    # ============================================================
    # GroupListener 接口
    # ============================================================

    def sync_children_params(self) -> dict[str, Any]:
        """返回已注册的指标"""
        return {
            indicator_id: {"indicator": indicator}
            for indicator_id, indicator in self._indicators.items()
        }

    def create_dynamic_child(self, name: str, param: Any) -> Listener:
        """不需要动态创建，指标通过 register_indicator 注册"""
        return param["indicator"]

    # ============================================================
    # 生命周期
    # ============================================================

    async def on_tick(self) -> bool:
        """检查并停止过期的指标"""
        for indicator_id, indicator in list(self._indicators.items()):
            if indicator.is_expired() and indicator.state == ListenerState.RUNNING:
                await indicator.stop()
                self.logger.debug(
                    "Stopped expired indicator: %s/%s",
                    self.name, indicator_id
                )
        return False

    @property
    def log_state_dict(self) -> dict:
        active = [
            ind_id for ind_id, ind in self._indicators.items()
            if ind.state == ListenerState.RUNNING
        ]
        return {
            "exchange_class": self._exchange_class,
            "symbol": self._symbol,
            "total_indicators": len(self._indicators),
            "active_indicators": active,
        }


class GlobalIndicators(GroupListener):
    """
    全局指标容器

    管理不绑定特定交易对的全局指标。
    """
    __pickle_exclude__ = (*GroupListener.__pickle_exclude__,)

    def __init__(self):
        super().__init__(name="GlobalIndicators", interval=60.0)
        # indicator_id -> GlobalIndicator
        self._indicators: dict[str, GlobalIndicator] = {}

    # ============================================================
    # 指标管理
    # ============================================================

    def get_indicator(self, indicator_id: str) -> Optional[GlobalIndicator]:
        """获取全局指标实例"""
        return self._indicators.get(indicator_id)

    def register_indicator(
        self,
        indicator_id: str,
        indicator: GlobalIndicator
    ) -> None:
        """注册全局指标"""
        self._indicators[indicator_id] = indicator
        self.add_child(indicator)

    def has_indicator(self, indicator_id: str) -> bool:
        """检查指标是否存在"""
        return indicator_id in self._indicators

    # ============================================================
    # GroupListener 接口
    # ============================================================

    def sync_children_params(self) -> dict[str, Any]:
        return {
            indicator_id: {"indicator": indicator}
            for indicator_id, indicator in self._indicators.items()
        }

    def create_dynamic_child(self, name: str, param: Any) -> Listener:
        return param["indicator"]

    # ============================================================
    # 生命周期
    # ============================================================

    async def on_tick(self) -> bool:
        """检查并停止过期的指标"""
        for indicator_id, indicator in list(self._indicators.items()):
            if indicator.is_expired() and indicator.state == ListenerState.RUNNING:
                await indicator.stop()
                self.logger.debug("Stopped expired global indicator: %s", indicator_id)
        return False

    @property
    def log_state_dict(self) -> dict:
        active = [
            ind_id for ind_id, ind in self._indicators.items()
            if ind.state == ListenerState.RUNNING
        ]
        return {
            "total_indicators": len(self._indicators),
            "active_indicators": active,
        }


class IndicatorGroup(GroupListener):
    """
    指标管理器 - 顶层

    Feature 0006 的核心组件，提供统一的 query_indicator / get_indicator 接口。

    层级结构：
    - GlobalIndicators: 全局指标
    - LocalIndicators: (exchange_class, symbol) -> TradingPairIndicators

    使用示例：
        # 获取指标（ready 时返回，否则返回 None）
        indicator = indicator_group.query_indicator("rsi", "okx", "BTC/USDT:USDT")
        if indicator:
            vars = indicator.calculate_vars(direction=1)

        # 获取指标实例（不管 ready 与否，用于订阅事件）
        indicator = indicator_group.get_indicator("rsi", "okx", "BTC/USDT:USDT")
        indicator.on("update", lambda ts, val: print(f"New: {val}"))
    """

    def __init__(self):
        super().__init__(name="IndicatorGroup", interval=60.0)
        # 全局指标容器
        self._global_indicators = GlobalIndicators()
        # 交易对级指标容器: (exchange_class, symbol) -> TradingPairIndicators
        self._local_indicators: dict[tuple[str, str], TradingPairIndicators] = {}
        # 指标工厂注册表: indicator_id -> factory_func
        self._indicator_factories: dict[str, Any] = {}
        # 注册默认的 DataSource 工厂（字符串 ID 映射）
        self._register_default_factories()

    def _register_default_factories(self) -> None:
        """
        注册默认的 DataSource 工厂

        使用字符串 ID（如 "ticker", "trades"）映射到对应的 DataSource 类。
        这样 LazyIndicator 可以通过 get_indicator("ticker", ...) 获取数据源。
        """
        from .factory import IndicatorFactory

        # 字符串 ID -> DataSource 类名映射
        default_mappings = {
            "ticker": "TickerDataSource",
            "trades": "TradesDataSource",
            "order_book": "OrderBookDataSource",
            "ohlcv": "OHLCVDataSource",
        }

        for indicator_id, class_name in default_mappings.items():
            # 只注册尚未存在的工厂（允许配置覆盖默认）
            if indicator_id not in self._indicator_factories:
                factory = IndicatorFactory(class_name, {})
                self._indicator_factories[indicator_id] = factory

    # ============================================================
    # 生命周期
    # ============================================================

    async def on_start(self) -> None:
        """启动时初始化"""
        # NOTE: GlobalIndicators 已通过 sync_children_params() 纳入同步，
        # 不需要在此处 add_child()，避免重复注册到 _class_index
        await super().on_start()

    # ============================================================
    # 指标工厂注册
    # ============================================================

    def register_factory(
        self,
        indicator_id: str,
        factory: Any,
    ) -> None:
        """
        注册指标工厂

        Args:
            indicator_id: 指标 ID
            factory: 工厂函数或类，签名为 (exchange_class, symbol, **params) -> BaseIndicator
        """
        self._indicator_factories[indicator_id] = factory

    # ============================================================
    # 查询接口
    # ============================================================

    def get_indicator(
        self,
        indicator_id: str,
        exchange_class: Optional[str],
        symbol: Optional[str],
    ) -> Optional[BaseIndicator]:
        """
        获取 indicator 实例（不管 ready 与否）

        行为：lazy 创建、自动启动、touch 更新。
        用途：订阅 update/ready 事件、访问 _data、调试/观测。

        Args:
            indicator_id: 指标 ID
            exchange_class: 交易所类名，GlobalIndicator 传 None
            symbol: 交易对，GlobalIndicator 传 None

        Returns:
            BaseIndicator 实例，如果无法创建则返回 None
        """
        # 全局指标
        if exchange_class is None and symbol is None:
            indicator = self._global_indicators.get_indicator(indicator_id)
            if indicator is not None:
                indicator.touch()
                self._ensure_started(indicator)
                return indicator
            # 尝试创建
            indicator = self._create_indicator(indicator_id, None, None)
            if indicator is not None:
                self._global_indicators.register_indicator(indicator_id, indicator)
                self._ensure_started(indicator)
            return indicator

        # 交易对级指标
        key = (exchange_class, symbol)
        pair_indicators = self._local_indicators.get(key)

        if pair_indicators is None:
            # 创建 TradingPairIndicators
            pair_indicators = TradingPairIndicators(exchange_class, symbol)
            self._local_indicators[key] = pair_indicators
            self.add_child(pair_indicators)
            # 确保容器启动（用于过期检查的 on_tick）
            self._ensure_started(pair_indicators)

        indicator = pair_indicators.get_indicator(indicator_id)
        if indicator is not None:
            indicator.touch()
            self._ensure_started(indicator)
            return indicator

        # 尝试创建
        indicator = self._create_indicator(indicator_id, exchange_class, symbol)
        if indicator is not None:
            pair_indicators.register_indicator(indicator_id, indicator)
            self._ensure_started(indicator)
        return indicator

    def query_indicator(
        self,
        indicator_id: str,
        exchange_class: Optional[str],
        symbol: Optional[str],
    ) -> Optional[BaseIndicator]:
        """
        查询 indicator，支持 lazy 创建和自动启动

        Args:
            indicator_id: 指标 ID
            exchange_class: 交易所类名，GlobalIndicator 传 None
            symbol: 交易对，GlobalIndicator 传 None

        Returns:
            - BaseIndicator 实例：indicator ready
            - None：indicator 未 ready（实例仍可通过 get_indicator() 获取）
        """
        indicator = self.get_indicator(indicator_id, exchange_class, symbol)
        if indicator is None:
            return None
        return indicator if indicator.is_ready() else None

    # ============================================================
    # 内部方法
    # ============================================================

    def _create_indicator(
        self,
        indicator_id: str,
        exchange_class: Optional[str],
        symbol: Optional[str],
    ) -> Optional[BaseIndicator]:
        """
        创建指标实例

        使用注册的工厂函数创建指标。
        """
        factory = self._indicator_factories.get(indicator_id)
        if factory is None:
            self.logger.warning("No factory registered for indicator: %s", indicator_id)
            return None

        try:
            indicator = factory(exchange_class, symbol)
            return indicator
        except Exception as e:
            self.logger.exception(
                "Failed to create indicator %s: %s",
                indicator_id, e
            )
            return None

    def _ensure_started(self, listener: Listener) -> None:
        """确保 listener 已启动"""
        if listener.state == ListenerState.STOPPED:
            # 先检查是否有 running loop，避免协程泄漏
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # 没有运行的事件循环，跳过自动启动
                # 调用方需要手动启动或在事件循环中调用
                return
            loop.create_task(listener.start())

    # ============================================================
    # GroupListener 接口
    # ============================================================

    def sync_children_params(self) -> dict[str, Any]:
        """返回所有子节点（包括 GlobalIndicators 和 TradingPairIndicators）"""
        params = {
            # 静态子节点：GlobalIndicators
            "GlobalIndicators": {"static": self._global_indicators},
        }
        # 动态子节点：TradingPairIndicators
        for (exchange_class, symbol), pair_indicators in self._local_indicators.items():
            name = f"{exchange_class}:{symbol}"
            params[name] = {"pair_indicators": pair_indicators}
        return params

    def create_dynamic_child(self, name: str, param: Any) -> Listener:
        # 静态子节点直接返回
        if "static" in param:
            return param["static"]
        # 动态子节点
        return param["pair_indicators"]

    # ============================================================
    # 统计
    # ============================================================

    @property
    def log_state_dict(self) -> dict:
        stats = defaultdict(int)
        for (exchange_class, _), pair_indicators in self._local_indicators.items():
            stats[exchange_class] += len(pair_indicators._indicators)

        return {
            "global_indicators": len(self._global_indicators._indicators),
            "trading_pairs": len(self._local_indicators),
            "by_exchange": dict(stats),
        }

    def get_stats(self) -> dict:
        """获取详细统计信息"""
        stats = {
            "global_indicators": {
                "total": len(self._global_indicators._indicators),
                "active": sum(
                    1 for ind in self._global_indicators._indicators.values()
                    if ind.state == ListenerState.RUNNING
                ),
            },
            "local_indicators": {
                "trading_pairs": len(self._local_indicators),
                "by_exchange": defaultdict(lambda: {"total": 0, "active": 0}),
            },
        }

        for (exchange_class, _), pair_indicators in self._local_indicators.items():
            total = len(pair_indicators._indicators)
            active = sum(
                1 for ind in pair_indicators._indicators.values()
                if ind.state == ListenerState.RUNNING
            )
            stats["local_indicators"]["by_exchange"][exchange_class]["total"] += total
            stats["local_indicators"]["by_exchange"][exchange_class]["active"] += active

        stats["local_indicators"]["by_exchange"] = dict(
            stats["local_indicators"]["by_exchange"]
        )
        return stats

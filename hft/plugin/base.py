"""
HFT 插件系统

基于 pluggy 实现的插件架构，支持：
- 生命周期钩子 (on_app_start, on_app_stop, ...)
- 交易钩子 (on_order_creating, on_order_created, ...)
- 数据钩子 (on_ticker_update, on_balance_update, ...)
- 通知钩子 (on_notify, on_health_check_failed, ...)

详见 docs/plugin.md

TODO: 动态加载插件（从指定目录或 entry_points），插件配置管理，插件间通信机制。
"""
from typing import TYPE_CHECKING

import pluggy

from .._version import __appname__

if TYPE_CHECKING:
    from ..core.app.base import AppCore
    from ..core.listener import Listener
    from ..exchange.base import BaseExchange
    from ..executor.base import BaseExecutor
    from ..strategy.base import BaseStrategy, TargetPositions

# pluggy markers
hookspec = pluggy.HookspecMarker(__appname__)
hookimpl = pluggy.HookimplMarker(__appname__)


class HookSpec:
    """
    HFT 钩子规范

    定义所有可用的插件钩子点。插件通过 @hookimpl 装饰器实现这些方法。
    """

    # ========== 生命周期 Hooks ==========

    @hookspec
    async def on_app_start(self, app: "AppCore"):
        """
        应用启动时调用

        Args:
            app: AppCore 实例
        """

    @hookspec
    async def on_app_stop(self, app: "AppCore"):
        """
        应用停止时调用

        Args:
            app: AppCore 实例
        """

    @hookspec
    async def on_app_tick(self, app: "AppCore"):
        """
        每个 tick 循环调用

        Args:
            app: AppCore 实例
        """

    @hookspec
    async def on_listener_start(self, listener: "Listener"):
        """
        任何 Listener 启动时调用

        Args:
            listener: Listener 实例
        """

    @hookspec
    async def on_listener_stop(self, listener: "Listener"):
        """
        任何 Listener 停止时调用

        Args:
            listener: Listener 实例
        """

    @hookspec
    async def on_listener_tick(self, listener: "Listener"):
        """
        任何 Listener 每个 tick 循环调用

        Args:
            listener: Listener 实例
        """
    # ========== 交易 Hooks ==========

    # @hookspec(firstresult=True)
    # def on_order_creating(
    #     self,
    #     exchange: "BaseExchange",
    #     symbol: str,
    #     side: str,
    #     amount: float,
    #     price: float
    # ) -> bool:
    #     """
    #     订单创建前调用
#
    #     使用 firstresult=True，任何插件返回 False 将阻止订单创建。
#
    #     Args:
    #         exchange: 交易所实例
    #         symbol: 交易对
    #         side: 方向 ("buy" / "sell")
    #         amount: 数量
    #         price: 价格 (市价单为 None)
#
    #     Returns:
    #         True 允许创建，False 阻止创建
    #     """
#
    # @hookspec
    # def on_order_created(self, exchange: "BaseExchange", order: dict):
    #     """
    #     订单创建成功后调用
#
    #     Args:
    #         exchange: 交易所实例
    #         order: 订单信息 (ccxt 格式)
    #     """
#
    # @hookspec
    # def on_order_filled(self, exchange: "BaseExchange", order: dict):
    #     """
    #     订单成交后调用
#
    #     Args:
    #         exchange: 交易所实例
    #         order: 订单信息 (ccxt 格式)
    #     """
#
    # @hookspec
    # def on_order_cancelled(self, exchange: "BaseExchange", order: dict):
    #     """
    #     订单取消后调用
#
    #     Args:
    #         exchange: 交易所实例
    #         order: 订单信息 (ccxt 格式)
    #     """
#
    # @hookspec
    # def on_order_error(
    #     self,
    #     exchange: "BaseExchange",
    #     error: Exception,
    #     order_params: dict
    # ):
    #     """
    #     订单创建失败时调用
#
    #     Args:
    #         exchange: 交易所实例
    #         error: 异常对象
    #         order_params: 订单参数
    #     """
#
    # # ========== 策略 Hooks ==========
#
    # @hookspec
    # def on_strategy_targets_calculated(
    #     self,
    #     strategy: "BaseStrategy",
    #     targets: "TargetPositions"
    # ):
    #     """
    #     策略计算出目标仓位后调用
#
    #     Args:
    #         strategy: 策略实例
    #         targets: 目标仓位
    #     """
#
    # @hookspec
    # def on_targets_aggregated(
    #     self,
    #     strategy_group: "StrategyGroup",
    #     targets: "AggregatedTargets"
    # ):
    #     """
    #     策略组聚合目标后调用
#
    #     Args:
    #         strategy_group: 策略组实例
    #         targets: 聚合后的目标仓位
    #     """
#
    # @hookspec
    # def on_execution_start(
    #     self,
    #     executor: "BaseExecutor",
    #     targets: "AggregatedTargets"
    # ):
    #     """
    #     执行器开始执行前调用
#
    #     Args:
    #         executor: 执行器实例
    #         targets: 聚合后的目标仓位
    #     """
#
    # @hookspec
    # def on_execution_complete(
    #     self,
    #     executor: "BaseExecutor",
    #     results: list
    # ):
    #     """
    #     执行器执行完成后调用
#
    #     Args:
    #         executor: 执行器实例
    #         results: 执行结果列表
    #     """
#
    # # ========== 数据 Hooks ==========
#
    # @hookspec
    # def on_ticker_update(
    #     self,
    #     exchange: "BaseExchange",
    #     symbol: str,
    #     ticker: dict
    # ):
    #     """
    #     Ticker 更新时调用
#
    #     Args:
    #         exchange: 交易所实例
    #         symbol: 交易对
    #         ticker: Ticker 数据
    #     """
#
    # @hookspec
    # def on_balance_update(self, exchange: "BaseExchange", account: str, balance: dict):
    #     """
    #     余额更新时调用
#
    #     Args:
    #         exchange: 交易所实例
    #         balance: 余额数据
    #     """
#
    # @hookspec
    # def on_position_update(self, exchange: "BaseExchange", account: str, positions: dict):
    #     """
    #     持仓更新时调用
#
    #     Args:
    #         exchange: 交易所实例
    #         account: 账户名称
    #         positions: 持仓数据
    #     """
#
    # @hookspec
    # def on_funding_rate_update(
    #     self,
    #     exchange: "BaseExchange",
    #     symbol: str,
    #     funding_rate: dict
    # ):
    #     """
    #     资金费率更新时调用
#
    #     Args:
    #         exchange: 交易所实例
    #         symbol: 交易对
    #         funding_rate: 资金费率数据
    #     """

    # ========== 通知 Hooks ==========

    @hookspec
    def on_notify(self, level: str, title: str, message: str):
        """
        发送通知时调用

        Args:
            level: 级别 ("info", "warning", "error")
            title: 通知标题
            message: 通知内容
        """

    @hookspec
    def on_health_check_failed(self, listener: "Listener", error: Exception):
        """
        健康检查失败时调用

        Args:
            listener: Listener 实例
            error: 异常对象
        """


class PluginBase:
    """
    插件基类

    所有插件应继承此类，并使用 @hookimpl 装饰器实现需要的钩子。

    Example:
        class MyPlugin(PluginBase):
            name = "my_plugin"

            @hookimpl
            def on_order_created(self, exchange, order):
                print(f"Order created: {order}")
    """

    # 插件名称，用于配置引用和日志
    name: str = "base_plugin"

    def __init__(self):
        """
        初始化插件

        Args:
            config: 插件配置字典
        """
        self.config = {}


# 创建 PluginManager 实例
pm = pluggy.PluginManager(__appname__)
pm.add_hookspecs(HookSpec)

# 从 setuptools entry_points 加载插件
pm.load_setuptools_entrypoints(__appname__)

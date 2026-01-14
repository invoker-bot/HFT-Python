"""
SmartExecutor 配置模块
"""
from functools import cached_property
from typing import ClassVar, Type

from pydantic import Field

from ..base_config import BaseExecutorConfig
from ..base import BaseExecutor


class SmartExecutorConfig(BaseExecutorConfig):
    """
    SmartExecutor 智能路由执行器配置

    自动选择最优子执行器：
    - 显式路由：exchange.config.executor_map[symbol] 优先
    - 速度阈值：speed > speed_threshold 使用 market
    - 自动选择：基于 trades 数据计算 taker 优势，选 market 或 as
    - 默认回退：数据不足时使用 default_executor

    Example config:
        class_name: smart
        interval: 1.0
        speed_threshold: 0.9
        trades_window_seconds: 300
        min_trades: 50
        default_executor: as
        children:
          market: market/default
          limit: limit/maker
          as: avellaneda_stoikov/default
          pca: pca/default

    Attributes:
        speed_threshold: 速度阈值，超过此值使用 market 执行器
        trades_window_seconds: trades 分析时间窗口（秒）
        min_trades: 最少成交笔数，低于此值回退默认
        default_executor: 默认执行器 key
        children: 子执行器配置路径映射 {key: config_path}
    """

    class_name: ClassVar[str] = "smart"

    speed_threshold: float = Field(0.9, description="速度阈值，超过使用 market")
    trades_window_seconds: float = Field(300.0, description="trades 分析窗口（秒）")
    min_trades: int = Field(50, description="最少成交笔数")
    default_executor: str = Field("as", description="默认执行器 key")
    children: dict[str, str] = Field(
        default_factory=dict,
        description="子执行器配置路径 {key: config_path}",
    )

    @classmethod
    def get_class_type(cls) -> Type["BaseExecutor"]:
        from .executor import SmartExecutor
        return SmartExecutor

    @cached_property
    def instance(self) -> "BaseExecutor":
        from .executor import SmartExecutor
        return SmartExecutor(config=self)


__all__ = [
    "SmartExecutorConfig",
]


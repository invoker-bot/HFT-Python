"""
SmartExecutor 配置模块
"""
from functools import cached_property
from typing import ClassVar, Type, Optional

from pydantic import BaseModel, Field

from ..base_config import BaseExecutorConfig
from ..base import BaseExecutor


class RouteConfig(BaseModel):
    """
    路由规则配置

    用于配置化路由决策，支持条件表达式。

    Attributes:
        condition: 条件表达式（可选，None 表示无条件匹配）
                  示例："speed > 0.9", "len(trades) > 50 and notional > 10000"
        executor: 目标执行器 key（可选，None 表示不执行 - 阶段 2）
                  与 children 中的 key 对应，或为 None 表示取消现有订单
        priority: 规则优先级（数字越小优先级越高，默认 0）

    条件表达式可用变量:
        speed: float - 执行紧急度，范围 [0, 1]
        trades: list - 最近的成交记录列表
        edge: float - Taker 优势（相对值），如 0.01 表示 1%
        notional: float - 该方向的成交额（USD）

    条件表达式可用函数:
        len(), abs(), min(), max(), sum(), round()

    Examples:
        >>> RouteConfig(condition="speed > 0.9", executor="market", priority=1)
        >>> RouteConfig(condition="len(trades) > 50", executor="as", priority=2)
        >>> RouteConfig(condition=None, executor="limit", priority=999)  # 默认规则
        >>> RouteConfig(condition="speed < 0.1", executor=None, priority=10)  # 不执行
    """
    condition: Optional[str] = Field(None, description="条件表达式，None 表示无条件")
    executor: Optional[str] = Field(None, description="目标执行器 key（可选，None 表示不执行）")
    priority: int = Field(0, description="规则优先级（越小越高）")


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
        routes:  # 可选，阶段 2+ 使用
          - condition: "speed > 0.9"
            executor: market
            priority: 1
          - condition: "len(trades) > 50 and edge > 0"
            executor: as
            priority: 2

    Attributes:
        speed_threshold: 速度阈值，超过此值使用 market 执行器
        trades_window_seconds: trades 分析时间窗口（秒）
        min_trades: 最少成交笔数，低于此值回退默认
        default_executor: 默认执行器 key
        children: 子执行器配置路径映射 {key: config_path}
        routes: 路由规则列表（可选，阶段 2+ 使用，按 priority 排序）
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
    routes: list[RouteConfig] = Field(
        default_factory=list,
        description="路由规则列表（阶段 2+ 使用，按 priority 排序）",
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
    "RouteConfig",
    "SmartExecutorConfig",
]


"""
执行器配置模块

提供执行器的配置类：
- BaseExecutorConfig: 执行器配置基类
- MarketExecutorConfig: 市价单执行器配置
- LimitExecutorConfig: 限价单执行器配置
"""
from functools import cached_property
from typing import ClassVar, Type, TYPE_CHECKING
from pydantic import BaseModel, Field
from ..config.base import BaseConfig

if TYPE_CHECKING:
    from .base import BaseExecutor
    from .market import MarketExecutor
    from .limit import LimitExecutor


class BaseExecutorConfig(BaseConfig["BaseExecutor"]):
    """
    执行器配置基类

    Attributes:
        interval: Tick 间隔（秒）
        always: 是否总是执行（忽略 delta 阈值检查）
            - False: 只有当 |delta| >= per_order_usd 时才执行（rebalancing 模式）
            - True: 无论 delta 多大都执行（market making 模式）
    """
    class_dir: ClassVar[str] = "conf/executor"

    interval: float = Field(1.0, description="Tick 间隔（秒）")
    always: bool = Field(False, description="是否总是执行（忽略 delta 阈值检查）")

    @classmethod
    def get_class_type(cls) -> Type["BaseExecutor"]:
        from .base import BaseExecutor
        return BaseExecutor


class MarketExecutorConfig(BaseExecutorConfig):
    """
    市价单执行器配置

    Attributes:
        per_order_usd: 单笔订单大小 / 执行阈值（USD）
    """
    class_name: ClassVar[str] = "market"

    per_order_usd: float = Field(100.0, description="单笔订单大小 / 执行阈值（USD）")

    @classmethod
    def get_class_type(cls) -> Type["MarketExecutor"]:
        from .market import MarketExecutor
        return MarketExecutor

    @cached_property
    def instance(self) -> "MarketExecutor":
        from .market import MarketExecutor
        return MarketExecutor(config=self)


class LimitOrderLevel(BaseModel):
    """
    单层限价单配置

    Attributes:
        reverse: 是否反向订单（用于对冲）
        spread: 距离当前价格的百分比（如 0.01 = 1%）
        refresh_tolerance: 刷新容忍度，超过此值才更新订单价格
            - 计算: |new_price - old_price| > refresh_tolerance * spread * old_price
            - 值为 1.0 时类似网格交易
        timeout: 订单超时时间（秒），超时后取消订单
        per_order_usd: 该层订单的 USD 价值
    """
    reverse: bool = Field(False, description="是否反向订单")
    spread: float = Field(description="距离当前价格的百分比")
    refresh_tolerance: float = Field(0.5, description="刷新容忍度")
    timeout: float = Field(60.0, description="订单超时（秒）")
    per_order_usd: float = Field(100.0, description="单笔订单 USD")


class LimitExecutorConfig(BaseExecutorConfig):
    """
    限价单执行器配置

    支持多层订单，每层有独立的 spread, timeout, per_order_usd 配置。

    Example config:
        class_name: limit
        interval: 0.5
        orders:
          - spread: 0.001
            refresh_tolerance: 0.5
            timeout: 30
            per_order_usd: 50
          - spread: 0.003
            refresh_tolerance: 0.5
            timeout: 60
            per_order_usd: 100

    Attributes:
        orders: 多层订单配置列表
    """
    class_name: ClassVar[str] = "limit"

    orders: list[LimitOrderLevel] = Field(
        default_factory=list,
        description="多层订单配置"
    )

    @classmethod
    def get_class_type(cls) -> Type["LimitExecutor"]:
        from .limit import LimitExecutor
        return LimitExecutor

    @cached_property
    def instance(self) -> "LimitExecutor":
        from .limit import LimitExecutor
        return LimitExecutor(config=self)


class ASOrderLevel(BaseModel):
    """
    AS 执行器单级订单配置

    Attributes:
        reverse: 是否为卖单（False=买单，True=卖单）
        gamma: 该级别的风险厌恶系数，越大 spread 越远
        per_order_usd: 该级别的订单大小（USD）
        timeout: 订单超时时间（秒），超时后取消重挂
        refresh_tolerance: 刷新容忍度，价格偏离超过 spread * tolerance 时取消重挂
    """
    reverse: bool = Field(False, description="是否为卖单（False=买单，True=卖单）")
    gamma: float = Field(description="风险厌恶系数")
    per_order_usd: float = Field(100.0, description="订单大小（USD）")
    timeout: float = Field(60.0, description="订单超时（秒）")
    refresh_tolerance: float = Field(0.5, description="刷新容忍度")


class AvellanedaStoikovExecutorConfig(BaseExecutorConfig):
    """
    Avellaneda-Stoikov 做市执行器配置

    基于 Avellaneda-Stoikov 论文的最优做市策略：
    - 支持多级挂单，每级有独立的 gamma 和订单大小
    - 根据当前库存动态调整报价
    - 从成交数据动态估计 k 和 sigma

    核心公式：
        reservation_price = mid_price - inventory * gamma * sigma^2 * (T - t)
        optimal_spread = gamma * sigma^2 * (T - t) + (2/gamma) * ln(1 + gamma/k)

    Example config:
        class_name: avellaneda_stoikov
        interval: 0.5
        orders:
          - gamma: 0.05
            per_order_usd: 50
          - gamma: 0.1
            per_order_usd: 100
          - gamma: 0.2
            per_order_usd: 200
        k_fallback: 1.5
        sigma_fallback: 0.01
        T: 300
        max_inventory: 1000
        min_spread: 0.0005
        max_spread: 0.01
        order_timeout: 30

    Attributes:
        orders: 多级订单配置，每级有独立的 gamma 和 per_order_usd
        k_fallback: k 估计失败时的回退值
        sigma_fallback: sigma 估计失败时的回退值
        T: 时间窗口（秒），策略优化的时间范围
        max_inventory: 最大允许库存（USD），超过后停止该方向挂单
        min_spread: 最小价差下限
        max_spread: 最大价差上限
        order_timeout: 订单超时时间（秒）
        intensity_*: 强度计算器参数
    """
    class_name: ClassVar[str] = "avellaneda_stoikov"

    # 多级订单配置
    orders: list[ASOrderLevel] = Field(
        default_factory=lambda: [ASOrderLevel(gamma=0.1, per_order_usd=100.0)],
        description="多级订单配置"
    )

    # AS 模型参数
    k_fallback: float = Field(1.5, description="k 估计失败时的回退值")
    sigma_fallback: float = Field(0.01, description="sigma 估计失败时的回退值")
    T: float = Field(300.0, description="时间窗口（秒）")

    # 订单参数
    max_inventory: float = Field(1000.0, description="最大库存（USD）")
    min_spread: float = Field(0.0005, description="最小价差")
    max_spread: float = Field(0.01, description="最大价差")
    cancel_delay: float = Field(5.0, description="取消延迟（秒），订单未被认领超过此时间才取消")

    # 强度计算器参数
    intensity_sub_range: float = Field(15.0, description="子区间长度（秒）")
    intensity_total_range: float = Field(600.0, description="总分析时间范围（秒）")
    intensity_precision: int = Field(20, description="价格分桶精度")
    intensity_std_range: float = Field(2.0, description="价格范围（标准差倍数）")
    intensity_min_correlation: float = Field(0.5, description="最小相关系数")
    intensity_min_trades: int = Field(50, description="最少成交笔数")

    # 加权中间价参数
    mid_levels: int = Field(10, description="订单簿深度档位数")
    mid_decay: float = Field(0.9, description="深度衰减系数")
    mid_adjustment_factor: float = Field(0.5, description="中间价调整系数 [0, 1]，0=不调整，1=全部使用加权偏离")

    @classmethod
    def get_class_type(cls) -> Type["BaseExecutor"]:
        from .avellaneda_stoikov import AvellanedaStoikovExecutor
        return AvellanedaStoikovExecutor

    @cached_property
    def instance(self) -> "BaseExecutor":
        from .avellaneda_stoikov import AvellanedaStoikovExecutor
        return AvellanedaStoikovExecutor(config=self)

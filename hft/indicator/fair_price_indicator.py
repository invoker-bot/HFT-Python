"""
FairPriceIndicator - 公平价格指标

用于 MarketNeutralPositions 策略，返回交易对的公平价格（mid_price）。
"""
from typing import Optional, Dict, Any
from .base import BaseIndicator


class FairPriceIndicator(BaseIndicator[float]):
    """
    公平价格指标

    特性：
    - 返回 mid_price 作为公平价格
    - 支持返回 None（mask 机制，当价格数据不可用时）
    - 注入到 trading_pair_class scope

    注意：
    - 标准化（最小价格 = 1.0）在 Strategy 层完成
    - 本 Indicator 只负责返回原始价格
    """

    def __init__(
        self,
        name: str = "fair_price",
        ready_condition: Optional[str] = None,
    ):
        super().__init__(
            name=name,
            window=None,  # 不需要历史数据
            ready_condition=ready_condition,
            expire_seconds=10.0,  # 10 秒过期
            interval=None,  # 事件驱动
        )

    def calculate_vars(self, direction: Optional[str] = None) -> Dict[str, Any]:
        """
        计算变量

        Args:
            direction: 方向（未使用）

        Returns:
            变量字典：{"trading_pair_std_price": mid_price or None}
        """
        # FairPriceIndicator 依赖 TickerDataSource 注入的 mid_price
        # 它的作用是将 mid_price 映射为 trading_pair_std_price
        # 实际的标准化在 Strategy 层的 trading_pair_class_group scope 中完成

        # 注意：这里需要从当前 Scope 获取 mid_price
        # 但 BaseIndicator 目前没有提供访问 Scope 的接口
        # 所以这是一个简化实现，返回 None 表示需要配置层面的支持

        # TODO: 需要 BaseIndicator 提供访问 Scope 的接口
        # 或者在配置中直接定义：trading_pair_std_price = mid_price

        return {"trading_pair_std_price": None}




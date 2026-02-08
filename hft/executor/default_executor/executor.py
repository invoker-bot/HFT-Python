"""
MarketExecutor - 市价单执行器

Feature 0005: 支持动态参数（表达式或字面量）

执行流程：
1. BaseExecutor 调用 execute_delta() 传入差值（USD）
2. 将 USD 差值转换为交易数量
3. 执行市价单
"""
from typing import TYPE_CHECKING

from ..base import BaseExecutor

if TYPE_CHECKING:
    from ...exchange.base import BaseExchange


class DefaultExecutor(BaseExecutor):
    """
    市价单执行器

    Feature 0005: 支持 condition 和动态 per_order_usd
    """


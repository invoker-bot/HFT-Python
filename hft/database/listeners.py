"""
数据模型和监听器模块

.. deprecated::
    此模块已迁移到 hft.indicator.persist，请使用新路径导入。
    将在后续版本中删除。

监听器类型：
- DataListener: 数据采集监听器基类
- ExchangeFundingRateBillListener: 资金费率账单采集
- ExchangeBalanceUsdListener: 账户余额快照采集
- FundingRatePersistListener: 资金费率快照持久化
"""
# DEPRECATED: 重新导出以保持向后兼容
from ..indicator.persist import (
    DataListener,
    ExchangeFundingRateBillListener,
    ExchangeBalanceUsdListener,
    FundingRatePersistListener,
)

__all__ = [
    "DataListener",
    "ExchangeFundingRateBillListener",
    "ExchangeBalanceUsdListener",
    "FundingRatePersistListener",
]

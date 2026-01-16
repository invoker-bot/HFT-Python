"""
FundingRateDataSource - 资金费率数据源

这是一个被动数据容器，不主动获取数据：
- 永远保持 STOPPED 状态，不执行 tick
- 数据由 GlobalFundingRateFetcher 填充
- 只提供 HealthyDataArray 存储和查询功能

设计理念：
- 资金费率 API 返回所有交易对数据，一次调用获取全部
- 每个交易对一个 FundingRateDataSource，存储该交易对的历史
- GlobalFundingRateFetcher 负责定时获取并分发数据
"""
from typing import Optional, TYPE_CHECKING
from ..core.healthy_data import HealthyDataArray

if TYPE_CHECKING:
    from ..exchange.base import BaseExchange, FundingRate


class FundingRateDataSource:
    """
    资金费率数据容器

    这不是一个 Listener，只是一个简单的数据容器：
    - 存储 FundingRate 历史数据
    - 提供 get_latest() 等查询方法
    - 由 GlobalFundingRateFetcher 调用 append() 填充数据

    注意：
        这个类不继承 BaseDataSource 或 Listener，因为它不需要生命周期管理。
        它只是一个附属于 TradingPairDataSource 的数据存储。
    """

    def __init__(
        self,
        exchange_class: str,
        symbol: str,
        max_seconds: float = 3600.0,  # 保留 1 小时数据
        freshness_threshold: float = 60.0,  # 资金费率更新较慢，60秒阈值
    ):
        self._exchange_class = exchange_class
        self._symbol = symbol
        self._freshness_threshold = freshness_threshold
        self._data: HealthyDataArray["FundingRate"] = HealthyDataArray(
            max_seconds=max_seconds,
        )

    @property
    def exchange_class(self) -> str:
        return self._exchange_class

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def data(self) -> HealthyDataArray["FundingRate"]:
        """获取底层 HealthyDataArray"""
        return self._data

    def append(self, funding_rate: "FundingRate") -> None:
        """添加资金费率数据（由 GlobalFundingRateFetcher 调用）"""
        self._data.append(funding_rate.timestamp, funding_rate)

    def get_latest(self, n: int = 1) -> list["FundingRate"]:
        """获取最近 n 条数据"""
        all_data = list(self._data)
        if n >= len(all_data):
            return all_data
        return all_data[-n:]

    def get_current(self) -> Optional["FundingRate"]:
        """获取当前资金费率"""
        return self._data.latest

    def is_fresh(self) -> bool:
        """检查数据是否新鲜"""
        return self._data.timeout <= self._freshness_threshold

    def __len__(self) -> int:
        return len(self._data)

    def __bool__(self) -> bool:
        return bool(self._data)

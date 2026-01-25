from abc import ABC, ABCMeta, abstractmethod
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ccxt.base.types import Order, OrderBook, Trade, Ticker
    from ..client import DatabaseClient
    from ..config import PersistConfig
    from ...exchange import BaseExchange, FundingRateBill, FundingRate


class DataBaseController(ABC):

    def __init__(self, client: 'DatabaseClient'):
        self.client = client

    @property
    def persist(self) -> 'PersistConfig':
        """获取持久化配置"""
        return self.client.config.persist

    @abstractmethod
    async def init(self):
        pass

    # any common methods for controllers can be added here


class OrderBillController(DataBaseController, metaclass=ABCMeta):
    """订单账单控制器抽象基类, 记录持久化的订单操作"""

    @abstractmethod
    async def update(self, orders: list['Order'], exchange: 'BaseExchange'):
        """更新订单状态"""

    @abstractmethod
    async def get_should_updated_orders(self, exchange: 'BaseExchange', duration_range: tuple[timedelta, timedelta]) -> list[tuple[str, str]]:
        """返回所有该exchange对应的 id trading pair"""


class FundingRateBillController(DataBaseController, metaclass=ABCMeta):
    """资金费率账单控制器抽象基类, 记录每小时的资费率收取/支出"""

    @abstractmethod
    async def update(self, bills: list['FundingRateBill'], exchange: 'BaseExchange'):
        """更新资金费率账单"""


class ExchangeStateController(DataBaseController, metaclass=ABCMeta):
    """交易所状态控制器抽象基类, 记录交易所的各种状态信息"""

    @abstractmethod
    async def update(self, future_usd: float, spot_usd: float, total_balance_usd: float, exchange: 'BaseExchange'):
        """更新交易所状态"""


class OrderBookController(DataBaseController, metaclass=ABCMeta):
    """订单簿控制器抽象基类, 记录交易所的订单簿数据"""

    @abstractmethod
    async def update(self, order_book: 'OrderBook', exchange: 'BaseExchange'):
        """更新订单簿数据"""

    @abstractmethod
    async def query(self, exchange_name: str, trading_pair: str, limit: int = 1000) -> list:
        """查询订单簿数据"""


class OHLCVController(DataBaseController, metaclass=ABCMeta):
    """OHLCV K线数据控制器抽象基类, 记录交易所的K线数据"""

    @abstractmethod
    async def update(self, trading_pair: str,
                     ohlcv_list: list[list[float]], exchange: 'BaseExchange'):
        """更新K线数据"""

    @abstractmethod
    async def query(self, exchange_name: str, trading_pair: str,
                    since: datetime = None, until: datetime = None,
                    limit: int = 1000) -> list:
        """查询K线数据"""


class TradesController(DataBaseController, metaclass=ABCMeta):
    """订单快照数据"""

    @abstractmethod
    async def update(self, trading_pair: str, trades: list['Trade'], exchange: 'BaseExchange'):
        """更新订单快照数据"""

    @abstractmethod
    async def query(self, exchange_name: str, trading_pair: str,
                    since: datetime = None, until: datetime = None,
                    limit: int = 1000):
        """查询"""


class TickerController(DataBaseController, metaclass=ABCMeta):

    @abstractmethod
    async def update(self, ticker: 'Ticker', exchange: 'BaseExchange'):
        """"""

    @abstractmethod
    async def query(self, exchange_name: str, trading_pair: str,
                    since: datetime = None, until: datetime = None,
                    limit: int = 1000):
        """"""


class FundingRateController(DataBaseController, metaclass=ABCMeta):

    @abstractmethod
    async def update(self, funding_rates: dict[str, 'FundingRate'], exchange: 'BaseExchange'):
        pass


class TickerVolumeController(DataBaseController, metaclass=ABCMeta):

    @abstractmethod
    async def update(self, volumes: dict[str, float], timestamp: float, exchange: 'BaseExchange'):
        pass

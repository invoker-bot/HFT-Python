from typing import Optional
from functools import cached_property
from pydantic import BaseModel, Field, AnyUrl
from .client import DatabaseClient


class PersistConfig(BaseModel):
    """
    持久化配置

    控制哪些数据类型需要保存
    默认全部启用，大数据量的 trades 和 orderbook 可以关闭。
    """
    order_bill: bool = Field(True, description="订单账单")
    funding_rate_bill: bool = Field(True, description="资金费率账单")
    exchange_state: bool = Field(True, description="账户余额快照")
    # positions: bool = Field(True, description="持仓快照")
    # balances: bool = Field(True, description="余额明细")
    ohlcv: bool = Field(True, description="K线数据")
    ticker: bool = Field(True, description="Ticker数据")
    ticker_volume: bool = Field(True, description="Ticker 成交量数据")
    funding_rate: bool = Field(True, description="资费率")
    trades: bool = Field(False, description="成交记录（数据量大，默认关闭）")
    order_book: bool = Field(False, description="订单簿（数据量大，默认关闭）")


class DatabaseDsn(AnyUrl):
    """
    数据库连接字符串

    支持 ClickHouse 连接字符串格式，例如：
    clickhouse://user:password@host:port/database
    """
    allowed_schemes = {'clickhouse'}


class DatabaseConfig(BaseModel):
    """
    数据库配置类

    Attributes:
        clickhouse_dsn: ClickHouse 连接字符串
        persist: 持久化配置
    """
    dsn: Optional[DatabaseDsn] = Field(
        None,
        description="连接字符串，例如：clickhouse://user:password@host:port/database"
    )
    persist: PersistConfig = Field(
        default_factory=PersistConfig,
        description="持久化配置"
    )

    @cached_property
    def instance(self) -> Optional[DatabaseClient]:
        """获取数据库客户端实例"""
        if self.dsn is None:
            return None
        return DatabaseClient.get_client(self)

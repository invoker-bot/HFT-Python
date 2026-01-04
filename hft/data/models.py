from typing import TYPE_CHECKING
from datetime import datetime
from clickhouse_sqlalchemy import engines, types
from sqlalchemy import Column, Float, String, func
from sqlalchemy.ext.declarative import declarative_base
from ..core.listener import Listener
if TYPE_CHECKING:
    from ..core.app import AppCore
    from ..exchange import BaseExchange

Base = declarative_base()


class OrderBillData(Base):  # 历史的order数据
    id = Column(String, primary_key=True)
    exchange_path = Column(String, nullable=False)  # 交易所实例的路径
    exchange_class = Column(String, nullable=False)  # 交易所实例的类名
    trading_pair = Column(String, nullable=False)  # 交易对


class FundingRateBillData(Base):

    id = Column(String, primary_key=True)
    exchange_name = Column(String, nullable=False)
    exchange_path = Column(String, nullable=False)
    trading_pair = Column(String, nullable=False)
    funding_profit = Column(Float, nullable=False)
    timestamp = Column(types.DateTime, nullable=False)

    __table_args__ = (
        engines.MergeTree(
            order_by=["timestamp", "exchange_path", "trading_pair"],
            partition_by=func.toYYYYMM(timestamp),
        ),
    )


class ExchangeFundingRateBillListener(Listener):

    def __init__(self, interval=300.0):
        super().__init__("ExchangeFundingRateBillListener", interval)
    
    async def on_tick(self):
        parent: 'BaseExchange' = self.parent
        root: 'AppCore' = self.root  # type: ignore
        if parent.ready:  # 当准备好的时候才执行
            bills = await parent.medal_fetch_funding_rates_history()
            if len(bills) > 0:
                async with root.database.get_session() as session:
                    for bill in bills:
                        session.merge(FundingRateBillData(
                            id=f"{parent.class_name}-{bill.id}",
                            exchange_name=parent.class_name,
                            exchange_path=parent.config.path,
                            trading_pair=bill.symbol,
                            funding_profit=bill.funding_amount,
                            timestamp=datetime.fromtimestamp(bill.funding_time),
                        ))
                    await session.commit()


"""
ClickHouse 数据库连接模块

使用 clickhouse-connect 官方驱动，支持异步操作。
通过 HTTP 协议连接 ClickHouse，兼容性好，支持负载均衡。

使用示例:
    db = ClickHouseDatabase(host='localhost', port=8123, user='default', password='', database='hft')
    await db.init()
    await db.insert('table_name', data, column_names=['col1', 'col2'])
    result = await db.query('SELECT * FROM table_name')
"""
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import urlparse
from ccxt.base.types import Order
from clickhouse_connect import get_async_client
from clickhouse_connect.driver.asyncclient import AsyncClient
if TYPE_CHECKING:
    from ..exchange.base import BaseExchange, FundingRateBill


class Controller(ABC):

    def __init__(self, db: 'ClickHouseDatabase'):
        self.db = db

    @abstractmethod
    async def init(self):
        pass


class OrderBillController(Controller):

    async def init(self):
        await self.db.client.command("""
            CREATE TABLE IF NOT EXISTS order_bill (
                id String,
                timestamp DateTime,
                exchange_name String,
                exchange_path String,
                trading_pair String,
                side String,
                order_type String,
                price Nullable(Float64),
                amount Nullable(Float64),
                filled Nullable(Float64),
                status Nullable(String),
                is_post_only Nullable(UInt8),
                cost Nullable(Float64),
                fee Nullable(Float64)
            )
            ENGINE = ReplacingMergeTree(timestamp)
            PARTITION BY toYYYYMM(timestamp)
            ORDER BY id
            TTL timestamp + INTERVAL 30 DAY DELETE
        """)

    async def update(self, orders: list[Order], exchange: 'BaseExchange'):
        data_updated = []
        for order in orders:
            if order['lastTradeTimestamp'] is None:
                timestamp = datetime.now()
            else:
                timestamp = datetime.fromtimestamp(order['lastTradeTimestamp'] / 1000.0)
            exchange_name = exchange.class_name
            exchange_path = exchange.config.path
            trading_pair = order['symbol']
            contract_size = exchange.get_contract_size(trading_pair)
            filled = order['filled'] * contract_size if order['filled'] is not None else None
            amount = order['amount'] * contract_size if order['amount'] is not None else None
            status = order['status']
            if exchange.get_symbol_ccxt_instance_key(trading_pair) == "swap":
                if order['type'] == 'market':
                    default_fee = exchange.config.swap_taker_fee
                else:
                    default_fee = exchange.config.swap_maker_fee
            else:
                if order['type'] == 'market':
                    default_fee = exchange.config.spot_taker_fee
                else:
                    default_fee = exchange.config.spot_maker_fee
            if order['fee'] is None or order['fee']['currency'] not in ('USDT', "USDC", "USD"):
                if status == 'closed' and isinstance(filled, float) and \
                        isinstance(order['average'], float):
                    filled_usd = abs(filled * order['average'])
                    fee = filled_usd * default_fee
                else:
                    fee = None
            else:
                fee = float(order['fee']['cost'])
            order_id = f"{exchange_path}-{order['id']}"
            side = order['side']
            price = order['price']
            is_post_only = order['postOnly']
            cost = order['cost']
            data_updated.append([
                order_id, timestamp, exchange_name, exchange_path,
                trading_pair, side, order['type'], price, amount,
                filled, status, int(is_post_only) if is_post_only is not None else None,
                cost, fee
            ])
        await self.db.client.insert('order_bill', data_updated, [
            'id', 'timestamp', 'exchange_name', 'exchange_path',
            'trading_pair', 'side', 'order_type', 'price', 'amount',
            'filled', 'status', 'is_post_only', 'cost', 'fee'
        ])

    async def get_should_updated_orders(self, exchange: 'BaseExchange') -> list[tuple[str, str]]:
        start_timestamp = datetime.now() - timedelta(hours=1)
        end_timestamp = datetime.now() - timedelta(minutes=5)

        result = await self.db.client.query("""
            SELECT id, trading_pair
            FROM order_bill FINAL
            WHERE timestamp BETWEEN %s AND %s 
            AND exchange_path = %s
            AND status NOT IN ('closed', 'canceled', 'expired', 'rejected')
        """, [start_timestamp, end_timestamp, exchange.class_name])
        return [(row[0], row[1]) for row in result.result_rows]


class FundingRateBillController:

    def __init__(self, db: 'ClickHouseDatabase'):
        self.db = db

    async def init(self):
        await self.db.client.command('''
            CREATE TABLE IF NOT EXISTS funding_rate_bill (
                id String,
                timestamp DateTime,
                exchange_name String,
                exchange_path String,
                trading_pair String,
                funding_profit Float64
            )
            ENGINE = ReplacingMergeTree(timestamp)
            PARTITION BY toYYYYMM(timestamp)
            ORDER BY id
            TTL timestamp + INTERVAL 365 DAY DELETE
        ''')

    async def update(self, bills: list['FundingRateBill'], exchange: 'BaseExchange'):
        # type: ignore
        data_updated = []
        for bill in bills:
            exchange_path = exchange.config.path
            data_updated.append([
                f"{exchange_path}-{bill.id}",
                datetime.fromtimestamp(bill.funding_time),
                exchange.class_name,
                exchange_path,
                bill.symbol,
                bill.funding_amount,
            ])
        await self.db.client.insert('funding_rate_bill', data_updated, [
            'id', 'timestamp', 'exchange_name', 'exchange_path',
            'trading_pair', 'funding_profit'
        ])


class BalanceUSDController(Controller):

    async def init(self):
        await self.db.client.command('''
            CREATE TABLE IF NOT EXISTS balance_usd (
                timestamp DateTime,
                hour DateTime MATERIALIZED toStartOfHour(timestamp),
                exchange_name String,
                exchange_path String,
                position_usd Float64,
                balance_usd Float64
            )
            ENGINE = MergeTree()
            PARTITION BY toYYYYMM(timestamp)
            ORDER BY (exchange_path, hour, timestamp)
            TTL
                timestamp + INTERVAL 30 DAY
                    GROUP BY exchange_path, hour
                    SET
                        balance_usd = avg(balance_usd),
                        position_usd = avg(position_usd),
                        exchange_name = any(exchange_name),
                        timestamp = any(timestamp),
                timestamp + INTERVAL 365 DAY DELETE
        ''')

    async def update(self, position_usd: float, balance_usd: float, exchange: 'BaseExchange'):
        data_updated = [[
            datetime.now(),
            exchange.class_name,
            exchange.config.path,
            position_usd,
            balance_usd,
        ]]
        await self.db.client.insert('balance_usd', data_updated, [
            'timestamp', 'exchange_name', 'exchange_path',
            'position_usd', 'balance_usd'
        ])


class ClickHouseDatabase:
    """
    ClickHouse 异步数据库连接管理类

    基于 clickhouse-connect 官方驱动，使用 HTTP 协议。

    Attributes:
        host: 数据库主机地址
        port: HTTP 端口（默认 8123）
        user: 用户名
        password: 密码
        database: 数据库名
        client: 异步客户端实例
    """

    def __init__(self, url: str):
        """
        初始化数据库配置

        Args:
            host: ClickHouse 服务器地址
            port: HTTP 端口（默认 8123，不是原生协议的 9000）
            user: 用户名
            password: 密码
            database: 数据库名
        """
        parsed_url = urlparse(url)
        self.host = parsed_url.hostname or 'localhost'
        self.port = parsed_url.port or 8123
        self.user = parsed_url.username or 'default'
        self.password = parsed_url.password or ''
        self.database = parsed_url.path.lstrip('/') if parsed_url.path else 'default'
        self.client: AsyncClient = None

    async def init(self):
        """
        初始化数据库连接并创建表

        创建异步客户端连接，并执行建表 DDL。
        """
        # 创建异步客户端
        self.client = await get_async_client(
            host=self.host,
            port=self.port,
            username=self.user,
            password=self.password,
            database=self.database,
            settings={'session_timezone': 'Asia/Shanghai'}
        )

        # 创建表（如果不存在）
        # await self._create_tables()
        await OrderBillController(self).init()
        await FundingRateBillController(self).init()
        await BalanceUSDController(self).init()

    async def close(self):
        """关闭数据库连接"""
        if self.client:
            await self.client.close()

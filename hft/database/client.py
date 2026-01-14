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
from ccxt.base.types import Order, OrderBook
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


class FundingRateBillController(Controller):

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


class OrderBookController(Controller):
    """
    订单簿快照控制器

    存储订单簿的 bids/asks 数据，用于回测和分析。
    数据保留 10 分钟后自动删除。
    """

    async def init(self):
        await self.db.client.command('''
            CREATE TABLE IF NOT EXISTS order_book (
                timestamp DateTime64(3),
                exchange_name String,
                trading_pair String,
                bids Nested(
                    price Float64,
                    quantity Float64
                ),
                asks Nested(
                    price Float64,
                    quantity Float64
                )
            )
            ENGINE = MergeTree()
            PARTITION BY toYYYYMMDD(timestamp)
            ORDER BY (exchange_name, trading_pair, timestamp)
            TTL toDateTime(timestamp) + INTERVAL 10 MINUTE DELETE
        ''')

    async def update(self, order_books: list[OrderBook], exchange: 'BaseExchange'):
        """
        插入订单簿快照

        Args:
            exchange_name: 交易所名称
            trading_pair: 交易对
            bids: [[price, quantity], ...]
            asks: [[price, quantity], ...]
        """
        data_updated = []
        for order_book in order_books:
            trading_pair = order_book['symbol']
            contract_size = exchange.get_contract_size(trading_pair)
            bids_p = [p for p, _q in order_book['bids']]
            bids_q = [q * contract_size for _p, q in order_book['bids']]
            asks_p = [p for p, _q in order_book['asks']]
            asks_q = [q * contract_size for _p, q in order_book['asks']]
            date = datetime.fromtimestamp(float(order_book['timestamp']) / 1000.0)
            data_updated.append([
                date,
                exchange.class_name,
                trading_pair,
                bids_p,
                bids_q,
                asks_p,
                asks_q,
            ])
        await self.db.client.insert('order_book', data_updated, [
            'timestamp', 'exchange_name', 'trading_pair',
            'bids.price', 'bids.quantity', 'asks.price', 'asks.quantity'
        ])

    async def query(self, exchange_name: str, trading_pair: str, limit: int = 1000):
        """
        查询订单簿快照

        Args:
            exchange_name: 交易所名称
            trading_pair: 交易对
            limit: 返回条数

        Returns:
            按 timestamp 升序排列的订单簿列表
        """
        result = await self.db.client.query('''
            SELECT timestamp, bids.price, bids.quantity, asks.price, asks.quantity
            FROM order_book
            WHERE exchange_name = %(exchange_name)s
              AND trading_pair = %(trading_pair)s
            ORDER BY timestamp ASC
            LIMIT %(limit)s
        ''', parameters={
            'exchange_name': exchange_name,
            'trading_pair': trading_pair,
            'limit': limit
        })
        return result.result_rows


class OHLCVController(Controller):
    """
    OHLCV K线数据控制器

    存储 K 线数据，支持按时间聚合压缩：
    - 1天后按 15 分钟聚合
    - 365天后删除
    """

    async def init(self):
        # 注意：TTL GROUP BY 需要是 ORDER BY 的前缀
        # 这里使用 timestamp_15min 作为 ORDER BY 的一部分，支持 15 分钟聚合
        await self.db.client.command('''
            CREATE TABLE IF NOT EXISTS ohlcv (
                timestamp DateTime64(3),
                close_timestamp DateTime64(3),
                exchange_name String,
                trading_pair String,
                open Float64,
                high Float64,
                low Float64,
                close Float64,
                volume Float64,
                timestamp_15min DateTime64(3) MATERIALIZED toStartOfFifteenMinutes(timestamp)
            )
            ENGINE = MergeTree()
            PARTITION BY toYYYYMM(timestamp)
            ORDER BY (exchange_name, trading_pair, timestamp_15min, timestamp, close_timestamp)
            TTL
                toDateTime(timestamp) + INTERVAL 1 DAY
                    GROUP BY exchange_name, trading_pair, timestamp_15min
                    SET
                        timestamp = min(timestamp),
                        close_timestamp = max(close_timestamp),
                        open = argMin(open, timestamp),
                        high = max(high),
                        low = min(low),
                        close = argMax(close, close_timestamp),
                        volume = sum(volume),
                toDateTime(timestamp) + INTERVAL 365 DAY DELETE
        ''')

    async def update(self, exchange_name: str, trading_pair: str,
                     ohlcv_list: list[list[float]]):
        """
        插入 OHLCV 数据

        Args:
            exchange_name: 交易所名称
            trading_pair: 交易对
            ohlcv_list: [[timestamp, open, high, low, close, volume], ...]
                        timestamp 为毫秒级时间戳
        """
        data = []
        for ohlcv in ohlcv_list:
            timestamp_ms = ohlcv[0]
            data.append([
                datetime.fromtimestamp(timestamp_ms / 1000.0),
                datetime.fromtimestamp(timestamp_ms / 1000.0),  # close_timestamp 需要外部传入
                exchange_name,
                trading_pair,
                ohlcv[1],  # open
                ohlcv[2],  # high
                ohlcv[3],  # low
                ohlcv[4],  # close
                ohlcv[5],  # volume
            ])
        await self.db.client.insert('ohlcv', data, [
            'timestamp', 'close_timestamp', 'exchange_name', 'trading_pair',
            'open', 'high', 'low', 'close', 'volume'
        ])

    async def update_with_close_timestamp(self, exchange_name: str, trading_pair: str,
                                          timestamp: datetime, close_timestamp: datetime,
                                          open_: float, high: float, low: float,
                                          close: float, volume: float):
        """插入单条 OHLCV 数据（带 close_timestamp）"""
        data = [[
            timestamp, close_timestamp, exchange_name, trading_pair,
            open_, high, low, close, volume
        ]]
        await self.db.client.insert('ohlcv', data, [
            'timestamp', 'close_timestamp', 'exchange_name', 'trading_pair',
            'open', 'high', 'low', 'close', 'volume'
        ])

    async def query(self, exchange_name: str, trading_pair: str,
                    since: datetime = None, until: datetime = None,
                    limit: int = 1000):
        """查询 OHLCV 数据"""
        params = {
            'exchange_name': exchange_name,
            'trading_pair': trading_pair,
            'limit': limit
        }
        where_clauses = [
            'exchange_name = %(exchange_name)s',
            'trading_pair = %(trading_pair)s'
        ]
        if since:
            where_clauses.append('timestamp >= %(since)s')
            params['since'] = since
        if until:
            where_clauses.append('timestamp <= %(until)s')
            params['until'] = until

        result = await self.db.client.query(f'''
            SELECT timestamp, close_timestamp, open, high, low, close, volume
            FROM ohlcv
            WHERE {' AND '.join(where_clauses)}
            ORDER BY timestamp ASC
            LIMIT %(limit)s
        ''', parameters=params)
        return result.result_rows


class TradesController(Controller):
    """
    成交记录控制器

    存储成交数据，支持按时间聚合压缩：
    - 10分钟后按 1 分钟聚合（price 取成交量加权平均，volume 求和）
    - 30天后删除
    """

    async def init(self):
        # 注意：TTL GROUP BY 需要是 ORDER BY 的前缀
        # 使用 timestamp_1min 作为 ORDER BY 的一部分，支持 1 分钟聚合
        await self.db.client.command('''
            CREATE TABLE IF NOT EXISTS trades (
                id String,
                timestamp DateTime64(3),
                exchange_name String,
                trading_pair String,
                side String,
                price Float64,
                volume Float64,
                price_volume Float64 MATERIALIZED price * volume,
                timestamp_1min DateTime64(3) MATERIALIZED toStartOfMinute(timestamp)
            )
            ENGINE = ReplacingMergeTree(timestamp)
            PARTITION BY toYYYYMMDD(timestamp)
            ORDER BY (exchange_name, trading_pair, side, timestamp_1min, id)
            TTL
                toDateTime(timestamp) + INTERVAL 10 MINUTE
                    GROUP BY exchange_name, trading_pair, side, timestamp_1min
                    SET
                        id = any(id),
                        timestamp = min(timestamp),
                        price = sum(price_volume) / sum(volume),
                        volume = sum(volume),
                toDateTime(timestamp) + INTERVAL 30 DAY DELETE
        ''')

    async def update(self, exchange_name: str, trading_pair: str,
                     trades: list[dict]):
        """
        插入成交数据

        Args:
            exchange_name: 交易所名称
            trading_pair: 交易对
            trades: [{'id': str, 'timestamp': int(ms), 'side': str, 'price': float, 'amount': float}, ...]
        """
        data = []
        for trade in trades:
            data.append([
                f"{exchange_name}-{trade['id']}",
                datetime.fromtimestamp(trade['timestamp'] / 1000.0),
                exchange_name,
                trading_pair,
                trade['side'],
                trade['price'],
                trade['amount'],
            ])
        await self.db.client.insert('trades', data, [
            'id', 'timestamp', 'exchange_name', 'trading_pair',
            'side', 'price', 'volume'
        ])

    async def query(self, exchange_name: str, trading_pair: str,
                    since: datetime = None, until: datetime = None,
                    limit: int = 1000):
        """查询成交数据"""
        params = {
            'exchange_name': exchange_name,
            'trading_pair': trading_pair,
            'limit': limit
        }
        where_clauses = [
            'exchange_name = %(exchange_name)s',
            'trading_pair = %(trading_pair)s'
        ]
        if since:
            where_clauses.append('timestamp >= %(since)s')
            params['since'] = since
        if until:
            where_clauses.append('timestamp <= %(until)s')
            params['until'] = until

        result = await self.db.client.query(f'''
            SELECT timestamp, side, price, volume
            FROM trades FINAL
            WHERE {' AND '.join(where_clauses)}
            ORDER BY timestamp ASC
            LIMIT %(limit)s
        ''', parameters=params)
        return result.result_rows


class TickerController(Controller):
    """
    Ticker 数据控制器

    存储 ticker 数据，支持按时间聚合压缩：
    - 10分钟后按 1 分钟聚合（取均值）
    - 30天后删除
    """

    async def init(self):
        # 注意：TTL GROUP BY 需要是 ORDER BY 的前缀
        # 使用 timestamp_1min 作为 ORDER BY 的一部分，支持 1 分钟聚合
        await self.db.client.command('''
            CREATE TABLE IF NOT EXISTS ticker (
                timestamp DateTime64(3),
                exchange_name String,
                trading_pair String,
                bid Float64,
                ask Float64,
                last Float64,
                timestamp_1min DateTime64(3) MATERIALIZED toStartOfMinute(timestamp)
            )
            ENGINE = MergeTree()
            PARTITION BY toYYYYMMDD(timestamp)
            ORDER BY (exchange_name, trading_pair, timestamp_1min, timestamp)
            TTL
                toDateTime(timestamp) + INTERVAL 10 MINUTE
                    GROUP BY exchange_name, trading_pair, timestamp_1min
                    SET
                        timestamp = min(timestamp),
                        bid = avg(bid),
                        ask = avg(ask),
                        last = avg(last),
                toDateTime(timestamp) + INTERVAL 30 DAY DELETE
        ''')

    async def update(self, exchange_name: str, trading_pair: str,
                     bid: float, ask: float, last: float):
        """插入 ticker 数据"""
        data = [[
            datetime.now(),
            exchange_name,
            trading_pair,
            bid,
            ask,
            last,
        ]]
        await self.db.client.insert('ticker', data, [
            'timestamp', 'exchange_name', 'trading_pair', 'bid', 'ask', 'last'
        ])

    async def query(self, exchange_name: str, trading_pair: str,
                    since: datetime = None, until: datetime = None,
                    limit: int = 1000):
        """查询 ticker 数据"""
        params = {
            'exchange_name': exchange_name,
            'trading_pair': trading_pair,
            'limit': limit
        }
        where_clauses = [
            'exchange_name = %(exchange_name)s',
            'trading_pair = %(trading_pair)s'
        ]
        if since:
            where_clauses.append('timestamp >= %(since)s')
            params['since'] = since
        if until:
            where_clauses.append('timestamp <= %(until)s')
            params['until'] = until

        result = await self.db.client.query(f'''
            SELECT timestamp, bid, ask, last
            FROM ticker
            WHERE {' AND '.join(where_clauses)}
            ORDER BY timestamp ASC
            LIMIT %(limit)s
        ''', parameters=params)
        return result.result_rows


class FundingRateController(Controller):
    """
    资金费率控制器

    存储资金费率数据，支持按时间聚合压缩：
    - 10分钟后按 1 分钟聚合（取均值）
    - 30天后删除
    """

    async def init(self):
        # 注意：TTL GROUP BY 需要是 ORDER BY 的前缀
        # 使用 timestamp_1min 作为 ORDER BY 的一部分，支持 1 分钟聚合
        await self.db.client.command('''
            CREATE TABLE IF NOT EXISTS funding_rate (
                timestamp DateTime64(3),
                exchange_name String,
                trading_pair String,
                index_price Float64,
                mark_price Float64,
                funding_rate Float64,
                daily_funding_rate Float64,
                timestamp_1min DateTime64(3) MATERIALIZED toStartOfMinute(timestamp)
            )
            ENGINE = MergeTree()
            PARTITION BY toYYYYMMDD(timestamp)
            ORDER BY (exchange_name, trading_pair, timestamp_1min, timestamp)
            TTL
                toDateTime(timestamp) + INTERVAL 10 MINUTE
                    GROUP BY exchange_name, trading_pair, timestamp_1min
                    SET
                        timestamp = min(timestamp),
                        index_price = avg(index_price),
                        mark_price = avg(mark_price),
                        funding_rate = avg(funding_rate),
                        daily_funding_rate = avg(daily_funding_rate),
                toDateTime(timestamp) + INTERVAL 30 DAY DELETE
        ''')

    async def update(self, exchange_name: str, trading_pair: str,
                     index_price: float, mark_price: float,
                     funding_rate: float, daily_funding_rate: float):
        """插入资金费率数据"""
        data = [[
            datetime.now(),
            exchange_name,
            trading_pair,
            index_price,
            mark_price,
            funding_rate,
            daily_funding_rate,
        ]]
        await self.db.client.insert('funding_rate', data, [
            'timestamp', 'exchange_name', 'trading_pair',
            'index_price', 'mark_price', 'funding_rate', 'daily_funding_rate'
        ])

    async def query(self, exchange_name: str, trading_pair: str,
                    since: datetime = None, until: datetime = None,
                    limit: int = 1000):
        """查询资金费率数据"""
        params = {
            'exchange_name': exchange_name,
            'trading_pair': trading_pair,
            'limit': limit
        }
        where_clauses = [
            'exchange_name = %(exchange_name)s',
            'trading_pair = %(trading_pair)s'
        ]
        if since:
            where_clauses.append('timestamp >= %(since)s')
            params['since'] = since
        if until:
            where_clauses.append('timestamp <= %(until)s')
            params['until'] = until

        result = await self.db.client.query(f'''
            SELECT timestamp, index_price, mark_price, funding_rate, daily_funding_rate
            FROM funding_rate
            WHERE {' AND '.join(where_clauses)}
            ORDER BY timestamp ASC
            LIMIT %(limit)s
        ''', parameters=params)
        return result.result_rows


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
        await OrderBookController(self).init()
        await OHLCVController(self).init()
        await TradesController(self).init()
        await TickerController(self).init()
        await FundingRateController(self).init()

    async def close(self):
        """关闭数据库连接"""
        if self.client:
            await self.client.close()

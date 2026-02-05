import logging
from typing import Optional, TYPE_CHECKING
from datetime import datetime, timedelta
from clickhouse_connect import get_async_client
from clickhouse_connect.driver.asyncclient import AsyncClient
from ..client import DatabaseClient
from .base import DataBaseController, OrderBillController, \
    FundingRateBillController, ExchangeStateController, OrderBookController, \
    OHLCVController, TradesController, TickerController, FundingRateController, \
    TickerVolumeController
if TYPE_CHECKING:
    from ccxt.base.types import Order, OrderBook, Trade, Ticker
    from ...exchange import BaseExchange, FundingRateBill, FundingRate


logger = logging.getLogger(__name__)


class ClickHouseDatabaseClient(DatabaseClient):

    def __init__(self, config):
        super().__init__(config)
        self.connector: AsyncClient = None

    async def init(self):
        if self.has_connection():
            self.connector = await get_async_client(
                host=self.host,
                port=self.port,
                username=self.user,
                password=self.password,
                database=self.database,
                settings={'session_timezone': 'Asia/Shanghai'}
            )
            for controller in self.controllers.values():
                controller_instance = controller(self)
                await controller_instance.init()

    async def close(self):
        if self.connector is not None:
            await self.connector.close()
            self.connector = None


class ClickHouseDatabaseController(DataBaseController):

    @property
    def connector(self) -> Optional[AsyncClient]:
        return self.client.connector


class OrderBillClickHouseDatabaseController(OrderBillController, ClickHouseDatabaseController):

    async def init(self):
        connector = self.connector
        if connector is not None:
            await self.connector.command("""
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

    async def update(self, orders: list['Order'], exchange: 'BaseExchange'):
        if not self.persist.order_bill or len(orders) == 0:
            return
        data_updated = []
        for order in orders:
            if order['lastTradeTimestamp'] is None:
                timestamp = datetime.now()
            else:
                timestamp = datetime.fromtimestamp(order['lastTradeTimestamp'] / 1000.0)
            exchange_name = exchange.class_name
            exchange_path = exchange.config.path
            trading_pair = order['symbol']
            contract_size = await exchange.get_contract_size_async(trading_pair)
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
        await self.connector.insert('order_bill', data_updated, [
            'id', 'timestamp', 'exchange_name', 'exchange_path',
            'trading_pair', 'side', 'order_type', 'price', 'amount',
            'filled', 'status', 'is_post_only', 'cost', 'fee'
        ])

    async def get_should_updated_orders(self, exchange: 'BaseExchange',
                                        duration_range: tuple[timedelta, timedelta] = (
                                            timedelta(minutes=5), timedelta(hours=1)
                                        )) -> list[tuple[str, str]]:
        if not self.persist.order_bill:
            return []
        range_min, range_max = duration_range
        start_timestamp = datetime.now() - range_max
        end_timestamp = datetime.now() - range_min

        result = await self.connector.query("""
            SELECT id, trading_pair
            FROM order_bill FINAL
            WHERE timestamp BETWEEN %s AND %s
            AND exchange_path = %s
            AND status NOT IN ('closed', 'canceled', 'expired', 'rejected')
        """, [start_timestamp, end_timestamp, exchange.class_name])
        return [(row[0], row[1]) for row in result.result_rows]


class FundingRateBillClickHouseDatabaseController(FundingRateBillController, ClickHouseDatabaseController):

    async def init(self):
        await self.connector.command('''
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
        if not self.persist.funding_rate_bill or len(bills) == 0:
            return
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
        await self.connector.insert('funding_rate_bill', data_updated, [
            'id', 'timestamp', 'exchange_name', 'exchange_path',
            'trading_pair', 'funding_profit'
        ])


class ExchangeStateClickHouseDatabaseController(ExchangeStateController, ClickHouseDatabaseController):

    async def init(self):
        await self.connector.command('''
            CREATE TABLE IF NOT EXISTS exchange_state (
                timestamp DateTime,
                hour DateTime MATERIALIZED toStartOfHour(timestamp),
                exchange_name String,
                exchange_path String,
                future_usd Float64,
                spot_usd  Float64,
                total_balance_usd Float64
            )
            ENGINE = MergeTree()
            PARTITION BY toYYYYMM(timestamp)
            ORDER BY (exchange_path, hour, timestamp)
            TTL
                timestamp + INTERVAL 30 DAY
                    GROUP BY exchange_path, hour
                    SET
                        future_usd = avg(future_usd),
                        spot_usd = avg(spot_usd),
                        total_balance_usd = avg(total_balance_usd),
                        exchange_name = any(exchange_name),
                        timestamp = any(timestamp),
                timestamp + INTERVAL 365 DAY DELETE
        ''')

    async def update(self, future_usd: float, spot_usd: float, total_balance_usd: float, exchange: 'BaseExchange'):
        if not self.persist.exchange_state:
            return
        data_updated = [[
            datetime.now(),
            exchange.class_name,
            exchange.config.path,
            future_usd,
            spot_usd,
            total_balance_usd,
        ]]
        await self.connector.insert('exchange_state', data_updated, [
            'timestamp', 'exchange_name', 'exchange_path',
            'future_usd', 'spot_usd', 'total_balance_usd'
        ])


class OrderBookClickHouseDatabaseController(OrderBookController, ClickHouseDatabaseController):
    """
    订单簿快照控制器

    存储订单簿的 bids/asks 数据，用于回测和分析。
    数据保留 10 分钟后自动删除。
    """

    async def init(self):
        await self.connector.command('''
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

    async def update(self, order_books: list['OrderBook'], exchange: 'BaseExchange'):
        """
        插入订单簿快照

        Args:
            exchange_name: 交易所名称
            trading_pair: 交易对
            bids: [[price, quantity], ...]
            asks: [[price, quantity], ...]
        """
        if not self.persist.order_book or len(order_books) == 0:
            return
        data_updated = []
        for order_book in order_books:
            trading_pair = order_book['symbol']
            contract_size = await exchange.get_contract_size_async(trading_pair)
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
        await self.connector.insert('order_book', data_updated, [
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
        if not self.persist.order_book:
            return []
        result = await self.connector.query('''
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


class OHLCVClickHouseDatabaseController(OHLCVController, ClickHouseDatabaseController):
    """
    OHLCV K线数据控制器

    存储 K 线数据，支持按时间聚合压缩：
    - 1天后按 15 分钟聚合
    - 365天后删除
    """

    async def init(self):
        # 注意：TTL GROUP BY 需要是 ORDER BY 的前缀
        # 这里使用 timestamp_1min 作为 ORDER BY 的一部分，支持聚合
        await self.connector.command('''
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
            ENGINE = ReplacingMergeTree(timestamp)
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

    async def update(self, trading_pair: str,
                     ohlcv_list: list[list[float]], exchange: 'BaseExchange'):
        """
        插入 OHLCV 数据

        Args:
            exchange_name: 交易所名称
            trading_pair: 交易对
            ohlcv_list: [[timestamp, open, high, low, close, volume], ...]
                        timestamp 为毫秒级时间戳
        """
        if not self.persist.ohlcv or len(ohlcv_list) <= 1:
            return
        data = []
        for index in range(len(ohlcv_list) - 1):
            ohlcv = ohlcv_list[index]
            timestamp_ms, o, h, l, c, v = ohlcv
            timestamp_end_ms = ohlcv_list[index + 1][0]
            contract_size = await exchange.get_contract_size_async(trading_pair)
            v *= contract_size
            data.append([
                datetime.fromtimestamp(timestamp_ms / 1000.0),
                datetime.fromtimestamp(timestamp_end_ms / 1000.0),
                exchange.class_name,
                trading_pair,
                o, h, l, c, v
            ])
        await self.connector.insert('ohlcv', data, [
            'timestamp', 'close_timestamp', 'exchange_name', 'trading_pair',
            'open', 'high', 'low', 'close', 'volume'
        ])

    async def query(self, exchange_name: str, trading_pair: str,
                    since: datetime = None, until: datetime = None,
                    limit: int = 1000):
        """查询 OHLCV 数据"""
        if not self.persist.ohlcv:
            return []
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

        result = await self.connector.query(f'''
            SELECT timestamp, close_timestamp, open, high, low, close, volume
            FROM ohlcv
            WHERE {' AND '.join(where_clauses)}
            ORDER BY timestamp ASC
            LIMIT %(limit)s
        ''', parameters=params)
        return result.result_rows


class TradesClickHouseController(TradesController, ClickHouseDatabaseController):
    """
    成交记录控制器

    存储成交数据，支持按时间聚合压缩：
    - 10分钟后按 1 分钟聚合（price 取成交量加权平均，volume 求和）
    - 30天后删除
    """

    async def init(self):
        # 注意：TTL GROUP BY 需要是 ORDER BY 的前缀
        # 使用 timestamp_1min 作为 ORDER BY 的一部分，支持 1 分钟聚合
        await self.connector.command('''
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

    async def update(self, trading_pair: str, trades: list['Trade'], exchange: 'BaseExchange'):
        """
        插入成交数据

        Args:
            exchange_name: 交易所名称
            trading_pair: 交易对
            trades: [{'id': str, 'timestamp': int(ms), 'side': str, 'price': float, 'amount': float}, ...]
        """
        if not self.persist.trades:
            return
        data = []
        for trade in trades:
            contract_size = await exchange.get_contract_size_async(trading_pair)
            volume = trade['amount'] * contract_size
            data.append([
                f"{exchange.class_name}-{trade['id']}",
                datetime.fromtimestamp(trade['timestamp'] / 1000.0),
                exchange.class_name,
                trading_pair,
                trade['side'],
                trade['price'],
                volume,
            ])
        await self.connector.insert('trades', data, [
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

        result = await self.connector.query(f'''
            SELECT timestamp, side, price, volume
            FROM trades FINAL
            WHERE {' AND '.join(where_clauses)}
            ORDER BY timestamp ASC
            LIMIT %(limit)s
        ''', parameters=params)
        return result.result_rows


class TickerClickHouseController(TickerController, ClickHouseDatabaseController):
    """
    Ticker 数据控制器

    存储 ticker 数据，支持按时间聚合压缩：
    - 10分钟后按 1 分钟聚合（取均值）
    - 30天后删除
    """

    async def init(self):
        # 注意：TTL GROUP BY 需要是 ORDER BY 的前缀
        # 使用 timestamp_1min 作为 ORDER BY 的一部分，支持 1 分钟聚合
        await self.connector.command('''
            CREATE TABLE IF NOT EXISTS ticker (
                timestamp DateTime64(3),
                exchange_name String,
                trading_pair String,
                bid Float64,
                bidVolume Float64,
                ask Float64,
                askVolume Float64,
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
                        bidVolume = sum(bidVolume),
                        ask = avg(ask),
                        askVolume = sum(askVolume),
                        last = avg(last),
                toDateTime(timestamp) + INTERVAL 30 DAY DELETE
        ''')  # baseVolume quoteVolume

    async def update(self, ticker: 'Ticker', exchange: 'BaseExchange'):
        """插入 ticker 数据"""
        if not self.persist.ticker:
            return
        contract_size = await exchange.get_contract_size_async(ticker.symbol)
        data = [[
            datetime.now(),
            exchange.class_name,
            ticker.symbol,
            ticker.bid,
            ticker.bidVolume * contract_size,
            ticker.ask,
            ticker.askVolume * contract_size,
            ticker.last,
        ]]
        await self.connector.insert('ticker', data, [
            'timestamp', 'exchange_name', 'trading_pair', 'bid', 'bidVolume',
            'ask', 'askVolume', 'last'
        ])

    async def query(self, exchange_name: str, trading_pair: str,
                    since: datetime = None, until: datetime = None,
                    limit: int = 1000):
        """查询 ticker 数据"""
        if not self.persist.ticker:
            return []
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

        result = await self.connector.query(f'''
            SELECT timestamp, bid, bidVolume, ask, askVolume, last
            FROM ticker
            WHERE {' AND '.join(where_clauses)}
            ORDER BY timestamp ASC
            LIMIT %(limit)s
        ''', parameters=params)
        return result.result_rows


class FundingRateClickHouseController(FundingRateController, ClickHouseDatabaseController):
    """
    资金费率控制器

    存储资金费率数据，支持按时间聚合压缩：
    - 10分钟后按 1 分钟聚合（取均值）
    - 30天后删除
    """

    async def init(self):
        # 注意：TTL GROUP BY 需要是 ORDER BY 的前缀
        # 使用 timestamp_1min 作为 ORDER BY 的一部分，支持 1 分钟聚合
        await self.connector.command('''
            CREATE TABLE IF NOT EXISTS funding_rate (
                timestamp DateTime64,
                exchange_name String,
                trading_pair String,
                base_funding_rate Float64,
                funding_rate Float64,
                interval_hours Int64,
                timestamp_1min DateTime64 MATERIALIZED toStartOfMinute(timestamp)
            )
            ENGINE = ReplacingMergeTree(timestamp)
            PARTITION BY toYYYYMMDD(timestamp)
            ORDER BY (exchange_name, trading_pair, timestamp_1min, timestamp)
            TTL
                toDateTime(timestamp) + INTERVAL 10 MINUTE
                    GROUP BY exchange_name, trading_pair, timestamp_1min
                    SET
                        timestamp = min(timestamp),
                        funding_rate = avg(funding_rate),
                        daily_funding_rate = avg(daily_funding_rate),
                toDateTime(timestamp) + INTERVAL 30 DAY DELETE
        ''')
        await self.connector.command('''
            CREATE TABLE IF NOT EXISTS index_price (
                timestamp DateTime64,
                exchange_name String,
                trading_pair String,
                index_price Float64,
                timestamp_1min DateTime64 MATERIALIZED toStartOfMinute(timestamp)
            )
            ENGINE = ReplacingMergeTree(timestamp)
            PARTITION BY toYYYYMMDD(timestamp)
            ORDER BY (exchange_name, trading_pair, timestamp_1min, timestamp)
            TTL toDateTime(timestamp) + INTERVAL 10 MINUTE
                GROUP BY exchange_name, trading_pair, timestamp_1min
                SET
                    timestamp = min(timestamp),
                    index_price = avg(index_price),
                toDateTime(timestamp) + INTERVAL 30 DAY DELETE
        ''')
        await self.connector.command('''
            CREATE TABLE IF NOT EXISTS mark_price (
                timestamp DateTime64,
                exchange_name String,
                trading_pair String,
                mark_price Float64,
                timestamp_1min DateTime64 MATERIALIZED toStartOfMinute(timestamp)
            )
            ENGINE = ReplacingMergeTree(timestamp)
            PARTITION BY toYYYYMMDD(timestamp)
            ORDER BY (exchange_name, trading_pair, timestamp_1min, timestamp)
            TTL toDateTime(timestamp) + INTERVAL 10 MINUTE
                GROUP BY exchange_name, trading_pair, timestamp_1min
                SET
                    timestamp = min(timestamp),
                    mark_price = avg(mark_price),
                toDateTime(timestamp) + INTERVAL 30 DAY DELETE
        ''')

    async def update(self, funding_rates: dict[str, 'FundingRate'], exchange: 'BaseExchange'):
        """插入资金费率数据"""
        if not self.persist.funding_rate:
            return
        data = [[
            datetime.fromtimestamp(funding_rate.timestamp),
            exchange.class_name,
            funding_rate.symbol,
            funding_rate.base_funding_rate,
            funding_rate.funding_rate,
            funding_rate.interval_hours,
        ] for funding_rate in funding_rates.values()]
        await self.connector.insert('funding_rate', data, [
            'timestamp', 'exchange_name', 'trading_pair',
            'base_funding_rate', 'funding_rate', 'interval_hours'
        ])
        data = [[
            datetime.fromtimestamp(funding_rate.index_price_timestamp),
            exchange.class_name,
            funding_rate.symbol,
            funding_rate.index_price,
        ] for funding_rate in funding_rates.values()]
        await self.connector.insert('index_price', data, [
            'timestamp', 'exchange_name', 'trading_pair', 'index_price'
        ])
        data = [[
            datetime.fromtimestamp(funding_rate.mark_price_timestamp),
            exchange.class_name,
            funding_rate.symbol,
            funding_rate.mark_price,
        ] for funding_rate in funding_rates.values()]
        await self.connector.insert('mark_price', data, [
            'timestamp', 'exchange_name', 'trading_pair', 'mark_price'
        ])

    # async def query(self, exchange_name: str, trading_pair: str,
    #                 since: datetime = None, until: datetime = None,
    #                 limit: int = 1000):
    #     """查询资金费率数据"""
    #     params = {
    #         'exchange_name': exchange_name,
    #         'trading_pair': trading_pair,
    #         'limit': limit
    #     }
    #     where_clauses = [
    #         'exchange_name = %(exchange_name)s',
    #         'trading_pair = %(trading_pair)s'
    #     ]
    #     if since:
    #         where_clauses.append('timestamp >= %(since)s')
    #         params['since'] = since
    #     if until:
    #         where_clauses.append('timestamp <= %(until)s')
    #         params['until'] = until
#
    #     result = await self.db.client.query(f'''
    #         SELECT timestamp, index_price, mark_price, funding_rate, daily_funding_rate
    #         FROM funding_rate
    #         WHERE {' AND '.join(where_clauses)}
    #         ORDER BY timestamp ASC
    #         LIMIT %(limit)s
    #     ''', parameters=params)
    #     return result.result_rows


class TickerVolumeClickHouseController(TickerVolumeController, ClickHouseDatabaseController):
    """
    Ticker 成交量数据控制器

    存储 ticker 成交量数据，支持按时间聚合压缩：
    - 10分钟后按 1 分钟聚合（取均值）
    - 30天后删除
    """

    async def init(self):
        # 注意：TTL GROUP BY 需要是 ORDER BY 的前缀
        # 使用 timestamp_1min 作为 ORDER BY 的一部分，支持 1 分钟聚合
        await self.connector.command('''
            CREATE TABLE IF NOT EXISTS ticker_volume (
                timestamp DateTime64,
                exchange_name String,
                trading_pair String,
                volume Float64,
                timestamp_1min DateTime64 MATERIALIZED toStartOfMinute(timestamp)
            )
            ENGINE = ReplacingMergeTree(timestamp)
            PARTITION BY toYYYYMMDD(timestamp)
            ORDER BY (exchange_name, trading_pair, timestamp_1min, timestamp)
            TTL
                toDateTime(timestamp) + INTERVAL 10 MINUTE
                    GROUP BY exchange_name, trading_pair, timestamp_1min
                    SET
                        timestamp = min(timestamp),
                        volume = avg(volume),
                toDateTime(timestamp) + INTERVAL 30 DAY DELETE
        ''')

    async def update(self, volumes: dict[str, float], timestamp: float, exchange: 'BaseExchange'):
        """插入 ticker 成交量数据"""
        if not self.persist.ticker_volume or len(volumes) == 0:
            return
        data = []
        dt = datetime.fromtimestamp(timestamp)
        for trading_pair, volume in volumes.items():
            data.append([
                dt,
                exchange.class_name,
                trading_pair,
                volume
            ])
        await self.connector.insert('ticker_volume', data, [
            'timestamp', 'exchange_name', 'trading_pair', 'volume'
        ])


ClickHouseDatabaseClient.controllers = {
    OrderBillController: OrderBillClickHouseDatabaseController,
    FundingRateBillController: FundingRateBillClickHouseDatabaseController,
    ExchangeStateController: ExchangeStateClickHouseDatabaseController,
    OrderBookController: OrderBookClickHouseDatabaseController,
    OHLCVController: OHLCVClickHouseDatabaseController,
    TradesController: TradesClickHouseController,
    TickerController: TickerClickHouseController,
    FundingRateController: FundingRateClickHouseController,
    TickerVolumeController: TickerVolumeClickHouseController,
}

DatabaseClient.clients = {
    'clickhouse': ClickHouseDatabaseClient,
}

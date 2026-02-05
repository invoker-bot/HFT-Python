"""
交易所基类

提供统一的交易所 API 封装，支持：
- 市场数据（ticker, orderbook, trades, ohlcv）
- 交易方法（下单、撤单、查询）
- 账户方法（余额、持仓）
- 期货方法（杠杆、保证金模式）
- 资金费率
- WebSocket 订阅
- 状态持久化
"""
import asyncio
import time
from abc import ABCMeta, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Optional

from cache import AsyncTTL
from ccxt.base.errors import InvalidOrder
from ccxt.base.types import (Order, OrderBook, OrderRequest, Position, Ticker, Balance, Balances,
                             Trade, MarketInterface, CurrencyInterface)
from ccxt.pro import Exchange as CCXTExchange
from pyee.asyncio import AsyncIOEventEmitter

from ..core.healthy_data import HealthyData
from ..core.listener import Listener
# from ..indicator.persist import (ExchangeBalanceUsdListener,
#                                  ExchangeFundingRateBillListener)
from ..plugin import pm
from .listeners import ExchangeStateListener, ExchangePositionListener, \
    ExchangeBalanceListener, ExchangeOrderBillListener
from .utils import round_to_precision

if TYPE_CHECKING:
    from ..core.app import AppCore
    from .config import BaseExchangeConfig


class TradeType(StrEnum):
    """交易类型"""
    SPOT = "spot"  # 现货
    SWAP = "swap"  # 永续合约
    FUTURE = "future"  # 期货合约
    OPTION = "option"  # 期权合约


class TradeSubType(StrEnum):
    """交易子类型"""
    LINEAR = "linear"  # USDT 永续合约
    INVERSE = "inverse"  # 币本位合约


@dataclass
class MarketTradingPair:
    """市场交易对的信息，据此可确定交易对的类型"""
    exchange: str
    base: str
    quote: str
    trade_type: TradeType
    id: str  # 交易对 ID
    trade_sub_type: Optional[TradeSubType] = None
    expiry: Optional[float] = None

    def __hash__(self):
        return hash(self.exchange) ^ hash(self.id)

    def __eq__(self, value):
        if isinstance(value, str):
            return self.id == value
        elif isinstance(value, MarketTradingPair):
            return self.exchange == value.exchange and self.id == value.id
        return False

    @property
    def is_spot(self):
        return self.trade_type == TradeType.SPOT

    @property
    def is_swap(self):
        return self.trade_type == TradeType.SWAP

    @property
    def trade_quote_asset(self) -> str:
        """交易该资源需要的资产"""
        if self.trade_type in (TradeType.SWAP, TradeType.FUTURE):
            if self.trade_sub_type == TradeSubType.INVERSE:  # 币本位合约
                return self.base
        return self.quote


class OrderType(StrEnum):
    """订单类型"""
    MARKET = "market"
    LIMIT = "limit"
    LIMIT_POST_ONLY = "limit_post_only"


@dataclass
class FundingRate:
    """资金费率数据"""
    exchange: str
    symbol: str                         # 交易对 (如 BTC/USDT:USDT)
    timestamp: float                    # 数据时间戳
    expiry: Optional[float]             # 到期时间
    base_funding_rate: float            # 基础资金费率
    next_funding_rate: float            # 预测下次资金费率
    next_funding_timestamp: float       # 下次结算时间戳
    funding_interval_hours: int         # 结算间隔（小时）
    mark_price: float                   # 标记价格
    mark_price_timestamp: float         # 标记价格时间戳
    index_price: float                  # 指数价格
    index_price_timestamp: float        # 指数价格时间戳
    minimum_funding_rate: float = -0.03     # 最小资金费率
    maximum_funding_rate: float = 0.03      # 最大资金费率

    @property
    def seconds_until_funding(self) -> float:
        """距离下次结算的秒数"""
        return max(self.next_funding_timestamp - time.time(), 0.0)

    @property
    def daily_funding_rate(self) -> float:
        """日化资金费率"""
        return self.next_funding_rate * (24 / self.funding_interval_hours)

    @property
    def annual_funding_rate(self) -> float:
        """年化资金费率"""
        return self.daily_funding_rate * 365

    @property
    def basis(self) -> float:
        """基差 (mark - index) / index"""
        if self.index_price == 0:
            return 0.0
        return (self.mark_price - self.index_price) / self.index_price


@dataclass
class FundingRateBill:
    """资金费率账单"""
    id: str
    symbol: str
    funding_time: float
    funding_amount: float


class BaseExchange(Listener, metaclass=ABCMeta):
    """
    交易所基类

    提供统一的交易所 API 封装
    """
    class_name: ClassVar[str] = "base_exchange"
    __pickle_exclude__ = {*Listener.__pickle_exclude__, "config", "event", "_positions",
                          "_balances", "_markets", "_currencies"}

    # 是否为统一账户模式（现货和合约共用账户）, 从而影响了 balance计算
    # OKX 等交易所为 True，Binance 等为 False（默认）
    unified_account: ClassVar[bool] = False
    # ccxt key -> {pair -> "used/free/total"}
    # self.add_child(ExchangeFundingRateBillListener())
    # self.add_child(ExchangeBalanceUsdListener(60))
    # self.add_child(ExchangeOrderBillListener())
    # self.add_child(ExchangePositionListener())
    # self.add_child(ExchangeBalanceListener())
    # self.add_child(ExchangeCurrenciesListener())

    @property
    def event(self) -> AsyncIOEventEmitter:
        """通过树形结构获取"""
        return self.parent.event

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self.config: 'BaseExchangeConfig' = kwargs['config']
        # self.event = AsyncIOEventEmitter()  # 重新创建事件发射器
        self._markets: HealthyData[dict[str, MarketInterface]] = HealthyData(max_age=1800.0)  # id -> market dict
        self._currencies: HealthyData[dict[str, CurrencyInterface]] = HealthyData(max_age=1800.0)  # 货币信息
        # 重新创建持仓数据管理器
        # 持仓数据：使用 HealthyData 管理缓存和刷新
        self._positions: HealthyData[dict[str, float]] = HealthyData(
            max_age=30.0  # 此数据主要是对合约生效
        )
        # 余额数据：使用 HealthyData 管理缓存和刷新 (symbol -> {"used":..., "free":..., "total":...})
        self._balances: dict[str, HealthyData[dict[str, dict[str, float]]]] = defaultdict(
            lambda: HealthyData(max_age=30.0)
        )
        factory = self.root.factory
        factory.get_or_create(ExchangeStateListener, parent=self)  # 写入余额变化
        if "swap" in self.config.ccxt_instances:
            factory.get_or_create(ExchangePositionListener, parent=self)  # 定期同步持仓，仅合约需要
        factory.get_or_create(ExchangeBalanceListener, parent=self)  # 定期同步余额
        factory.get_or_create(ExchangeOrderBillListener, parent=self)  # 订单账单监听器

    @property
    def markets(self) -> HealthyData[dict[str, MarketInterface]]:
        """获取缓存的市场数据（不检查健康状态）"""
        return self._markets

    async def get_markets_data(self) -> dict[str, MarketInterface]:
        """获取最新的市场数据"""
        return self.markets.get_data_or_update_by_func(self.load_markets_internal)

    @property
    def currencies(self) -> HealthyData[dict[str, CurrencyInterface]]:
        """获取缓存的货币数据（不检查健康状态）"""
        return self._currencies

    async def get_currencies_data(self) -> dict[str, CurrencyInterface]:
        """获取最新的货币数据"""
        return self.currencies.get_data_or_update_by_func(self.fetch_currencies_internal)

    @property
    def positions(self) -> HealthyData[dict[str, float]]:
        """获取缓存的持仓数据（不检查健康状态）"""
        return self._positions

    async def get_positions_data(self) -> dict[str, float]:
        """获取最新的持仓数据"""
        return await self.medal_fetch_positions()

    @property
    def balances(self) -> dict[str, HealthyData[dict[str, dict[str, float]]]]:
        return self._balances

    async def get_balances_data(self, ccxt_instance_key: str) -> dict[str, Balance]:
        """获取最新的余额数据"""
        return await self.medal_fetch_balance(ccxt_instance_key)

    async def get_total_balances_data(self) -> dict[str, dict[str, float]]:
        if self.unified_account:
            return await self.get_balances_data(self.config.ccxt_instance_key)
        else:
            result = defaultdict(lambda: defaultdict(float))
            for ccxt_key in self.config.ccxt_instances.keys():
                for key, data in (await self.get_balances_data(ccxt_key)).items():
                    for field, num in data.items():
                        result[key][field] += num
            return result

    async def medal_get_pair_amount(self, pair: str | MarketTradingPair) -> float:
        """获取指定交易对的持仓数量（正数表示多头，负数表示空头）"""
        symbol = self.to_raw_symbol(pair)
        markets = await self.get_markets_data()
        market = markets[symbol]
        match market['type']:
            case 'swap':
                positions = await self.get_positions_data()
                return positions.get(symbol, 0.0)
            case 'spot':
                base = market['base'] # 获取基础货币数量
                if self.unified_account:
                    balances = await self.get_balances_data(self.config.ccxt_instance_key)
                    return balances.get(base, {}).get("total", 0.0)  #
                else:
                    result = 0.0
                    for ccxt_key in self.config.ccxt_instances.keys():
                        balances = await self.get_balances_data(ccxt_key)
                        result += balances.get(base, {}).get("total", 0.0)
                    return result
            case _:
                raise NotImplementedError(f"Unsupported trade type for symbol {symbol}")

    def to_raw_symbol(self, pair: str | MarketTradingPair) -> str:
        if isinstance(pair, MarketTradingPair):
            return pair.id
        return pair

    def to_trading_pair(self, pair: str | MarketTradingPair | MarketInterface) -> MarketTradingPair:
        if isinstance(pair, MarketTradingPair):
            return pair
        if isinstance(pair, str):
            market: MarketInterface = self._markets.get_data()[pair]
        else:
            market: MarketInterface = pair
        base = market['base']
        quote = market['quote']
        trade_type = TradeType(market['type'])
        trade_sub_type = market['subType']
        if trade_sub_type is not None:
            trade_sub_type = TradeSubType(trade_sub_type)
        expiry = market['expiry']
        if expiry is not None:
            expiry = float(expiry) / 1000.0  # 转换为秒级时间戳
        else:
            expiry = float('inf')
        return MarketTradingPair(
            self.class_name, base, quote, trade_type, market['id'], trade_sub_type, expiry)

    def get_symbol_ccxt_instance_key(self, pair: str | MarketTradingPair) -> str:
        pair = self.to_raw_symbol(pair)
        type_str = self._markets.get_data()[pair]['type']  # 需要提前load markets
        return self.config.to_ccxt_instance_key(type_str)

    def get_exchange(self, pair: str | MarketTradingPair) -> CCXTExchange:  # 查询指定id的交易所实例
        return self.exchanges[self.get_symbol_ccxt_instance_key(pair)]

    def get_contract_size(self, pair: str | MarketTradingPair) -> float:
        symbol = self.to_raw_symbol(pair)
        market = self._markets.get_data()[symbol]
        refactor = 1.0
        if market['contract']:
            contract_size = float(market['contractSize'])
            if contract_size <= 1e-8:
                raise ValueError(f"Invalid contract size {contract_size} for symbol {symbol}")
            refactor *= contract_size
        return refactor

    @property
    def exchanges(self) -> dict[str, CCXTExchange]:
        """获取所有 ccxt 交易所实例"""
        return self.config.ccxt_instances

    @property
    def exchange_id(self) -> str:
        """交易所 ID"""
        return self.config.ccxt_instance.id

    async def on_start(self) -> None:
        """启动时加载市场数据"""
        await super().on_start()
        await self.open()
        await self.on_tick()  # 这里加载市场数据

    async def on_stop(self) -> None:
        """停止时保存状态"""
        await self.close()
        await super().on_stop()

    async def on_tick(self):
        """每 tick 刷新数据"""
        await self.load_time_diff()
        await self.load_markets()
        await self.fetch_currencies()

    async def on_health_check(self):
        await self.on_tick()

    # ========== 市场数据方法 ticker/order_book/trades/ohlcv ==========

    async def fetch_ticker(self, symbol: str) -> Ticker:
        """
        获取 ticker 数据

        Returns:
            {
                'symbol': 'BTC/USDT',
                'timestamp': 1678886400000,
                'high': 27500.00,
                'low': 26800.00,
                'bid': 27100.00,
                'ask': 27105.00,
                'last': 27100.00,
                'baseVolume': 1234.56,
                'quoteVolume': 33567890.12,
                ...
            }
        """
        exchange = self.get_exchange(symbol)
        return await exchange.fetch_ticker(symbol)

    async def watch_ticker(self, symbol: str) -> Ticker:
        """订阅 ticker"""
        exchange = self.get_exchange(symbol)
        return await exchange.watch_ticker(symbol)

    async def un_watch_ticker(self, symbol: str):
        """取消订阅 ticker"""
        exchange = self.get_exchange(symbol)
        await exchange.un_watch_ticker(symbol)

    async def fetch_order_book(self, symbol: str, limit: Optional[int] = None) -> OrderBook:
        """
        获取订单簿

        Returns:
            {
                'symbol': 'BTC/USDT',
                'bids': [[price, amount], ...],
                'asks': [[price, amount], ...],
                'timestamp': 1678886400000,
                ...
            }
        """
        exchange = self.get_exchange(symbol)
        return await exchange.fetch_order_book(symbol, limit)

    async def watch_order_book(self, symbol: str, limit: Optional[int] = None) -> OrderBook:
        """订阅订单簿"""
        exchange = self.get_exchange(symbol)
        return await exchange.watch_order_book(symbol, limit)

    async def un_watch_order_book(self, symbol: str):
        """取消订阅订单簿"""
        exchange = self.get_exchange(symbol)
        await exchange.un_watch_order_book(symbol)

    async def fetch_trades(
        self,
        symbol: str,
        since: Optional[int] = None,
        limit: Optional[int] = None
    ) -> list[Trade]:
        """获取成交记录"""
        exchange = self.get_exchange(symbol)
        return await exchange.fetch_trades(symbol, since, limit)

    async def watch_trades(self, symbol: str) -> list[Trade]:
        """订阅成交"""
        exchange = self.get_exchange(symbol)
        return await exchange.watch_trades(symbol)

    async def un_watch_trades(self, symbol: str):
        """取消订阅成交"""
        exchange = self.get_exchange(symbol)
        await exchange.un_watch_trades(symbol)

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = '1m',
        since: Optional[int] = None,
        limit: Optional[int] = None
    ) -> list[list[float]]:
        """
        获取 K 线数据

        Returns:
            [[timestamp, open, high, low, close, volume], ...]
        """
        exchange = self.get_exchange(symbol)
        return await exchange.fetch_ohlcv(symbol, timeframe, since, limit)

    async def watch_ohlcv(self, symbol: str, timeframe: str = '1m') -> list[list[float]]:
        """订阅 K 线"""
        exchange = self.get_exchange(symbol)
        return await exchange.watch_ohlcv(symbol, timeframe)

    async def un_watch_ohlcv(self, symbol: str, timeframe: str = '1m'):
        """取消订阅 K 线"""
        exchange = self.get_exchange(symbol)
        return await exchange.un_watch_ohlcv(symbol, timeframe)

    # ========== 交易方法 create/cancel/fetch/watch order ==========
    def _default_order_params(self) -> dict:
        """默认订单参数，子类可覆盖以添加交易所特定参数"""
        return {}

    async def __resolve_order(self, order_request: OrderRequest, market: MarketInterface) -> Optional[OrderRequest]:
        """将 OrderRequest 转换为内置的 OrderRequest，注意这里并没有处理contract size"""
        symbol = self.to_raw_symbol(order_request["symbol"])
        if market is None:
            self.logger.error("Symbol %s not found in markets", symbol)
            return None
        # 合并默认参数和请求参数
        default_params = self._default_order_params()
        if order_request.get("params", None) is None:
            order_request["params"] = default_params
        else:
            order_request["params"] = {**default_params, **order_request["params"]}
        # 精度处理
        precision = float(market["precision"]['amount'])  # another price
        aligned_amount = round_to_precision(order_request['amount'], precision)

        # 最小数量检查
        limit_amount_min = market['limits']['amount']['min'] or precision
        limit_price_min = market['limits']['price']['min'] or 0.0
        limit_cost_min = market["limits"]["cost"]["min"] or 0.0
        position_amount = (await self.get_positions_data()).get(symbol, 0.0)
        direction = 1 if order_request["side"] == "buy" else -1
        # reverse direction, 减仓
        if (position_amount * direction < -1e-9) or (market['type'] == 'spot' and order_request["side"] == "sell"):
            if abs(aligned_amount) < precision:
                self.logger.debug(
                    "Order rejected: reduce amount %.6f < precision %.6f",
                    abs(aligned_amount), precision
                )
                return None
            order_request["params"]["reduceOnly"] = True  # 减仓订单
        else:
            price = order_request.get("price", None)
            if abs(aligned_amount) < limit_amount_min:
                self.logger.debug(
                    "Order rejected: amount %.6f (original %.6f) < min %.6f",
                    abs(aligned_amount), order_request['amount'], limit_amount_min
                )
                return None
            elif price and (price < limit_price_min or price * abs(aligned_amount) < limit_cost_min):
                self.logger.debug(
                    "Order rejected: price %.2f < min %.2f or cost %.2f < min %.2f",
                    price, limit_price_min, price * abs(aligned_amount), limit_cost_min
                )
                return None
        order_request['amount'] = aligned_amount
        return order_request

    def __get_place_str(self, order_request: OrderRequest):
        price = order_request.get("price", None)
        aligned_amount = order_request['amount']
        side = order_request['side']
        symbol = order_request['symbol']
        if price is not None:
            price_str = f"{aligned_amount:.4f} x ${price:.2f} = ${aligned_amount * price:.2f}"
        else:
            price_str = f"{aligned_amount:.4f}"
        return f"create the {side} {symbol} {type} order {price_str}"

    async def create_order(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: Optional[float] = None,
        params: Optional[dict] = None,
    ) -> Optional[Order]:
        """
        下单

        Args:
            symbol: 交易对
            order_type: 'market' 或 'limit'
            side: 'buy' 或 'sell'
            amount: 数量, 已处理过合约大小缩放
            price: 价格（限价单必填）
            params: 额外参数

        Returns:
            订单信息
        """
        order_params: OrderRequest = {
            "symbol": symbol,
            "type": type,
            "side": side,
            "amount": amount,
            "price": price,
            "params": params,
        }

        # 插件钩子：允许插件阻止订单
        if pm.hook.on_order_creating(
            exchange=self, symbol=symbol, side=side, amount=amount, price=price
        ) is False:
            self.logger.info("Order blocked by plugin: %s %s %s", symbol, side, amount)
            return None
        market = (await self.get_markets_data()).get(symbol, None)
        resolved_order = await self.__resolve_order(order_params, market)
        if resolved_order is None:
            return None
        place_str = self.__get_place_str(resolved_order)
        # 下单
        if not self.config.debug:
            try:
                exchange = self.get_exchange(symbol)
                self.event.emit("order:creating", resolved_order)   # TODO: 可以记录order_params
                order = await exchange.create_order(
                    **resolved_order
                )
                if order is None or order['id'] is None:
                    raise InvalidOrder("Order response is invalid or missing order ID")
                if type == "market":  # 记录市价订单
                    await self._balances[market['type']].mark_dirty()
                    if market['type'] != "spot":
                        await self._positions.mark_dirty()  # 市价订单后标记持仓数据需要刷新
                self.event.emit("order:created", resolved_order, order)  # TODO: 可以记录order
                # 插件钩子：订单创建成功
                # pm.hook.on_order_created(exchange=self, order=order)
                self.logger.info("Successfully %s (id: %s)", place_str, order.get('id'))
                return order
            except (InvalidOrder, KeyError) as e:
                # 插件钩子：订单创建失败
                pm.hook.on_order_error(exchange=self, error=e, order_params=order_params)
                self.logger.exception("Failed to create order: %s", e)
                return None
        else:
            self.logger.info("Debug: %s", place_str)
            return None

    async def create_orders(self, order_params: list[OrderRequest]) -> list[Order]:
        """批量下单"""
        order_params_list = []
        for order_param in order_params:
            market = (await self.get_markets_data()).get(order_param["symbol"], None)
            resolved_order = await self.__resolve_order(order_param, market)
            if resolved_order is not None:
                order_params_list.append(resolved_order)
        if len(order_params_list) == 0:
            return []

        # Debug 模式：仅日志，不实际下单
        if self.config.debug:
            for order_param in order_params_list:
                place_str = self.__get_place_str(order_param)
                self.logger.info("Debug: %s", place_str)
            return []

        # only support swap for now
        try:
            for order_param in order_params:
                self.event.emit("order:creating", order_param)
            results = await self.exchanges['swap'].create_orders(order_params)
            for order_param, order in zip(order_params, results):
                place_str = self.__get_place_str(order)
                if order_param['type'] == "market":
                    await self._balances[market['type']].mark_dirty()
                    if market['type'] != "spot":
                        await self._positions.mark_dirty()
                self.event.emit("order:created", order_param, order)
                self.logger.info("Successfully %s (id: %s)", place_str, order.get('id'))
            return results
        except (InvalidOrder, KeyError) as e:
            self.logger.exception("Failed to create orders: %s", e)
            return []

    async def cancel_order(self, order_id: str, symbol: str) -> Order:
        """撤销订单"""
        exchange = self.get_exchange(symbol)
        self.event.emit("order:canceling", order_id, symbol)
        order = await exchange.cancel_order(order_id, symbol)
        self.event.emit("order:canceled", order_id, symbol, order)
        # 插件钩子：订单取消
        # pm.hook.on_order_cancelled(exchange=self, order=order)
        self.logger.info("Successfully canceled order %s for symbol %s", order_id, symbol)
        return order

    async def cancel_orders(self, orders: list[str], symbol: str) -> list[Order]:
        """批量撤销订单"""
        exchange = self.get_exchange(symbol)
        for order_id in orders:
            self.event.emit("order:canceling", order_id, symbol)
        results = await exchange.cancel_orders(orders, symbol)
        for order_id, order in zip(orders, results):
            self.event.emit("order:canceled", order_id, symbol, order)
            self.logger.info("Successfully canceled order %s for symbol %s", order_id, symbol)
        return results

    async def fetch_order(self, order_id: str, symbol: str = None) -> Order:
        """查询订单"""
        exchange = self.get_exchange(symbol)
        order = await exchange.fetch_order(order_id, symbol)
        self.event.emit("order:updated", order)
        return order

    async def fetch_open_orders(
        self,
        symbol: str,
        since: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[Order]:
        """查询未完成订单"""
        exchange = self.get_exchange(symbol)
        orders = await exchange.fetch_open_orders(symbol, since, limit)
        for order in orders:
            self.event.emit("order:updated", order)
        return orders

    async def fetch_closed_orders(
        self,
        symbol: str,
        since: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[Order]:
        """查询已完成订单"""
        exchange = self.get_exchange(symbol)
        orders = await exchange.fetch_closed_orders(symbol, since, limit)
        for order in orders:
            self.event.emit("order:updated", order)
        return orders

    async def watch_orders(self, ccxt_exchange_key: str) -> list[Order]:
        """订单更新"""
        exchange = self.exchanges[ccxt_exchange_key]
        orders = await exchange.watch_orders()
        for order in orders:
            self.event.emit("order:updated", order)
        self._positions.mark_dirty()  # 订单更新后仓位可能变化
        return orders

    async def un_watch_orders(self, ccxt_exchange_key: str):
        """取消订阅订单更新"""
        exchange = self.exchanges[ccxt_exchange_key]
        return await exchange.un_watch_orders()

    # ========== 账户方法 position/balance ==========
    stable_coins = {'USDT', 'USDG', 'USDC', 'BUSD', 'DAI', 'TUSD', 'USDP', 'USD',
                    'FDUSD', 'LDUSDT', 'BFUSD', 'RWUSD', 'USD1'}

    async def fetch_balance(self) -> dict:
        """
        获取账户余额（兼容接口）

        用于 `hft run test exchange ...` 等通用测试命令，保持与 ccxt 的 `fetch_balance` 命名一致。
        默认调用配置中的主 ccxt 实例；如需多账户（spot/swap）可在上层并行调用。
        """
        return await self.config.ccxt_instance.fetch_balance()

    def medal_balance_usd(self, data: dict) -> float:
        usd = 0.0
        for coin, amount in data.get('total', {}).items():
            if coin in self.stable_coins:
                usd += float(amount)
        return usd

    @AsyncTTL(time_to_live=30, maxsize=32)
    async def medal_fetch_balance_usd(self, ccxt_instance_key: str) -> float:
        exchange = self.exchanges[ccxt_instance_key]
        balance = await exchange.fetch_balance()
        await self.medal_cache_balance(ccxt_instance_key, self._transform_balance(balance))
        return self.medal_balance_usd(balance)

    @AsyncTTL(time_to_live=30, maxsize=32)
    async def medal_fetch_total_balance_usd(self) -> float:
        # 这个放法只是粗略地估算账户的 USD 价值，并且只利用了稳定币的信息，应该使用平台特定的比较准确
        if self.unified_account:
            balance = await self.config.ccxt_instance.fetch_balance()
            return self.medal_balance_usd(balance)
        else:
            balances = await self.fetch_parrallel('fetch_balance')
            return sum([self.medal_balance_usd(balance) for balance in balances])

    def _transform_balance(self, balance: Balances) -> dict[str, Balance]:
        return {asset: info for asset, info in balance.items() if asset.isupper()}

    async def medal_cache_balance(self, ccxt_instance_key: str, balance: dict[str, Balance]):
        await self._balances[ccxt_instance_key].update(balance)
        # 插件钩子：余额更新
        pm.hook.on_balance_update(exchange=self, account=ccxt_instance_key, balance=balance)
        return balance

    async def medal_fetch_balance_internal(self, ccxt_instance_key: str) -> dict[str, Balance]:
        exchange = self.exchanges[ccxt_instance_key]
        balance = self._transform_balance(await exchange.fetch_balance())
        pm.hook.on_balance_update(exchange=self, account=ccxt_instance_key, balance=balance)
        return balance, time.time()

    async def medal_fetch_balance(self, ccxt_instance_key: str) -> dict[str, Balance]:
        async def data_fn():
            return await self.medal_fetch_balance_internal(ccxt_instance_key)
        return await self._balances[ccxt_instance_key].get_data_or_update_by_func(data_fn)

    async def medal_watch_balance(self, ccxt_instance_key: str) -> dict[str, Balance]:
        """订阅余额更新"""
        exchange = self.exchanges[ccxt_instance_key]
        balance = await exchange.watch_balance()
        return await self.medal_cache_balance(ccxt_instance_key, balance)

    async def fetch_position(self, symbol: str) -> list[Position]:
        exchange = self.get_exchange(symbol)
        return await exchange.fetch_position(symbol)

    async def fetch_positions(self) -> list[Position]:
        """
        获取原始持仓数据

        Returns:
            [
                {
                    'symbol': 'BTC/USDT:USDT',
                    'side': 'long',
                    'contracts': 0.5,
                    'entryPrice': 27000.00,
                    'unrealizedPnl': 100.00,
                    'leverage': 10,
                    ...
                },
                ...
            ]
        """
        return await self.exchanges["swap"].fetch_positions()

    def _transform_positions(self, positions: list[Position]) -> dict[str, float]:
        """将原始持仓数据转换为 {symbol: amount} 格式"""
        result = defaultdict(float)
        for position in positions:
            symbol = position['symbol']
            amount = abs(float(position['contracts']) * self.get_contract_size(symbol))
            direction = -1 if position['side'] == 'short' else 1
            result[symbol] += amount * direction
        return dict(result)

    async def medal_fetch_positions_internal(self) -> tuple[dict[str, float], float]:
        """内部方法：获取并转换持仓数据（用于 HealthyData）"""
        positions = self._transform_positions(await self.fetch_positions())
        pm.hook.on_position_update(exchange=self, account="swap", positions=positions)
        return positions, time.time()

    async def medal_cache_positions(self, positions: dict[str, float]) -> dict[str, float]:
        """缓存持仓数据（供 watch/fetch 调用）"""
        await self._positions.update(positions)
        # 插件钩子：持仓更新
        pm.hook.on_position_update(exchange=self, account="swap", positions=positions)
        return positions

    async def medal_fetch_positions(self) -> dict[str, float]:
        """获取持仓（优先使用缓存，过期或 dirty 时自动刷新）"""
        return await self._positions.get_data_or_update_by_func(
            self.medal_fetch_positions_internal
        )

    async def watch_position(self, symbol: str) -> list[Position]:
        """订阅持仓更新"""
        exchange = self.get_exchange(symbol)
        return await exchange.watch_position(symbol)

    async def un_watch_position(self, symbol: str):
        """取消订阅持仓更新"""
        exchange = self.get_exchange(symbol)
        return await exchange.un_watch_positions([symbol])

    async def watch_positions(self) -> list[Trade]:
        """订阅持仓更新"""
        exchange = self.exchanges["swap"]
        return await exchange.watch_positions()

    async def un_watch_positions(self):
        """取消订阅持仓更新"""
        exchange = self.exchanges["swap"]
        return await exchange.un_watch_positions()

    async def medal_watch_positions(self) -> dict[str, float]:
        positions = self._transform_positions(await self.watch_positions())
        return await self.medal_cache_positions(positions)

    async def fetch_my_trades(
        self,
        symbol: str,
        since: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[Trade]:
        """获取我的成交记录"""
        exchange = self.get_exchange(symbol)
        return await exchange.fetch_my_trades(symbol, since, limit)

    # ========== 期货初始化方法 ==========

    async def set_leverage(self, symbol: str, leverage: int):
        """设置杠杆"""
        exchange = self.get_exchange(symbol)
        if self._markets[symbol]['type'] in ('swap', 'future'):  # 仅合约市场支持杠杆设置
            await exchange.set_leverage(leverage, symbol)

    async def set_margin_mode(self, symbol: str, margin_mode: str):
        """
        设置保证金模式

        Args:
            symbol: 交易对
            margin_mode: 'cross' 或 'isolated'
        """
        exchange = self.get_exchange(symbol)
        if self._markets[symbol]['type'] in ('swap', 'future'):  # 仅合约市场支持保证金模式设置
            await exchange.set_margin_mode(margin_mode, symbol)

    async def set_leverage_and_cross_margin_mode(self, symbol: str, leverage: int):
        await self.set_margin_mode(symbol, 'CROSSED')
        await self.set_leverage(symbol, leverage)

    @AsyncTTL(time_to_live=24 * 3600, maxsize=1024)
    async def medal_initialize_symbol(self, symbol: str) -> None:
        """初始化交易对（设置杠杆和保证金模式）"""
        self.get_exchange(symbol)  # 确保交易所实例存在
        symbol_info = self._markets[symbol]
        if symbol_info["type"] in ("future", "swap"):
            max_leverage = symbol_info['limits']['leverage']['max'] or 125
            target_leverage = min(self.config.leverage or 10, max_leverage)
            for _ in range(3):
                try:
                    if target_leverage <= 1:
                        return  # 不设置杠杆
                    await self.set_leverage_and_cross_margin_mode(symbol, target_leverage)
                    self.logger.info("Initialized %s with X%d leverage", symbol, target_leverage)
                    break
                except Exception as e:
                    self.logger.warning("Failed to initialize %s with X%d leverage: %s", symbol, target_leverage, e)
                    target_leverage = int(target_leverage // 2)

    # ========== 资金费率方法 ==========
    async def fetch_funding_rate(self, symbol: str) -> dict:
        """获取资金费率"""
        exchange = self.get_exchange(symbol)
        return await exchange.fetch_funding_rate(symbol)

    async def fetch_funding_rate_history(
        self,
        symbol: str,
        since: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list:
        """获取历史资金费率"""
        exchange = self.get_exchange(symbol)
        return await exchange.fetch_funding_rate_history(symbol, since, limit)

    @abstractmethod
    async def medal_fetch_funding_rates(self) -> dict[str, FundingRate]:
        """
        获取所有交易对的资金费率
        子类应该覆盖此方法
        """

    @abstractmethod
    async def medal_fetch_funding_rates_history(self) -> list[FundingRateBill]:
        """
        获取资金费率账单
        子类应该覆盖此方法
        """

    # ========== 转账方法 ==========
    @AsyncTTL(time_to_live=60, maxsize=32)
    async def medal_fetch_deposit_address(self, currency: str, network: str):
        result = await self.exchanges['spot'].fetch_deposit_address(currency, {'network': network})
        return result['address']

    async def transfer(
        self,
        currency: str,
        amount: float,
        from_account: str,
        to_account: str,
    ) -> dict:
        """
        内部转账

        Args:
            currency: 币种
            amount: 数量
            from_account: 来源账户 ('spot', 'swap', 'future')
            to_account: 目标账户
        """
        exchange = self.exchanges[from_account]
        await exchange.transfer(currency, amount, from_account, to_account)

    def get_currency_networks(self, currency: str) -> dict[str, dict]:
        """
        获取币种的所有充值/提币网络信息

        Args:
            currency: 币种代码（如 'USDT', 'BTC'）

        Returns:
            {network_id: {
                "id": str,
                "network": str,
                "name": str,
                "fee": float,
                "active": bool,
                "deposit": bool,   # 是否支持充值
                "withdraw": bool,  # 是否支持提币
            }}
        """
        currency_info = self._currencies.get(currency, {})
        networks = currency_info.get("networks", {})

        return {
            k: {
                "id": v.get("id"),
                "network": v.get("network"),
                "name": v.get("name"),
                "fee": v.get("fee", 0.0),
                "active": v.get("active", False),
                "deposit": v.get("deposit", False),
                "withdraw": v.get("withdraw", False),
            }
            for k, v in networks.items()
        }

    def get_withdraw_networks(self, currency: str) -> dict[str, dict]:
        """
        获取币种可提币的网络列表

        Returns:
            {network_id: network_info} 只包含 active=True 且 withdraw=True 的网络
        """
        networks = self.get_currency_networks(currency)
        return {
            k: v for k, v in networks.items()
            if v.get("active") and v.get("withdraw")
        }

    def get_deposit_networks(self, currency: str) -> dict[str, dict]:
        """
        获取币种可充值的网络列表

        Returns:
            {network_id: network_info} 只包含 active=True 且 deposit=True 的网络
        """
        networks = self.get_currency_networks(currency)
        return {
            k: v for k, v in networks.items()
            if v.get("active") and v.get("deposit")
        }

    async def medal_auto_deposit(
        self,
        to_exchange: "BaseExchange",
        currency: str,
        amount: float,
        network: str = "auto",
        timeout: float = 900.0,  # 15 minutes
        check_interval: float = 10.0,
    ) -> dict:
        """
        自动链上提币到目标交易所

        自动选择最优网络（费用最低），发起提币，等待到账。

        Args:
            to_exchange: 目标交易所实例
            currency: 币种代码（如 'USDT'）
            amount: 提币数量
            network: 网络（'auto' 自动选择费用最低的）
            timeout: 等待到账超时时间（秒），默认 15 分钟
            check_interval: 检查间隔（秒）

        Returns:
            提币结果 dict

        Raises:
            ValueError: 参数无效或条件不满足
            TimeoutError: 等待到账超时
        """
        # 获取通知服务（如果可用）
        notify = getattr(self.root, 'notify', None)

        # 1. 检查现货余额
        spot_balance = self._balances.get('spot', {}).get(currency, {})
        available = spot_balance.get('free', 0.0)
        if available < amount:
            # 发送余额不足通知
            if notify:
                await notify.notify_insufficient_balance(
                    self.name, currency, available, amount
                )
            raise ValueError(
                f"Insufficient {currency} balance: available={available}, required={amount}"
            )

        # 2. 获取可用网络
        my_withdraw_networks = self.get_withdraw_networks(currency)
        target_deposit_networks = to_exchange.get_deposit_networks(currency)

        if not my_withdraw_networks:
            raise ValueError(f"No withdraw networks available for {currency} on {self.name}")
        if not target_deposit_networks:
            raise ValueError(f"No deposit networks available for {currency} on {to_exchange.name}")

        # 3. 获取目标交易所白名单地址
        white_addresses = to_exchange.config.white_deposit_addresses
        if not white_addresses:
            raise ValueError(
                f"No white_deposit_addresses configured for {to_exchange.name}. "
                "Please configure deposit addresses in exchange config."
            )

        # 4. 选择网络
        if network == "auto":
            # 找出双方都支持且在白名单中的网络
            candidates = []
            for addr_config in white_addresses:
                addr_network = addr_config.network
                address = addr_config.address

                # '*' 匹配所有网络
                if addr_network == '*':
                    # 找所有双方都支持的网络
                    for net_id in my_withdraw_networks:
                        if net_id in target_deposit_networks:
                            fee = my_withdraw_networks[net_id].get("fee", float('inf'))
                            candidates.append((net_id, address, fee))
                else:
                    # 检查特定网络是否双方都支持
                    if addr_network in my_withdraw_networks and addr_network in target_deposit_networks:
                        fee = my_withdraw_networks[addr_network].get("fee", float('inf'))
                        candidates.append((addr_network, address, fee))

            if not candidates:
                raise ValueError(
                    f"No common network available for {currency} between "
                    f"{self.name} (withdraw) and {to_exchange.name} (deposit)"
                )

            # 选择费用最低的
            candidates.sort(key=lambda x: x[2])
            selected_network, deposit_address, fee = candidates[0]
            self.logger.info(
                "Auto-selected network %s for %s (fee=%.6f)",
                selected_network, currency, fee
            )
        else:
            # 使用指定网络
            selected_network = network
            if selected_network not in my_withdraw_networks:
                raise ValueError(
                    f"Network {selected_network} not available for withdraw on {self.name}"
                )
            if selected_network not in target_deposit_networks:
                raise ValueError(
                    f"Network {selected_network} not available for deposit on {to_exchange.name}"
                )

            # 查找白名单中的地址
            deposit_address = None
            for addr_config in white_addresses:
                if addr_config.network == '*' or addr_config.network == selected_network:
                    deposit_address = addr_config.address
                    break

            if not deposit_address:
                raise ValueError(
                    f"No whitelist address for network {selected_network} on {to_exchange.name}"
                )

        # 5. 记录目标交易所初始余额
        target_spot_balance = to_exchange._balances.get('spot', {}).get(currency, {})
        initial_balance = target_spot_balance.get('total', 0.0)

        # 6. 发起提币
        self.logger.info(
            "Withdrawing %.6f %s to %s via %s (address: %s...)",
            amount, currency, to_exchange.name, selected_network, deposit_address[:10]
        )

        spot_exchange = self.exchanges.get('spot')
        if not spot_exchange:
            raise ValueError(f"Spot exchange not available on {self.name}")

        withdraw_result = await spot_exchange.withdraw(
            currency,
            amount,
            deposit_address,
            None,  # tag
            {"network": selected_network}
        )

        self.logger.info("Withdraw initiated: %s", withdraw_result.get('id', 'unknown'))

        # 7. 等待到账
        start_time = asyncio.get_event_loop().time()
        withdraw_id = withdraw_result.get('id', 'unknown')

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                # 发送超时通知
                if notify:
                    await notify.notify_deposit_timeout(
                        self.name, to_exchange.name, currency,
                        amount, withdraw_id, timeout
                    )
                raise TimeoutError(
                    f"Deposit not received after {timeout}s. "
                    f"Withdraw ID: {withdraw_id}"
                )

            await asyncio.sleep(check_interval)

            # 刷新目标交易所余额
            await to_exchange.medal_fetch_balance('spot')

            target_spot_balance = to_exchange._balances.get('spot', {}).get(currency, {})
            current_balance = target_spot_balance.get('total', 0.0)

            # 检查余额是否增加（考虑手续费，至少增加 50%）
            expected_increase = amount * 0.5
            if current_balance >= initial_balance + expected_increase:
                actual_received = current_balance - initial_balance
                self.logger.info(
                    "Deposit received: %.6f %s (expected: %.6f)",
                    actual_received, currency, amount
                )
                # 发送成功通知
                if notify:
                    await notify.notify_deposit_success(
                        self.name, to_exchange.name, currency,
                        amount, actual_received
                    )
                withdraw_result['received_amount'] = actual_received
                return withdraw_result

            self.logger.debug(
                "Waiting for deposit... elapsed=%.0fs, balance=%.6f (initial=%.6f)",
                elapsed, current_balance, initial_balance
            )

    # ========== WebSocket 方法 ==========
    async def watch_balance(self, ccxt_exchange_key: str) -> dict:
        """订阅余额更新"""
        exchange = self.exchanges[ccxt_exchange_key]
        return await exchange.watch_balance()

    # ========== 工具方法 ==========
    async def fetch_parrallel(self, method: str, *args, **kwargs) -> list:
        tasks = [getattr(exchange, method)(*args, **kwargs) for exchange in list(self.exchanges.values())]
        results = await asyncio.gather(*tasks)
        return results

    @AsyncTTL(time_to_live=300, maxsize=128)
    async def load_time_diff(self) -> None:
        """加载时间差"""
        await self.fetch_parrallel('load_time_difference')

    async def load_markets_internal(self, reload: bool = True) -> tuple[dict[str, MarketInterface], float]:
        """加载市场信息"""
        markets = {}
        markets_list = await self.fetch_parrallel('load_markets', reload)
        for market_dict in markets_list:
            for symbol, market in market_dict.items():
                try:
                    trade_type = TradeType(market['type'])
                    match trade_type:
                        case TradeType.SPOT:
                            if "spot" not in self.config.support_types:
                                continue
                        case TradeType.SWAP | TradeType.FUTURE:
                            if "swap" not in self.config.support_types:
                                continue
                        case TradeType.OPTION:
                            continue  # TODO: 期权暂不支持
                        case _:
                            self.logger.warning("Unsupported trade %s with trade type: %s", symbol, trade_type)
                            continue
                    markets[symbol] = market
                except Exception as e:
                    self.logger.warning("Failed to parse market: %s", e, exc_info=True)
        return markets, time.time()

    @AsyncTTL(time_to_live=300, maxsize=128)
    async def load_markets(self, reload: bool = True) -> dict[str, MarketInterface]:
        markets, timestamp = await self.load_markets_internal(reload)
        await self._markets.update(markets, timestamp)
        return markets

    async def fetch_currencies_internal(self) -> tuple[dict[str, CurrencyInterface], float]:
        """内部方法：获取币种信息"""
        currencies: dict[str, CurrencyInterface] = await self.config.ccxt_instance.fetch_currencies()
        return currencies, time.time()

    @AsyncTTL(time_to_live=30, maxsize=32)
    async def fetch_currencies(self) -> dict[str, CurrencyInterface]:  # 主要用于判断某个币种是否支持转账
        currencies, timestamp = await self.fetch_currencies_internal()
        await self._currencies.update(currencies, timestamp)
        return self._currencies

    async def open(self) -> None:
        """打开连接"""
        for exchange in list(self.exchanges.values()):
            exchange.open()
            await asyncio.sleep(0.0)  # 仅让出控制权

    async def close(self) -> None:
        """关闭连接"""
        tasks = [exchange.close() for exchange in list(self.exchanges.values())]
        await asyncio.gather(*tasks)

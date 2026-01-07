import time
import asyncio
from typing import TYPE_CHECKING
from ccxt.base.errors import UnsubscribeError
from ..core.listener import Listener
from ..data.database import OrderBillController
from ..data.listeners import DataListener
if TYPE_CHECKING:
    from ..exchange.base import BaseExchange


class ExchangeOrderBillWatchListener(DataListener):

    def __init__(self, ccxt_instance_key: str, exchange: 'BaseExchange', interval=0.1):
        super().__init__(interval)
        self.exchange = exchange
        self.ccxt_instance_key = ccxt_instance_key
        self.name = f"{self.name}-{ccxt_instance_key}"

    async def on_tick(self):
        if not self.exchange.ready or not self.db:
            return
        try:
            order_lists = await asyncio.wait_for(self.exchange.watch_orders(self.ccxt_instance_key), timeout=900)
            controller = OrderBillController(self.db)
            await controller.update(order_lists, self.exchange)
        except asyncio.TimeoutError:
            self.logger.debug("Watch orders timeout for exchange %s", self.exchange.name)
            return

    async def on_stop(self):
        # 停止时取消所有挂单监听
        try:
            pass
            # await self.exchange.un_watch_orders(self.ccxt_instance_key)
            # current not supported in ccxt
        except UnsubscribeError:  # 可能已经取消订阅
            pass
        await super().on_stop()


class ExchangeOrderBillFetchListener(DataListener):
    def __init__(self, exchange: 'BaseExchange', interval=60.0):
        super().__init__(interval)
        self.exchange = exchange

    async def on_tick(self):
        if not self.exchange.ready or not self.db:
            return
        controller = OrderBillController(self.db)
        for order_id, symbol in await controller.get_should_updated_orders(self.exchange):
            order = await self.exchange.fetch_order(order_id, symbol)
            if order['status'] not in ["closed", "canceled", "expired", "rejected"]:  # cancel if too old
                if order['lastTradeTimestamp'] is not None:
                    order_timestamp = float(order['lastTradeTimestamp']) / 1000.0
                    if time.time() - order_timestamp > 600:
                        await self.exchange.cancel_order(order['id'], order['symbol'])
                        self.logger.info("Cancel old open %s order %s:%s", order['side'], order['symbol'], order['id'])
            await controller.update([order], self.exchange)
            await asyncio.sleep(1)  # 避免请求过快


class ExchangeOrderBillListener(DataListener):

    __pickle_exclude__ = (*DataListener.__pickle_exclude__, "_children", "children")

    async def on_start(self):
        await super().on_start()
        exchange: 'BaseExchange' = self.parent
        for ccxt_instance_key in list(exchange.config.ccxt_instances.keys()):
            watch_listener = ExchangeOrderBillWatchListener(ccxt_instance_key, exchange)
            self.add_child(watch_listener)
            await watch_listener.start()
        fetch_listener = ExchangeOrderBillFetchListener(exchange)
        self.add_child(fetch_listener)
        await fetch_listener.start()
        exchange.event.add_listener("order_created", self.on_order_created)

    async def on_order_created(self, resolved_order, order):
        exchange: 'BaseExchange' = self.parent
        if self.db:
            controller = OrderBillController(self.db)
            await controller.update(order, exchange)

    def on_save(self):
        d = super().on_save()
        d["_children"] = {}
        return d

    async def on_tick(self):
        pass  # TODO: fetch open orders periodically

    async def on_stop(self):
        exchange: 'BaseExchange' = self.parent
        if exchange is not None:
            exchange.event.remove_listener("order_created", self.on_order_created)
        for child in list(self.children.values()):
            await child.stop()
            self.remove_child(child.name)

        await super().on_stop()


class ExchangePositionWatchListener(Listener):
    """
    交易所持仓监听器

    定期从交易所获取持仓信息并更新到 Exchange 实例中。
    """

    def __init__(self, exchange: 'BaseExchange', interval=0.1):
        super().__init__(self.__class__.__name__, interval)
        self.exchange = exchange

    async def on_tick(self) -> None:
        """获取并更新持仓信息"""
        if not self.exchange.ready:
            return
        await self.exchange.medal_watch_positions()

    async def on_stop(self):
        """停止时取消持仓订阅"""
        try:
            # await self.exchange.un_watch_positions()
            pass  # current not supported in ccxt
        except UnsubscribeError:
            pass
        await super().on_stop()


class ExchangePositionListener(Listener):

    __pickle_exclude__ = (*Listener.__pickle_exclude__, "_children", "children")

    def __init__(self, interval=1):
        super().__init__(self.__class__.__name__, interval)

    def on_save(self):
        d = super().on_save()
        d["_children"] = {}
        return d

    async def on_start(self):
        await super().on_start()
        exchange: 'BaseExchange' = self.parent
        position_listener = ExchangePositionWatchListener(exchange)
        self.add_child(position_listener)
        await position_listener.start()

    async def on_tick(self):
        exchange: 'BaseExchange' = self.parent
        await exchange.medal_fetch_positions()

    async def on_stop(self):
        for child in list(self.children.values()):
            await child.stop()
            self.remove_child(child.name)
        await super().on_stop()


class ExchangeBalanceWatchListener(Listener):
    def __init__(self, ccxt_instance_key: str, exchange: 'BaseExchange', interval=1):
        super().__init__(f"{self.__class__.__name__}-{ccxt_instance_key}", interval)
        self.ccxt_instance_key = ccxt_instance_key
        self.exchange = exchange

    async def on_tick(self) -> None:
        """获取并更新余额信息"""
        if not self.exchange.ready:
            return
        await self.exchange.medal_watch_balance(self.ccxt_instance_key)


class ExchangeBalanceFetchListener(Listener):
    def __init__(self, ccxt_instance_key: str, exchange: 'BaseExchange', interval=5):
        super().__init__(f"{self.__class__.__name__}-{ccxt_instance_key}", interval)
        self.ccxt_instance_key = ccxt_instance_key
        self.exchange = exchange

    async def on_tick(self) -> None:
        """获取并更新余额信息"""
        if not self.exchange.ready:
            return
        await self.exchange.medal_fetch_balance(self.ccxt_instance_key)


class ExchangeBalanceListener(Listener):
    """
    交易所余额监听器

    定期从交易所获取余额信息并更新到 Exchange 实例中。
    """
    __pickle_exclude__ = (*Listener.__pickle_exclude__, "_children", "children")

    def __init__(self):
        super().__init__(self.__class__.__name__, 60)

    def on_save(self):
        d = super().on_save()
        d["_children"] = {}
        return d

    async def on_start(self):
        await super().on_start()
        exchange: 'BaseExchange' = self.parent
        for ccxt_instance_key in list(exchange.config.ccxt_instances.keys()):
            watch_listener = ExchangeBalanceWatchListener(ccxt_instance_key, exchange)
            self.add_child(watch_listener)
            await watch_listener.start()
            fetch_listener = ExchangeBalanceFetchListener(ccxt_instance_key, exchange)
            self.add_child(fetch_listener)
            await fetch_listener.start()
    
    async def on_stop(self):
        for child in list(self.children.values()):
            await child.stop()
            self.remove_child(child.name)
        await super().on_stop()

    async def on_tick(self) -> None:
        """获取并更新余额信息"""



import time
import asyncio
from typing import TYPE_CHECKING
from ccxt.base.errors import UnsubscribeError
from ..core.listener import Listener, GroupListener
from ..database.client import OrderBillController
from ..database.listeners import DataListener
if TYPE_CHECKING:
    from ..exchange.base import BaseExchange


class ExchangeOrderBillWatchListener(DataListener):
    persist_key = "order_bill"

    def __init__(self, name: str, ccxt_instance_key: str, interval: float = 0.1):
        super().__init__(interval)
        self.name = name  # 使用传入的 name，与 sync_children_params 的 key 一致
        self.ccxt_instance_key = ccxt_instance_key

    @property
    def exchange(self) -> 'BaseExchange | None':
        """通过树形结构获取 exchange（parent.parent）"""
        if self.parent is None or self.parent.parent is None:
            return None
        return self.parent.parent

    async def on_tick(self):
        if self.exchange is None or not self.exchange.ready or not self.db_ready or not self.persist_enabled:
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
    persist_key = "order_bill"

    def __init__(self, name: str, interval: float = 60.0):
        super().__init__(interval)
        self.name = name  # 使用传入的 name，与 sync_children_params 的 key 一致

    @property
    def exchange(self) -> 'BaseExchange | None':
        """通过树形结构获取 exchange（parent.parent）"""
        if self.parent is None or self.parent.parent is None:
            return None
        return self.parent.parent

    async def on_tick(self):
        if self.exchange is None or not self.exchange.ready or not self.db_ready or not self.persist_enabled:
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


class ExchangeOrderBillListener(GroupListener, DataListener):
    """
    交易所订单账单监听器

    使用 GroupListener 自动管理动态子节点。
    """
    persist_key = "order_bill"

    def sync_children_params(self) -> dict[str, any]:
        """根据 ccxt_instances 声明需要的 children"""
        exchange: 'BaseExchange' = self.parent
        if not exchange:
            return {}
        params = {}
        for key in exchange.config.ccxt_instances.keys():
            params[f"watch-{key}"] = {"key": key, "type": "watch"}
        params["fetch"] = {"type": "fetch"}
        return params

    def create_dynamic_child(self, name: str, param: any) -> Listener:
        """根据参数创建 WatchListener 或 FetchListener"""
        if param["type"] == "watch":
            return ExchangeOrderBillWatchListener(name=name, ccxt_instance_key=param["key"])
        else:
            return ExchangeOrderBillFetchListener(name=name)

    async def on_start(self):
        await super().on_start()
        exchange: 'BaseExchange' = self.parent
        exchange.event.add_listener("order_created", self.on_order_created)

    async def on_order_created(self, resolved_order, order):
        exchange: 'BaseExchange' = self.parent
        if self.db_ready and self.persist_enabled:
            controller = OrderBillController(self.db)
            await controller.update(order, exchange)

    async def on_stop(self):
        exchange: 'BaseExchange' = self.parent
        if exchange is not None:
            exchange.event.remove_listener("order_created", self.on_order_created)
        await super().on_stop()


class ExchangePositionWatchListener(Listener):
    """
    交易所持仓监听器

    定期从交易所获取持仓信息并更新到 Exchange 实例中。
    """

    def __init__(self, name: str = None, interval: float = 0.1):
        super().__init__(name or self.__class__.__name__, interval)

    @property
    def exchange(self) -> 'BaseExchange | None':
        """通过树形结构获取 exchange（parent.parent）"""
        if self.parent is None or self.parent.parent is None:
            return None
        return self.parent.parent

    async def on_tick(self) -> None:
        """获取并更新持仓信息"""
        if self.exchange is None or not self.exchange.ready:
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


class ExchangePositionListener(GroupListener):
    """
    交易所持仓监听器

    使用 GroupListener 自动管理动态子节点。
    """

    def __init__(self, interval=1):
        super().__init__(self.__class__.__name__, interval)

    def sync_children_params(self) -> dict[str, any]:
        """声明需要一个 watch listener"""
        return {"watch": {}}

    def create_dynamic_child(self, name: str, param: any) -> Listener:
        """创建 PositionWatchListener"""
        return ExchangePositionWatchListener(name=name)

    async def on_tick(self):
        await super().on_tick()  # 调用 GroupListener 的同步逻辑
        exchange: 'BaseExchange' = self.parent
        await exchange.medal_fetch_positions()
        return False


class ExchangeBalanceWatchListener(Listener):
    def __init__(self, name: str, ccxt_instance_key: str, interval: float = 1.0):
        super().__init__(name, interval)  # 使用传入的 name
        self.ccxt_instance_key = ccxt_instance_key

    @property
    def exchange(self) -> 'BaseExchange | None':
        """通过树形结构获取 exchange（parent.parent）"""
        if self.parent is None or self.parent.parent is None:
            return None
        return self.parent.parent

    async def on_tick(self) -> None:
        """获取并更新余额信息"""
        if self.exchange is None or not self.exchange.ready:
            return
        await self.exchange.medal_watch_balance(self.ccxt_instance_key)


class ExchangeBalanceFetchListener(Listener):
    def __init__(self, name: str, ccxt_instance_key: str, interval: float = 5.0):
        super().__init__(name, interval)  # 使用传入的 name
        self.ccxt_instance_key = ccxt_instance_key

    @property
    def exchange(self) -> 'BaseExchange | None':
        """通过树形结构获取 exchange（parent.parent）"""
        if self.parent is None or self.parent.parent is None:
            return None
        return self.parent.parent

    async def on_tick(self) -> None:
        """获取并更新余额信息"""
        if self.exchange is None or not self.exchange.ready:
            return
        await self.exchange.medal_fetch_balance(self.ccxt_instance_key)


class ExchangeBalanceListener(GroupListener):
    """
    交易所余额监听器

    定期从交易所获取余额信息并更新到 Exchange 实例中。
    使用 GroupListener 自动管理动态子节点。
    """

    def __init__(self):
        super().__init__(self.__class__.__name__, 60)

    def sync_children_params(self) -> dict[str, any]:
        """根据 ccxt_instances 声明需要的 children"""
        exchange: 'BaseExchange' = self.parent
        if not exchange:
            return {}
        params = {}
        for key in exchange.config.ccxt_instances.keys():
            params[f"watch-{key}"] = {"key": key, "type": "watch"}
            params[f"fetch-{key}"] = {"key": key, "type": "fetch"}
        return params

    def create_dynamic_child(self, name: str, param: any) -> Listener:
        """根据参数创建 WatchListener 或 FetchListener"""
        if param["type"] == "watch":
            return ExchangeBalanceWatchListener(name=name, ccxt_instance_key=param["key"])
        else:
            return ExchangeBalanceFetchListener(name=name, ccxt_instance_key=param["key"])



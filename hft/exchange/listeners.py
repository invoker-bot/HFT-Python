import asyncio
import time
from functools import cached_property
from datetime import timedelta
from typing import TYPE_CHECKING, Any
# from ccxt.base.errors import UnsubscribeError
from ..core.duration import parse_duration
from ..core.listener import GroupListener, Listener, ListenerState
from ..database.controllers import ExchangeStateController, OrderBillController, FundingRateBillController
# from ..plugin import pm

if TYPE_CHECKING:
    from ..exchange.base import BaseExchange


class CCXTExchangeGroupListener(GroupListener):

    def sync_children_params(self):
        exchange: 'BaseExchange' = self.parent
        return exchange.exchanges

    @property
    def exchange(self) -> 'BaseExchange':
        """通过树形结构获取 exchange"""
        return self.parent

class CCXTExchangeOrderBillWatchListener(Listener):
    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self._should_stop = False

    @property
    def interval(self) -> float:
        return 0.1  # watch 频率支持较高

    @property
    def exchange(self) -> 'BaseExchange':
        """通过树形结构获取 exchange"""
        return self.parent.exchange

    async def on_tick(self):
        if not self.exchange.ready:
            return
        # 使用 wait_for 配合超时，定期检查停止标志
        try:
            order_lists = await asyncio.wait_for(
                self.exchange.watch_orders(self.name),
                timeout=30.0  # 30秒超时，定期检查是否需要停止
            )
        except asyncio.TimeoutError:
            # 超时后检查是否需要停止
            if self._should_stop or self.state != ListenerState.RUNNING:
                raise asyncio.CancelledError()  # 主动退出
            return  # 继续下一轮
        except asyncio.CancelledError:
            self.logger.debug("watch_orders cancelled for: %s", self.name)
            raise

        db = self.root.database
        if db is not None:
            controller = db.get_controller(OrderBillController)
            await controller.update(order_lists, self.exchange)

    async def on_stop(self):
        self._should_stop = True
        await super().on_stop()


class CCXTExchangeOrderBillListener(Listener):
    __pickle_exclude__ = {*Listener.__pickle_exclude__, "auto_tracking_orders_after",
                          "auto_tracking_orders_before", "auto_cancel_orders_after" }

    @property
    def interval(self) -> float:
        return 10.0

    @property
    def exchange(self) -> 'BaseExchange | None':
        """通过树形结构获取 exchange（parent.parent）"""
        return self.parent.exchange

    @cached_property
    def auto_tracking_orders_after(self) -> timedelta:
        return timedelta(seconds=parse_duration(self.exchange.config.auto_tracking_orders_after))

    @cached_property
    def auto_tracking_orders_before(self) -> timedelta:
        return timedelta(seconds=parse_duration(self.exchange.config.auto_tracking_orders_before))

    @cached_property
    def auto_cancel_orders_after(self) -> timedelta:
        return timedelta(seconds=parse_duration(self.exchange.config.auto_cancel_orders_after))

    async def on_tick(self):
        if not self.exchange.ready:
            return
        db = self.root.database
        if db is None:
            return
        controller = db.get_controller(OrderBillController)
        for order_id, symbol in await controller.get_should_updated_orders(self.exchange,
                                                                           (self.auto_cancel_orders_after,
                                                                            self.auto_tracking_orders_before)):
            try:
                order = await asyncio.wait_for(
                    self.exchange.fetch_order(order_id, symbol),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                self.logger.warning("Timeout fetching order %s:%s", symbol, order_id)
                if self.state != ListenerState.RUNNING:  # 可能在等待订单更新时被停止，停止后不继续处理剩余订单
                    break
                continue
            if order['status'] not in ["closed", "canceled", "expired", "rejected"]:  # cancel if too old
                if order['lastTradeTimestamp'] is not None:
                    order_timestamp = float(order['lastTradeTimestamp']) / 1000.0
                    if time.time() - order_timestamp > self.auto_cancel_orders_after.total_seconds():
                        try:
                            await asyncio.wait_for(
                                self.exchange.cancel_order(order['id'], order['symbol']),
                                timeout=5.0
                            )
                            self.logger.info("Cancel old open %s order %s:%s", order['side'], order['symbol'], order['id'])
                        except asyncio.TimeoutError:
                            self.logger.warning("Timeout canceling order %s:%s", order['symbol'], order['id'])
            await controller.update([order], self.exchange)
            if self.state != ListenerState.RUNNING:  # 可能在等待订单更新时被停止，停止后不继续处理剩余订单
                break
            await asyncio.sleep(1)  # 避免请求过快

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self.root.factory.get_or_create(
            CCXTExchangeOrderBillWatchListener,
            name=self.name,  # 使用相同名称，与 sync_children_params 的 key 一致
            parent=self
        )

class ExchangeOrderBillListener(CCXTExchangeGroupListener):
    """
    交易所订单账单监听器

    """
    def create_dynamic_child(self, name: str, param: any) -> Listener:
        """根据参数创建 WatchListener 或 FetchListener"""
        return self.root.factory.get_or_create(
            CCXTExchangeOrderBillListener,
            name=name,
            parent=self
        )

class ExchangePositionWatchListener(Listener):
    """
    交易所持仓监听器

    定期从交易所获取持仓信息并更新到 Exchange 实例中。
    """
    @property
    def interval(self) -> float:
        return 0.1  # watch 频率支持较高

    @property
    def exchange(self) -> 'BaseExchange':
        """通过树形结构获取"""
        return self.parent.parent

    async def on_tick(self) -> None:
        """获取并更新持仓信息"""
        if not self.exchange.ready:
            return
        await self.exchange.medal_watch_positions()


class ExchangePositionListener(Listener):
    """
    交易所持仓监听器

    使用 GroupListener 自动管理动态子节点。
    """
    @property
    def interval(self) -> float:  # 若过期自动同步仓位
        return 5.0

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self.root.factory.get_or_create(
            ExchangePositionWatchListener,
            parent=self
        )

    @property
    def exchange(self) -> 'BaseExchange':
        """通过树形结构获取"""
        return self.parent

    async def on_tick(self):
        if not self.exchange.ready:
            return
        await self.exchange.medal_fetch_positions()
        return False


class ExchangeStateListener(Listener):
    @property
    def interval(self) -> float:
        return 30.0

    @property
    def exchange(self) -> 'BaseExchange':
        """通过树形结构获取"""
        return self.parent

    async def on_tick(self) -> None:
        """获取并更新余额信息"""
        # print("tick: ExchangeStateListener")
        db = self.root.database
        if not self.exchange.ready or db is None:
            return
        result = {}
        for ccxt_key in self.exchange.exchanges.keys():
            balance_usd = await self.exchange.medal_fetch_balance_usd(ccxt_key)
            result[ccxt_key] = balance_usd  # 余额
        total = sum(result.values())
        if self.exchange.unified_account:
            total = total / len(self.exchange.exchanges)
        controller = db.get_controller(ExchangeStateController)
        await controller.update(result.get("swap", 0.0), result.get("spot", 0.0), total, self.exchange)


class CCXTExchangeBalanceWatchListener(Listener):

    @property
    def interval(self) -> float:
        return 0.1

    @property
    def exchange(self) -> 'BaseExchange':
        """通过树形结构获取 """
        return self.parent.exchange

    async def on_tick(self) -> None:
        """获取并更新余额信息"""
        if not self.exchange.ready:
            return
        await self.exchange.medal_watch_balance(self.name)  # name 即 ccxt_instance_key


class CCXTExchangeBalanceListener(Listener):

    """
    交易所余额监听器

    定期从交易所获取余额信息并更新到 Exchange 实例中。
    """

    @property
    def interval(self) -> float:
        """获取日志输出间隔"""
        return 15.0

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self.root.factory.get_or_create(
            CCXTExchangeBalanceWatchListener,
            name=self.name,  # 使用相同名称，与 sync_children_params 的 key 一致
            parent=self
        )

    @property
    def exchange(self) -> 'BaseExchange':
        """通过树形结构获取"""
        return self.parent.exchange

    async def on_tick(self) -> None:
        """获取并更新余额信息"""
        if not self.exchange.ready:
            return
        await self.exchange.medal_fetch_balance(self.name)


class ExchangeBalanceListener(CCXTExchangeGroupListener):
    """
    交易所余额监听器

    定期从交易所获取余额信息并更新到 Exchange 实例中。
    使用 GroupListener 自动管理动态子节点。
    """

    def create_dynamic_child(self, name: str, param: Any) -> Listener:
        """根据参数创建 WatchListener 或 FetchListener"""
        return self.root.factory.get_or_create(
            CCXTExchangeBalanceListener,
            name=name,
            parent=self
        )


class ExchangeFundingRateBillListener(Listener):

    @property
    def interval(self) -> float:
        return 30.0

    @property
    def exchange(self) -> 'BaseExchange':
        """通过树形结构获取"""
        return self.parent

    async def on_tick(self):
        """获取并更新资金费率信息"""
        if not self.exchange.ready:
            return
        history = await self.exchange.medal_fetch_funding_rates_history()
        db = self.root.database
        if db is not None:
            controller = db.get_controller(FundingRateBillController)
            await controller.update(history, self.exchange)

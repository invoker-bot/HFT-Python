"""
Executor 执行器基类

执行器负责将策略的目标仓位转化为实际交易。

工作流程：
    1. on_tick() 获取聚合目标
    2. 对每个 (exchange, symbol, scope)：
        a. 获取当前仓位
        b. 计算 delta = target - current
        c. 如果 |delta| > per_order_usd，执行交易
    # 3. speed 影响执行策略（市价/限价等）

参数说明：
    per_order_usd: 单笔订单大小，也是执行阈值
        - delta > per_order_usd 时才执行
        - 这避免了频繁的小额交易

Issue 0013: Strategy 数据驱动增强（单策略标量化）
    - strategies_data: {"字段名": 值, ...} 格式（不再是列表）
    - Executor 可通过 strategies["字段名"] 直接访问值
    - 单策略场景，不需要 sum/avg 聚合
"""
# pylint: disable=import-outside-toplevel,protected-access
import time
from abc import abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional
from ..core.scope.scopes import TradingPairScope
from ..core.listener import Listener
from ..plugin import pm
from ..indicator.base import BaseIndicator
from ccxt.base.types import OrderRequest, Order
if TYPE_CHECKING:
    from ..exchange.base import BaseExchange
    from ..exchange.group import ExchangeGroup
    from ..strategy.base import BaseStrategy
    from .config import BaseExecutorConfig


# class ExecutorState(Enum):
#     """执行器状态"""
#     IDLE = "idle"               # 空闲
#     EXECUTING = "executing"     # 执行中
#     PAUSED = "paused"           # 暂停


# @dataclass
# class ExecutionResult:
#     """执行结果"""
#     exchange_class: str
#     symbol: str
#     success: bool
#     exchange_name: str
#     target_usd: float = 0.0
#     current_usd: float = 0.0
#     delta_usd: float = 0.0
#     order_id: Optional[str] = None
#     filled_amount: float = 0.0
#     average_price: float = 0.0
#     error: Optional[str] = None
#     timestamp: datetime = field(default_factory=datetime.now)


# ============================================================
# 限价单管理相关数据结构
# ============================================================

@dataclass
class ActiveOrder:
    """活跃订单"""
    order_id: str
    exchange_path: str
    symbol: str
    price: float
    amount: float          # > 0 buy, < 0 sell
    created_at: float      # 创建时间
    timeout_refresh_tolerance: float  # 超时时间（秒）
    # last_updated_at: float # 最后被认领时间

    @property
    def outdated(self) -> bool:
        """检查订单是否过期"""
        return time.time() > self.created_at + self.timeout_refresh_tolerance


@dataclass
class OrderIntent:
    """
    订单意图

    描述"想要"在什么价格挂什么单，由基类统一处理订单生命周期。
    """
    price: Optional[float]    # 目标价格，None 表示市价单
    amount: float          # 数量
    timeout_refresh_tolerance: float # 超时时间（秒）
    price_refresh_tolerance: float  # 刷新容忍度, 绝对值
    post_only: bool = True  # 是否只挂 maker 单


class ActiveOrdersTracker:

    def __init__(self):
        # exchange_path -> symbol -> order_id -> ActiveOrder
        self.orders: dict[str, dict[str, dict[str, ActiveOrder]]] = defaultdict(self._default_dict_factory)

    def _default_dict_factory(self):
        return defaultdict(dict)

    def is_in_tolerance(self, price: float, price_ref: float, tolerance: float) -> bool:
        return abs(price - price_ref) <= abs(tolerance)

    def calculate_changed_orders(self, exchange_path: str, symbol: str, orders: list[OrderIntent]) -> \
            tuple[list[OrderIntent], list[ActiveOrder]]:  # 应该先place 再remove
        orders_to_place: list[OrderIntent] = []
        orders_to_remove: list[ActiveOrder] = []
        for check_order in list(self.orders[exchange_path][symbol].values()):
            if check_order.outdated:
                orders_to_remove.append(check_order)
                continue
            matched = False
            for order in orders:  # orders is the desired orders
                if order.price is not None and self.is_in_tolerance(order.price, check_order.price, order.price_refresh_tolerance):
                    matched = True
                    break
            if not matched:
                orders_to_remove.append(check_order)

        for order in orders:
            if order.price is None:  # market order, always place
                orders_to_place.append(order)
                continue
            matched = False
            for tracked_order in list(self.orders[exchange_path][symbol].values()):
                if (not tracked_order.outdated) and self.is_in_tolerance(order.price, tracked_order.price, order.price_refresh_tolerance):
                    matched = True
                    break
            if not matched:
                orders_to_place.append(order)
        return orders_to_place, orders_to_remove

    def add_active_orders(self, exchange_path: str, symbol: str, orders: list[ActiveOrder]):
        for o in orders:
            self.orders[exchange_path][symbol][o.order_id] = o

    def remove_active_orders(self, exchange_path: str, symbol: str, order_ids: list[str]):
        for order_id in order_ids:
            self.orders[exchange_path][symbol].pop(order_id, None)


class BaseExecutor(Listener):
    """
    执行器基类

    职责：
    - 每个 tick 从 StrategyGroup 获取聚合的目标仓位
    - 计算当前仓位与目标的差值
    - 当差值超过阈值时执行交易
    - 管理限价单生命周期（复用、取消）

    子类需要实现：
        execute_delta(): 执行具体的交易逻辑
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.active_orders_tracker = ActiveOrdersTracker()  # 这里管理所有类型的订单

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self.config: 'BaseExecutorConfig' = kwargs['config']
        self.exchange_group.event.on("order:canceled", self.on_order_canceled)
        self.exchange_group.event.on("order:updated", self.on_order_updated)

    async def on_order_updated(self, exchange_path: str, symbol: str, order: Order):
        if order['status'] in ["closed", "canceled", "expired", "rejected"]:
            self.active_orders_tracker.remove_active_orders(exchange_path, symbol, [
                order['id']
            ])
        elif order['status'] == "open":
            direction = 1 if order['side'] == "buy" else -1
            if order['id'] in self.active_orders_tracker.orders[exchange_path][symbol]:
                return  # 已经在跟踪列表中
            lastTradeTimestamp = order.get('lastTradeTimestamp', time.time() * 1000)
            if lastTradeTimestamp is None:
                lastTradeTimestamp = time.time()
            else:
                lastTradeTimestamp = float(lastTradeTimestamp) / 1000.0
            self.active_orders_tracker.add_active_orders(exchange_path, symbol, [
                ActiveOrder(
                    order_id=order['id'],
                    exchange_path=exchange_path,
                    symbol=symbol,
                    price=order.get('price', 0.0),
                    amount=order.get('amount', 0.0) * direction,
                    created_at=lastTradeTimestamp,
                    timeout_refresh_tolerance=self.config.default_timeout
                )
            ])

    async def on_order_canceled(self, exchange_path: str, symbol: str, order_id: str, order: Order):
        self.active_orders_tracker.remove_active_orders(exchange_path, symbol, [
                order_id
            ])

    async def create_orders_by_intents(
        self,
        exchange_path: str,
        symbol: str,
        intents: list[OrderIntent]
    ) -> list[ActiveOrder]:
        """根据订单意图创建订单"""
        exchange = self.exchange_group.exchange_instances[exchange_path]
        order_requests: list[OrderRequest] = []
        created_orders: list[ActiveOrder] = []
        contract_size = await exchange.get_contract_size_async(symbol)
        for intent in intents:
            side = "buy" if intent.amount > 0 else "sell"
            if intent.post_only:
                params = {"postOnly": True}
            else:
                params = {}
            order_requests.append({
                "symbol": symbol,
                "type": "limit" if intent.price is not None else "market",
                "side": side,
                "amount": abs(intent.amount) / contract_size,
                "price": intent.price,
                "params": params
            })
        orders = await exchange.create_orders(order_requests)
        for order, intent in zip(orders, intents):
            if order.get('id', None) is not None and intent.price is not None:
                created_orders.append(ActiveOrder(
                    order_id=order['id'],
                    exchange_path=exchange_path,
                    symbol=symbol,
                    price=intent.price,
                    amount=intent.amount,
                    created_at=time.time(),
                    timeout_refresh_tolerance=intent.timeout_refresh_tolerance
                ))
        return created_orders

    async def cancel_active_orders(
        self,
        exchange_path: str,
        symbol: str,
        orders: list[ActiveOrder]
    ):
        """取消活跃订单"""
        exchange = self.exchange_group.exchange_instances[exchange_path]
        order_ids = [o.order_id for o in orders]
        await exchange.cancel_orders(order_ids, symbol)

    async def process_intents(self, exchange_path: str, symbol: str, intents: list[OrderIntent]):
        """
        处理单个目标仓位

        子类可覆盖此方法以实现不同的执行逻辑。
        默认实现为市价单执行差值。
        """
        if len(intents) == 0:
            return
        orders_to_place, orders_to_remove = self.active_orders_tracker.calculate_changed_orders(
            exchange_path, symbol, intents
        )
        # 1. 创建新订单
        if len(orders_to_place) > 0:
            created_orders = await self.create_orders_by_intents(exchange_path, symbol, orders_to_place)
            self.active_orders_tracker.add_active_orders(exchange_path, symbol, created_orders)

        # 2. 取消过期订单
        if len(orders_to_remove) > 0:
            await self.cancel_active_orders(exchange_path, symbol, orders_to_remove)
        # self.active_orders_tracker.remove_active_orders(
        #     exchange_path, symbol,
        #     [o.order_id for o in orders_to_remove]
        # )


    # ===== 属性 =====
    @property
    def exchange_group(self) -> "ExchangeGroup":
        """获取 ExchangeGroup"""
        return self.root.exchange_group

    @property
    def strategy(self) -> "BaseStrategy":
        """获取 Strategy"""
        return self.root.strategy

    @property
    def virtual_machine(self):
        """获取虚拟机"""
        return self.root.vm


    # ===== 工具方法 =====
    # TODO: 可能需要考虑合约的 contract_size
    # @staticmethod
    # def usd_to_amount(
    #     exchange: "BaseExchange",
    #     symbol: str,
    #     usd: float,
    #     price: float,
    # ) -> float:
    #     """
    #     将 USD 价值转换为下单数量（合约数量）
#
    #     计算公式：
    #         base_amount = usd / price  # 基础货币数量（如 BTC）
    #         amount = base_amount / contract_size  # 合约数量
#
    #     Args:
    #         exchange: 交易所实例
    #         symbol: 交易对
    #         usd: USD 价值（可正可负）
    #         price: 当前价格
#
    #     Returns:
    #         合约数量（保持 usd 的正负符号）
    #     """
    #     if price <= 0:
    #         return 0.0
    #     base_amount = usd / price
    #     contract_size = exchange.get_contract_size(symbol)
    #     if contract_size is None:
    #         contract_size = 1.0  # 默认值，避免除零
    #     return base_amount / contract_size
    #

    # ===== 生命周期 =====

    async def on_tick(self) -> bool:
        """
        主循环：获取目标仓位，计算差值，执行交易
        """
        nodes = self.strategy.calculate_flow_nodes()
        vm = self.virtual_machine
        app_core = self.root
        for node in nodes.values():
            if not isinstance(node.scope, TradingPairScope):
                self.logger.warning("Skipping non-trading pair scope: %s", node.scope.path)
            exchange_path = node.get_var("exchange_path")
            exchange = self.exchange_group.exchange_instances[exchange_path]
            symbol = node.get_var("symbol")
            last_refresh_orders = node.get_var("__last_refresh_orders", 0)
            if time.time() - last_refresh_orders > self.config.default_timeout:  # 每timeout秒刷新一次订单状态
                for order in await exchange.fetch_open_orders(symbol):
                    await self.on_order_updated(exchange_path, symbol, order)
                node.set_var("__last_refresh_orders", time.time())
            # process
            # inject indicators data into vm context
            should_continue = False
            for required_indicator in self.config.requires:
                indicator_class_name = app_core.config.indicators[required_indicator].class_name
                indicator_class = BaseIndicator.classes[indicator_class_name]
                indicator_node = node
                while indicator_node is not None:
                    if isinstance(indicator_node.scope, indicator_class.supported_scope):
                        break
                    if len(indicator_node.prev) > 0:
                        indicator_node = indicator_node.prev[0]
                    else:
                        indicator_node = None
                if indicator_node is None:
                    raise ValueError(f"Cannot find suitable scope for indicator {required_indicator} in scope {node.scope.path}")
                indicator = app_core.query_indicator(required_indicator, indicator_node)
                if not indicator.ready:
                    should_continue = True
                    break
                injected_vars = indicator.get_vars()
                vm.inject_vars(injected_vars, node, indicator.namespace)  # 注入指标
            if should_continue:
                continue
            vm.execute_vars(self.config.standard_vars_definition, node)  # 执行变量
            if self.config.condition is not None and not vm.eval(self.config.condition, node):
                self.logger.debug("Condition not met, skipping scope: %s", node.scope.path)
                continue
            # collect intents
            intents: list[OrderIntent] = []
            for order_def in self.config.total_order_definitions:
                node.set_var("level", order_def.level)  # 设置当前订单层级变量
                vm.execute_vars(order_def.standard_vars_definition, node)  # 计算订单级变量
                if order_def.condition is not None and not vm.eval(order_def.condition, node):
                    self.logger.debug("Order condition not met, skipping order level: %s", order_def.level)
                    continue
                amount = 0.0
                if order_def.order_amount is not None:
                    amount = vm.eval(order_def.order_amount, node)
                elif order_def.order_usd is not None:
                    usd = vm.eval(order_def.order_usd, node)
                    current_price = node.get_var("last_price")  # 使用最新价
                    amount = usd / current_price  # 简化处理
                else:
                    raise ValueError("Either order_amount or order_usd must be specified")
                price = None
                spread = None
                if order_def.price is not None:
                    price = vm.eval(order_def.price, node)
                elif order_def.spread is not None:
                    ask = node.get_var("ask_price")
                    bid = node.get_var("bid_price")
                    spread = vm.eval(order_def.spread, node)
                    if amount > 0:  # buy
                        price = bid - spread
                    else:
                        price = ask + spread
                # else:  is market order
                if price is None:
                    post_only = False
                else:
                    post_only = vm.eval(order_def.post_only, node)

                timeout = vm.eval(order_def.timeout, node)
                refresh_tolerance = 0.0
                if order_def.refresh_tolerance_usd is not None:
                    refresh_tolerance = vm.eval(order_def.refresh_tolerance_usd, node)
                elif order_def.refresh_tolerance is not None:
                    refresh_tolerance_pct = vm.eval(order_def.refresh_tolerance, node)
                    if spread is None:
                        if price is not None:
                            spread = abs(price - node.get_var("last_price"))
                        else:
                            spread = 0.0
                        refresh_tolerance = spread * refresh_tolerance_pct
                    intents.append(OrderIntent(
                        price=price,
                        amount=amount,
                        timeout_refresh_tolerance=timeout,
                        price_refresh_tolerance=refresh_tolerance,
                        post_only=post_only
                    ))
            # print("Intents:", intents)
            await self.process_intents(exchange_path, symbol, intents)


        # self._stats["ticks"] += 1

        # if self._executor_state == ExecutorState.PAUSED:
        #     return False

        # 获取聚合的目标仓位
        # targets = self.strategy_group.get_aggregated_targets()

        # if not targets:
        #     return False
        # self.logger.info("on_tick called - placeholder implementation")
        # self._executor_state = ExecutorState.EXECUTING

        # 插件钩子：执行开始
        # pm.hook.on_execution_start(executor=self, targets=targets)

        # results = []
        # try:
        #     results = await self._process_targets(targets)
        # finally:
        #     self._executor_state = ExecutorState.IDLE
            # 插件钩子：执行完成
        #     pm.hook.on_execution_complete(executor=self, results=results)

        # return False


    # ===== 控制方法 =====

    # def pause(self) -> None:
    #     """暂停执行"""
    #     self._executor_state = ExecutorState.PAUSED
    #     self.logger.info("Executor paused")

    # def resume(self) -> None:
    #     """恢复执行"""
    #     self._executor_state = ExecutorState.IDLE
    #     self.logger.info("Executor resumed")

    # ===== 状态 =====

    # @property
    # def log_state_dict(self) -> dict:
    #     return {
    #         "state": self._executor_state.value,
    #         "per_order_usd": self.per_order_usd,
    #         "active_orders": self.active_orders_count,
    #         **self._stats
    #     }

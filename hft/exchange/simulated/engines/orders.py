"""
OrderManager - 订单管理器

支持概率成交、部分成交、异步通知。
"""
import asyncio
import math
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

from .positions import PositionTracker
from .balance import BalanceTracker

logger = logging.getLogger(__name__)


@dataclass
class SimulatedOrder:
    """模拟订单"""
    id: str
    symbol: str
    type: str           # 'limit', 'market', 'limit_post_only'
    side: str           # 'buy', 'sell'
    amount: float
    price: Optional[float]
    filled: float = 0.0
    remaining: float = 0.0
    status: str = 'open'       # open, closed, canceled, expired
    timestamp: float = 0.0
    average: Optional[float] = None
    cost: float = 0.0
    params: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.remaining == 0.0:
            self.remaining = self.amount

    def to_ccxt_order(self) -> dict:
        """转换为 ccxt Order 格式"""
        return {
            'id': self.id,
            'clientOrderId': None,
            'symbol': self.symbol,
            'type': 'limit' if 'limit' in self.type else self.type,
            'side': self.side,
            'amount': self.amount,
            'price': self.price,
            'filled': self.filled,
            'remaining': self.remaining,
            'status': self.status,
            'average': self.average,
            'cost': self.cost,
            'timestamp': int(self.timestamp),
            'datetime': None,
            'fee': {'cost': 0.0, 'currency': 'USDT'},
            'trades': [],
            'reduceOnly': self.params.get('reduceOnly', False),
            'postOnly': 'post_only' in self.type,
            'info': {},
        }


class OrderManager:
    """
    订单管理器

    特性：
    - 市价单立即成交
    - 限价单概率成交（基于价格偏离度）
    - 部分成交
    - asyncio.Queue 通知 watch_orders()
    """

    # 已关闭订单最大保留数量（防止无限增长）
    MAX_CLOSED_ORDERS = 1000
    # 更新队列最大容量
    MAX_QUEUE_SIZE = 500

    def __init__(
        self,
        position_tracker: PositionTracker,
        balance_tracker: BalanceTracker,
        fill_probability: float = 0.5,
        contract_sizes: Optional[dict[str, float]] = None,
    ):
        self._orders: dict[str, SimulatedOrder] = {}
        self._closed_orders: list[SimulatedOrder] = []
        self._order_counter = 0
        self._position_tracker = position_tracker
        self._balance_tracker = balance_tracker
        self._fill_probability = fill_probability
        self._contract_sizes: dict[str, float] = contract_sizes or {}
        self._update_queue: asyncio.Queue = asyncio.Queue(maxsize=self.MAX_QUEUE_SIZE)

    @property
    def position_tracker(self) -> PositionTracker:
        return self._position_tracker

    def _next_order_id(self) -> str:
        self._order_counter += 1
        return f"sim-{self._order_counter}"

    def _get_contract_size(self, symbol: str) -> float:
        return self._contract_sizes.get(symbol, 1.0)

    def place_order(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: Optional[float] = None,
        params: Optional[dict] = None,
    ) -> dict:
        """创建订单"""
        order = SimulatedOrder(
            id=self._next_order_id(),
            symbol=symbol,
            type=type,
            side=side,
            amount=amount,
            price=price,
            filled=0.0,
            remaining=amount,
            status='open',
            timestamp=time.time() * 1000,
            params=params or {},
        )

        if type == 'market':
            # 市价单立即全额成交
            fill_price = price if price else 0.0
            self._execute_fill(order, amount, fill_price)
        else:
            # 限价单挂起
            self._orders[order.id] = order
            logger.debug("Order placed: %s %s %s %.6f @ %.2f",
                         order.id, side, symbol, amount, price or 0)

        return order.to_ccxt_order()

    def _execute_fill(self, order: SimulatedOrder, fill_amount: float, fill_price: float):
        """执行成交"""
        fill_amount = min(fill_amount, order.remaining)
        if fill_amount <= 0:
            return

        order.filled += fill_amount
        order.remaining = order.amount - order.filled
        order.cost += fill_amount * fill_price
        order.average = order.cost / order.filled if order.filled > 0 else None

        if order.remaining < 1e-9 or order.remaining / order.amount < 0.001:
            order.status = 'closed'
            order.remaining = 0.0
            self._orders.pop(order.id, None)
            self._closed_orders.append(order)
            self._trim_closed_orders()
        else:
            order.status = 'open'  # 部分成交仍然 open

        # 更新仓位（amount 是合约张数，需乘以 contract_size）
        contract_size = self._get_contract_size(order.symbol)
        position_delta = fill_amount * contract_size
        direction = 1 if order.side == 'buy' else -1
        self._position_tracker.update(order.symbol, direction * position_delta)

        # 更新余额
        cost_usdt = fill_amount * fill_price * contract_size
        self._balance_tracker.apply_trade(order.side, cost_usdt, order.symbol)

        # 手续费（简化：maker 0.02%, taker 0.05%）
        fee_rate = 0.0002 if 'limit' in order.type else 0.0005
        self._balance_tracker.apply_fee(cost_usdt * fee_rate)

        # 通知 watch_orders
        try:
            self._update_queue.put_nowait(order.to_ccxt_order())
        except asyncio.QueueFull:
            pass

        logger.debug("Order filled: %s %s %.6f @ %.2f (total: %.6f/%.6f)",
                     order.id, order.side, fill_amount, fill_price,
                     order.filled, order.amount)

    @staticmethod
    def _sigmoid(x: float) -> float:
        """Sigmoid 函数"""
        return 1.0 / (1.0 + math.exp(-x))

    def try_fill_orders(self, price_states: dict):
        """
        尝试成交挂单

        Args:
            price_states: {symbol: SymbolPriceState} 当前价格状态
        """
        for order_id in list(self._orders.keys()):
            order = self._orders.get(order_id)
            if order is None:
                continue

            state = price_states.get(order.symbol)
            if state is None:
                continue

            mid = state.mid_price
            half_spread = mid * state.spread_bps / 10000
            bid = mid - half_spread
            ask = mid + half_spread

            # 检查价格是否有利
            can_fill = False
            if order.side == 'buy' and order.price is not None and ask <= order.price:
                can_fill = True
            elif order.side == 'sell' and order.price is not None and bid >= order.price:
                can_fill = True

            if not can_fill:
                continue

            # 概率成交
            if order.price is not None and mid > 0:
                distance = abs(mid - order.price) / mid
                spread_ratio = (half_spread * 2) / mid if mid > 0 else 0.001
                # 价格越有利，成交概率越高
                favorability = distance / max(spread_ratio, 1e-8)
                p = self._sigmoid(favorability * 2 - 1) * self._fill_probability
            else:
                p = self._fill_probability

            import random
            if random.random() < p:
                # 部分或全额成交
                fill_ratio = 0.3 + random.random() * 0.7  # 30%-100%
                fill_amount = order.remaining * fill_ratio
                self._execute_fill(order, fill_amount, order.price or mid)

    def cancel_order(self, order_id: str) -> dict:
        """取消订单"""
        order = self._orders.pop(order_id, None)
        if order is None:
            # 检查已完成的订单
            for closed in self._closed_orders:
                if closed.id == order_id:
                    return closed.to_ccxt_order()
            raise Exception(f"Order {order_id} not found")

        order.status = 'canceled'
        self._closed_orders.append(order)
        self._trim_closed_orders()
        try:
            self._update_queue.put_nowait(order.to_ccxt_order())
        except asyncio.QueueFull:
            pass
        logger.debug("Order canceled: %s", order_id)
        return order.to_ccxt_order()

    def cancel_orders(self, order_ids: list[str], symbol: str = None) -> list[dict]:
        """批量取消订单"""
        results = []
        for oid in order_ids:
            try:
                results.append(self.cancel_order(oid))
            except Exception:
                results.append({'id': oid, 'status': 'error'})
        return results

    def _trim_closed_orders(self):
        """修剪已关闭订单列表，防止无限增长"""
        if len(self._closed_orders) > self.MAX_CLOSED_ORDERS:
            # 保留最新的一半
            self._closed_orders = self._closed_orders[len(self._closed_orders) - self.MAX_CLOSED_ORDERS // 2:]

    def get_order(self, order_id: str) -> dict:
        """查询订单"""
        order = self._orders.get(order_id)
        if order is not None:
            return order.to_ccxt_order()
        for closed in self._closed_orders:
            if closed.id == order_id:
                return closed.to_ccxt_order()
        raise Exception(f"Order {order_id} not found")

    def get_open_orders(self, symbol: Optional[str] = None) -> list[dict]:
        """获取所有挂单"""
        orders = self._orders.values()
        if symbol is not None:
            orders = [o for o in orders if o.symbol == symbol]
        return [o.to_ccxt_order() for o in orders]

    async def wait_for_updates(self, timeout: float = 30.0) -> list[dict]:
        """等待订单更新（用于 watch_orders）"""
        try:
            order = await asyncio.wait_for(self._update_queue.get(), timeout=timeout)
            results = [order]
            # 批量获取
            while not self._update_queue.empty():
                try:
                    results.append(self._update_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            return results
        except asyncio.TimeoutError:
            return []

    def reset(self):
        """重置所有订单"""
        self._orders.clear()
        self._closed_orders.clear()
        self._order_counter = 0
        # 清空队列
        while not self._update_queue.empty():
            try:
                self._update_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

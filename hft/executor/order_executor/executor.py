"""
订单执行器（已弃用）

.. deprecated::
    本模块中的类已弃用，将在未来版本中移除。
    请使用 hft.executor.base.BaseExecutor 和 hft.executor.market.MarketExecutor 替代。

遗留类：
- MarketOrderExecutor: 市价单执行器（使用 tick_callback 模式）
- LimitOrderExecutor: 限价单执行器
- MultipleLimitOrderExecutor: 多级限价单执行器

新架构说明：
- 新的 BaseExecutor 通过 on_signal() 接收 TradeSignal
- TradeSignal 包含 value [-1.0, 1.0] 表示目标仓位比例
- 通过 StrategyGroup.emit_signal() 发送信号到执行器
"""
import time
import logging
from abc import abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from ...core.listener import Listener
from ...strategy.pairs_strategy import TradingPairs
from ..spread_executor import BaseSpread, FixedSpread, SpreadResult

if TYPE_CHECKING:
    from ...exchange.base import BaseExchange

logger = logging.getLogger(__name__)


@dataclass
class OrderState:
    """订单状态"""
    order_id: str
    symbol: str
    side: str                     # 'buy' or 'sell'
    order_type: str               # 'market' or 'limit'
    price: Optional[float]        # 限价单价格
    amount: float                 # 下单数量
    filled: float = 0.0           # 已成交数量
    status: str = "open"          # open, closed, canceled
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @property
    def is_open(self) -> bool:
        return self.status == "open"

    @property
    def is_filled(self) -> bool:
        return self.status == "closed" and self.filled >= self.amount * 0.99

    @property
    def is_cancelled(self) -> bool:
        return self.status == "canceled"

    @property
    def remaining(self) -> float:
        return max(0, self.amount - self.filled)


class BaseOrderExecutor(Listener):
    """
    订单执行器基类

    根据目标仓位和当前仓位的差值，定期下单
    """

    def __init__(
        self,
        name: str,
        exchange: "BaseExchange",
        symbol: str,
        order_interval: float = 5.0,      # 下单间隔（秒）
        per_order_usd: float = 100.0,     # 每单金额（USD）
        max_orders: int = 10,             # 最大挂单数
        order_timeout: float = 60.0,      # 订单超时（秒）
    ):
        super().__init__(name=name, interval=order_interval)
        self._exchange = exchange
        self._symbol = symbol
        self._order_interval = order_interval
        self._per_order_usd = per_order_usd
        self._max_orders = max_orders
        self._order_timeout = order_timeout

        # 目标仓位差（正数=需要买入，负数=需要卖出）
        self._target_delta: float = 0.0

        # 活跃订单
        self._active_orders: dict[str, OrderState] = {}

        # 上次下单时间
        self._last_order_time: float = 0.0

        # 累计成交
        self._total_filled: float = 0.0
        self._total_cost: float = 0.0

    @property
    def exchange(self) -> "BaseExchange":
        return self._exchange

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def target_delta(self) -> float:
        return self._target_delta

    @target_delta.setter
    def target_delta(self, value: float) -> None:
        self._target_delta = value

    @property
    def active_orders(self) -> dict[str, OrderState]:
        return self._active_orders

    @property
    def pending_amount(self) -> float:
        """挂单中的数量"""
        return sum(o.remaining for o in self._active_orders.values())

    @property
    def remaining_delta(self) -> float:
        """剩余需要执行的差值"""
        if self._target_delta > 0:
            return max(0, self._target_delta - self.pending_amount)
        else:
            return min(0, self._target_delta + self.pending_amount)

    async def get_current_price(self) -> float:
        """获取当前价格"""
        ticker = await self._exchange.fetch_ticker(self._symbol)
        return (ticker['bid'] + ticker['ask']) / 2

    def calculate_amount(self, price: float) -> float:
        """根据 per_order_usd 计算下单数量"""
        return self._per_order_usd / price

    @abstractmethod
    async def place_order(self) -> Optional[OrderState]:
        """下单逻辑，子类实现"""
        ...

    async def update_orders(self) -> None:
        """更新订单状态"""
        for order_id in list(self._active_orders.keys()):
            try:
                order = await self._exchange.fetch_order(order_id, self._symbol)
                state = self._active_orders[order_id]
                state.status = order.get('status', 'open')
                state.filled = order.get('filled', 0)
                state.updated_at = time.time()

                # 订单完成或取消，移除
                if state.is_filled or state.is_cancelled:
                    if state.is_filled:
                        self._total_filled += state.filled
                        avg_price = order.get('average', order.get('price', 0))
                        self._total_cost += state.filled * avg_price
                    del self._active_orders[order_id]
                    logger.info(f"[{self.name}] Order {order_id} {state.status}, filled={state.filled}")

            except Exception as e:
                logger.warning(f"[{self.name}] Failed to update order {order_id}: {e}")

    async def cancel_stale_orders(self) -> None:
        """取消超时订单"""
        now = time.time()
        for order_id, state in list(self._active_orders.items()):
            if now - state.created_at > self._order_timeout and state.is_open:
                try:
                    await self._exchange.cancel_order(order_id, self._symbol)
                    state.status = "canceled"
                    del self._active_orders[order_id]
                    logger.info(f"[{self.name}] Cancelled stale order {order_id}")
                except Exception as e:
                    logger.warning(f"[{self.name}] Failed to cancel order {order_id}: {e}")

    async def tick_callback(self) -> bool:
        """定期执行"""
        # 1. 更新订单状态
        await self.update_orders()

        # 2. 取消超时订单
        await self.cancel_stale_orders()

        # 3. 检查是否需要下单
        now = time.time()
        if now - self._last_order_time < self._order_interval:
            return True

        # 4. 检查剩余差值
        if abs(self.remaining_delta) < 1e-8:
            return True

        # 5. 检查挂单数量限制
        if len(self._active_orders) >= self._max_orders:
            return True

        # 6. 下单
        order = await self.place_order()
        if order:
            self._active_orders[order.order_id] = order
            self._last_order_time = now

        return True


class MarketOrderExecutor(BaseOrderExecutor):
    """
    市价单执行器

    根据 order_interval 和 per_order_usd 定期下市价单
    """

    async def place_order(self) -> Optional[OrderState]:
        """下市价单"""
        try:
            price = await self.get_current_price()
            amount = self.calculate_amount(price)

            # 确定方向
            if self._target_delta > 0:
                side = 'buy'
                amount = min(amount, self.remaining_delta)
            else:
                side = 'sell'
                amount = min(amount, abs(self.remaining_delta))

            if amount < 1e-8:
                return None

            # 下单
            order = await self._exchange.place_order(
                symbol=self._symbol,
                order_type='market',
                side=side,
                amount=amount,
            )

            if order:
                logger.info(
                    f"[{self.name}] Market {side} {amount:.6f} @ market, "
                    f"order_id={order.get('id', 'N/A')}"
                )
                return OrderState(
                    order_id=order['id'],
                    symbol=self._symbol,
                    side=side,
                    order_type='market',
                    price=None,
                    amount=amount,
                )

        except Exception as e:
            logger.error(f"[{self.name}] Failed to place market order: {e}")

        return None


class LimitOrderExecutor(BaseOrderExecutor):
    """
    限价单执行器

    使用 Spread 计算价格，定期下限价单
    支持根据当前价格与挂单价格的差距调整订单
    """

    def __init__(
        self,
        name: str,
        exchange: "BaseExchange",
        symbol: str,
        spread: Optional[BaseSpread] = None,
        price_tolerance: float = 0.002,   # 价格容忍度，超过则撤单重挂
        **kwargs
    ):
        super().__init__(name=name, exchange=exchange, symbol=symbol, **kwargs)
        self._spread = spread or FixedSpread(spread_pct=0.001)
        self._price_tolerance = price_tolerance
        self._volatility: float = 0.01  # 波动率估计
        self._inventory: float = 0.0     # 当前库存

    @property
    def spread(self) -> BaseSpread:
        return self._spread

    @spread.setter
    def spread(self, value: BaseSpread) -> None:
        self._spread = value

    def set_volatility(self, vol: float) -> None:
        """设置波动率"""
        self._volatility = vol

    def set_inventory(self, inv: float) -> None:
        """设置库存"""
        self._inventory = inv

    async def place_order(self) -> Optional[OrderState]:
        """下限价单"""
        try:
            mid_price = await self.get_current_price()
            amount = self.calculate_amount(mid_price)

            # 确定方向
            if self._target_delta > 0:
                side = 'buy'
                amount = min(amount, self.remaining_delta)
            else:
                side = 'sell'
                amount = min(amount, abs(self.remaining_delta))

            if amount < 1e-8:
                return None

            # 使用 Spread 计算价格
            price = self._spread.get_order_price(
                mid_price=mid_price,
                side=side,
                volatility=self._volatility,
                inventory=self._inventory,
            )

            # 下单
            order = await self._exchange.place_order(
                symbol=self._symbol,
                order_type='limit',
                side=side,
                amount=amount,
                price=price,
            )

            if order:
                logger.info(
                    f"[{self.name}] Limit {side} {amount:.6f} @ {price:.2f}, "
                    f"order_id={order.get('id', 'N/A')}"
                )
                return OrderState(
                    order_id=order['id'],
                    symbol=self._symbol,
                    side=side,
                    order_type='limit',
                    price=price,
                    amount=amount,
                )

        except Exception as e:
            logger.error(f"[{self.name}] Failed to place limit order: {e}")

        return None

    async def adjust_orders(self) -> None:
        """根据价格变化调整订单"""
        try:
            mid_price = await self.get_current_price()

            for order_id, state in list(self._active_orders.items()):
                if not state.is_open or state.price is None:
                    continue

                # 计算当前应该的价格
                target_price = self._spread.get_order_price(
                    mid_price=mid_price,
                    side=state.side,
                    volatility=self._volatility,
                    inventory=self._inventory,
                )

                # 检查价格偏离
                price_diff = abs(state.price - target_price) / mid_price
                if price_diff > self._price_tolerance:
                    # 撤单
                    try:
                        await self._exchange.cancel_order(order_id, self._symbol)
                        del self._active_orders[order_id]
                        logger.info(
                            f"[{self.name}] Adjusted order {order_id}: "
                            f"{state.price:.2f} -> {target_price:.2f}"
                        )
                    except Exception as e:
                        logger.warning(f"[{self.name}] Failed to adjust order: {e}")

        except Exception as e:
            logger.warning(f"[{self.name}] Failed to adjust orders: {e}")

    async def tick_callback(self) -> bool:
        """定期执行，包含订单调整"""
        # 先调整订单
        await self.adjust_orders()

        # 然后执行基类逻辑
        return await super().tick_callback()


@dataclass
class LevelConfig:
    """订单级别配置"""
    spread_multiplier: float    # 点差乘数
    amount_ratio: float         # 数量比例
    priority: int = 0           # 优先级


class MultipleLimitOrderExecutor(BaseOrderExecutor):
    """
    多级限价单执行器

    支持在不同价位挂多个订单
    """

    def __init__(
        self,
        name: str,
        exchange: "BaseExchange",
        symbol: str,
        spread: Optional[BaseSpread] = None,
        levels: Optional[list[LevelConfig]] = None,
        **kwargs
    ):
        super().__init__(name=name, exchange=exchange, symbol=symbol, **kwargs)
        self._spread = spread or FixedSpread(spread_pct=0.001)
        self._levels = levels or [
            LevelConfig(spread_multiplier=1.0, amount_ratio=0.5),
            LevelConfig(spread_multiplier=2.0, amount_ratio=0.3),
            LevelConfig(spread_multiplier=3.0, amount_ratio=0.2),
        ]
        self._volatility: float = 0.01
        self._inventory: float = 0.0

        # 每级别的订单
        self._level_orders: dict[int, Optional[str]] = {}

    @property
    def spread(self) -> BaseSpread:
        return self._spread

    def set_levels(self, levels: list[LevelConfig]) -> None:
        """设置级别配置"""
        self._levels = levels

    def set_volatility(self, vol: float) -> None:
        self._volatility = vol

    def set_inventory(self, inv: float) -> None:
        self._inventory = inv

    async def place_order(self) -> Optional[OrderState]:
        """下多级限价单"""
        try:
            mid_price = await self.get_current_price()
            total_amount = self.calculate_amount(mid_price)

            # 确定方向
            if self._target_delta > 0:
                side = 'buy'
                available = self.remaining_delta
            else:
                side = 'sell'
                available = abs(self.remaining_delta)

            if available < 1e-8:
                return None

            # 为每个级别下单
            placed_order = None
            for i, level in enumerate(self._levels):
                # 检查该级别是否已有订单
                if i in self._level_orders and self._level_orders[i] in self._active_orders:
                    continue

                # 计算该级别的数量
                level_amount = total_amount * level.amount_ratio
                level_amount = min(level_amount, available)

                if level_amount < 1e-8:
                    continue

                # 计算该级别的价格
                base_result = self._spread.calculate(
                    mid_price=mid_price,
                    side=side,
                    volatility=self._volatility,
                    inventory=self._inventory,
                )

                if side == 'buy':
                    level_spread = base_result.bid_spread * level.spread_multiplier
                    price = mid_price * (1 - level_spread)
                else:
                    level_spread = base_result.ask_spread * level.spread_multiplier
                    price = mid_price * (1 + level_spread)

                # 下单
                order = await self._exchange.place_order(
                    symbol=self._symbol,
                    order_type='limit',
                    side=side,
                    amount=level_amount,
                    price=price,
                )

                if order:
                    order_id = order['id']
                    logger.info(
                        f"[{self.name}] Level {i} limit {side} {level_amount:.6f} @ {price:.2f}"
                    )

                    state = OrderState(
                        order_id=order_id,
                        symbol=self._symbol,
                        side=side,
                        order_type='limit',
                        price=price,
                        amount=level_amount,
                    )

                    self._active_orders[order_id] = state
                    self._level_orders[i] = order_id

                    if placed_order is None:
                        placed_order = state

                    available -= level_amount

        except Exception as e:
            logger.error(f"[{self.name}] Failed to place multi-level orders: {e}")

        return placed_order

    async def tick_callback(self) -> bool:
        """定期执行"""
        # 清理已完成的级别订单映射
        for level, order_id in list(self._level_orders.items()):
            if order_id not in self._active_orders:
                del self._level_orders[level]

        return await super().tick_callback()

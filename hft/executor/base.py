"""
Executor 执行器基类

执行器负责将策略的目标仓位转化为实际交易。

工作流程：
    1. on_tick() 调用 strategy_group.get_aggregated_targets() 获取聚合目标
    2. 对每个 (exchange_class, symbol, target_usd, speed)：
        a. 获取当前仓位
        b. 计算 delta = target - current
        c. 如果 |delta| > per_order_usd，执行交易
    3. speed 影响执行策略（市价/限价等）

参数说明：
    per_order_usd: 单笔订单大小，也是执行阈值
        - delta > per_order_usd 时才执行
        - 这避免了频繁的小额交易
"""
from abc import abstractmethod
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, TYPE_CHECKING
from ..core.listener import Listener

if TYPE_CHECKING:
    from ..exchange.group import ExchangeGroup
    from ..exchange.base import BaseExchange
    from ..strategy.group import StrategyGroup, AggregatedTargets
    from .config import BaseExecutorConfig


class ExecutorState(Enum):
    """执行器状态"""
    IDLE = "idle"               # 空闲
    EXECUTING = "executing"     # 执行中
    PAUSED = "paused"           # 暂停


@dataclass
class ExecutionResult:
    """执行结果"""
    exchange_class: str
    symbol: str
    success: bool
    exchange_name: str
    target_usd: float = 0.0
    current_usd: float = 0.0
    delta_usd: float = 0.0
    order_id: Optional[str] = None
    filled_amount: float = 0.0
    average_price: float = 0.0
    error: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)


# ============================================================
# 限价单管理相关数据结构
# ============================================================

@dataclass
class ActiveOrder:
    """活跃订单"""
    order_id: str
    exchange_name: str
    symbol: str
    side: str              # "buy" or "sell"
    level: int             # 订单层级
    price: float
    amount: float
    created_at: float      # 创建时间
    last_updated_at: float # 最后被认领时间


@dataclass
class OrderIntent:
    """
    订单意图 - 子类计算后返回

    描述"想要"在什么价格挂什么单，由基类统一处理订单生命周期。
    """
    side: str              # "buy" or "sell"
    level: int             # 订单层级（用于追踪）
    price: float           # 目标价格
    amount: float          # 数量
    timeout: float         # 超时时间（秒）
    refresh_tolerance: float  # 刷新容忍度


class BaseExecutor(Listener):
    """
    执行器基类

    职责：
    - 每个 tick 从 StrategyGroup 获取聚合的目标仓位
    - 计算当前仓位与目标的差值
    - 当差值超过阈值时执行交易
    - 管理限价单生命周期（复用、取消）

    核心参数：
        per_order_usd: 单笔订单大小 / 执行阈值（USD）
            - delta 超过此值才会执行
            - 每次执行的订单大小
        cancel_delay: 取消延迟（秒）
            - 订单未被认领超过此时间才取消

    子类需要实现：
        execute_delta(): 执行具体的交易逻辑
    """

    def __init__(self, config: "BaseExecutorConfig"):
        """
        初始化执行器

        Args:
            config: 执行器配置对象
        """
        super().__init__(name=config.path, interval=config.interval)
        self.config = config

        # 状态
        self._executor_state = ExecutorState.IDLE

        # 限价单管理
        self._active_orders: dict[tuple[str, str, str, int], ActiveOrder] = {}
        # key = (exchange_name, symbol, side, level)

        # 执行统计
        self._stats = {
            "ticks": 0,
            "executions": 0,
            "orders_created": 0,
            "orders_cancelled": 0,
            "orders_reused": 0,
            "orders_failed": 0,
        }

    # ===== 属性 =====

    @property
    def exchange_group(self) -> "ExchangeGroup":
        """获取 ExchangeGroup"""
        return self.root.exchange_group

    @property
    def strategy_group(self) -> "StrategyGroup":
        """获取 StrategyGroup"""
        return self.root.strategy_group

    def _get_exchange_by_path(self, exchange_path: str) -> Optional["BaseExchange"]:
        """根据配置路径获取交易所实例"""
        for exchange in self.exchange_group.children.values():
            if exchange.config.path == exchange_path:
                return exchange
        return None

    @property
    def executor_state(self) -> ExecutorState:
        return self._executor_state

    @property
    @abstractmethod
    def per_order_usd(self) -> float:
        """获取单笔订单大小（子类必须实现）"""
        ...

    @property
    def cancel_delay(self) -> float:
        """获取取消延迟（子类可覆盖）"""
        return getattr(self.config, 'cancel_delay', 5.0)

    @property
    def stats(self) -> dict:
        return self._stats.copy()

    @property
    def active_orders_count(self) -> int:
        return len(self._active_orders)

    # ===== 工具方法 =====

    def usd_to_amount(
        self,
        exchange: "BaseExchange",
        symbol: str,
        usd: float,
        price: float,
    ) -> float:
        """
        将 USD 价值转换为下单数量（合约数量）

        计算公式：
            base_amount = usd / price  # 基础货币数量（如 BTC）
            amount = base_amount / contract_size  # 合约数量

        Args:
            exchange: 交易所实例
            symbol: 交易对
            usd: USD 价值（可正可负）
            price: 当前价格

        Returns:
            合约数量（保持 usd 的正负符号）
        """
        if price <= 0:
            return 0.0
        base_amount = usd / price
        contract_size = exchange.get_contract_size(symbol)
        return base_amount / contract_size

    @staticmethod
    def _order_key(exchange_name: str, symbol: str, side: str, level: int) -> tuple:
        """生成订单 key"""
        return (exchange_name, symbol, side, level)

    # ===== 限价单管理（通用逻辑）=====

    async def manage_limit_orders(
        self,
        exchange: "BaseExchange",
        symbol: str,
        intents: list[OrderIntent],
        mid_price: float,
    ) -> tuple[int, int, int]:
        """
        管理限价单：复用、创建、取消

        核心逻辑：
        1. 对每个 intent，检查是否有可复用的订单
        2. 可复用 → 刷新 last_updated_at
        3. 不可复用 → 创建新订单
        4. 过期订单 → 批量取消

        Args:
            exchange: 交易所实例
            symbol: 交易对
            intents: 订单意图列表
            mid_price: 中间价（用于计算容忍度）

        Returns:
            (created, cancelled, reused) 数量统计
        """
        import time
        now = time.time()

        orders_to_create: list[tuple[OrderIntent, tuple]] = []  # [(intent, key)]
        created = 0
        cancelled = 0
        reused = 0

        # 1. 处理每个 intent
        for intent in intents:
            key = self._order_key(exchange.name, symbol, intent.side, intent.level)
            active = self._active_orders.get(key)

            if active:
                # 检查是否可复用
                if self._can_reuse_order(active, intent, mid_price, now):
                    active.last_updated_at = now
                    reused += 1
                    continue
                else:
                    # 标记为过期（等待后续批量取消）
                    active.last_updated_at = now - self.cancel_delay - 1

            # 需要创建新订单
            orders_to_create.append((intent, key))

        # 2. 收集过期订单
        orders_to_cancel = self._collect_expired_orders(exchange.name, symbol, now)

        # 3. 批量创建新订单（先创建，确保始终在市场上）
        if orders_to_create:
            created = await self._batch_create_orders(
                exchange, symbol, orders_to_create, now
            )

        # 4. 批量取消过期订单
        if orders_to_cancel:
            cancelled = await self._batch_cancel_orders(
                exchange, symbol, orders_to_cancel
            )

        # 更新统计
        self._stats["orders_created"] += created
        self._stats["orders_cancelled"] += cancelled
        self._stats["orders_reused"] += reused

        return created, cancelled, reused

    def _can_reuse_order(
        self,
        order: ActiveOrder,
        intent: OrderIntent,
        mid_price: float,
        now: float,
    ) -> bool:
        """
        检查订单是否可复用

        条件：
        1. 未超时（created_at < timeout）
        2. 价格在容忍范围内
        """
        # 超时检查
        if now - order.created_at > intent.timeout:
            return False

        # 价格容忍度检查
        old_spread = abs(order.price - mid_price)
        price_deviation = abs(intent.price - order.price)

        if old_spread > 0:
            return price_deviation / old_spread <= intent.refresh_tolerance
        return price_deviation < 1e-9  # 几乎相同价格

    def _collect_expired_orders(
        self,
        exchange_name: str,
        symbol: str,
        now: float,
    ) -> list[ActiveOrder]:
        """收集过期订单"""
        expired = []
        for key, order in list(self._active_orders.items()):
            if order.exchange_name != exchange_name or order.symbol != symbol:
                continue

            # 条件：last_updated_at 超过 cancel_delay
            if now - order.last_updated_at > self.cancel_delay:
                expired.append(order)

        return expired

    async def _batch_create_orders(
        self,
        exchange: "BaseExchange",
        symbol: str,
        to_create: list[tuple[OrderIntent, tuple]],
        now: float,
    ) -> int:
        """批量创建订单"""
        if not to_create:
            return 0

        requests = []
        for intent, _ in to_create:
            requests.append({
                "symbol": symbol,
                "type": "limit",
                "side": intent.side,
                "amount": intent.amount,
                "price": intent.price,
            })

        created = 0
        try:
            results = await exchange.create_orders(requests)
            for (intent, key), result in zip(to_create, results):
                if result and result.get("id"):
                    self._active_orders[key] = ActiveOrder(
                        order_id=result["id"],
                        exchange_name=exchange.name,
                        symbol=symbol,
                        side=intent.side,
                        level=intent.level,
                        price=intent.price,
                        amount=intent.amount,
                        created_at=now,
                        last_updated_at=now,
                    )
                    created += 1
                    self.logger.info(
                        "[%s] L%d %s %s @ %.6f",
                        exchange.name, intent.level, intent.side.upper(),
                        symbol, intent.price
                    )
        except Exception as e:
            self.logger.warning("Failed to create orders: %s", e)
            self._stats["orders_failed"] += len(to_create)

        return created

    async def _batch_cancel_orders(
        self,
        exchange: "BaseExchange",
        symbol: str,
        to_cancel: list[ActiveOrder],
    ) -> int:
        """批量取消订单"""
        if not to_cancel:
            return 0

        # 去重
        seen = set()
        unique = []
        for o in to_cancel:
            if o.order_id not in seen:
                seen.add(o.order_id)
                unique.append(o)

        cancel_ids = [o.order_id for o in unique]
        cancelled = 0

        try:
            await exchange.cancel_orders(cancel_ids, symbol)
            cancelled = len(unique)
            for order in unique:
                key = self._order_key(
                    order.exchange_name, order.symbol, order.side, order.level
                )
                # 只移除 order_id 匹配的（避免误删新订单）
                current = self._active_orders.get(key)
                if current and current.order_id == order.order_id:
                    self._active_orders.pop(key, None)
                self.logger.debug(
                    "[%s] Cancelled L%d %s %s",
                    exchange.name, order.level, order.side, order.order_id
                )
        except Exception as e:
            self.logger.warning("Failed to cancel orders: %s", e)
            # 仍然从追踪中移除
            for order in unique:
                key = self._order_key(
                    order.exchange_name, order.symbol, order.side, order.level
                )
                current = self._active_orders.get(key)
                if current and current.order_id == order.order_id:
                    self._active_orders.pop(key, None)

        return cancelled

    async def cancel_all_orders(self) -> int:
        """
        取消所有活跃订单

        用于 on_stop 或紧急情况。
        """
        if not self._active_orders:
            return 0

        # 按 (exchange_name, symbol) 分组
        by_key: dict[tuple[str, str], list[ActiveOrder]] = {}
        for order in self._active_orders.values():
            key = (order.exchange_name, order.symbol)
            if key not in by_key:
                by_key[key] = []
            by_key[key].append(order)

        total_cancelled = 0
        for (exchange_name, symbol), orders in by_key.items():
            try:
                exchange = self.exchange_group.children.get(exchange_name)
                if not exchange:
                    self.logger.warning("Exchange %s not found", exchange_name)
                    continue

                cancel_ids = [o.order_id for o in orders]
                await exchange.cancel_orders(cancel_ids, symbol)
                total_cancelled += len(orders)
                self.logger.info(
                    "[%s] Cancelled %d orders for %s",
                    exchange_name, len(orders), symbol
                )
            except Exception as e:
                self.logger.warning("Failed to cancel orders: %s", e)

        self._active_orders.clear()
        return total_cancelled

    # ===== 抽象方法 =====

    @abstractmethod
    async def execute_delta(
        self,
        exchange: "BaseExchange",
        symbol: str,
        delta_usd: float,
        speed: float,
        current_price: float,
    ) -> ExecutionResult:
        """
        执行仓位调整

        子类必须实现此方法，处理具体的下单逻辑。

        Args:
            exchange: 交易所实例
            symbol: 交易对
            delta_usd: 需要调整的 USD 价值（正=买入，负=卖出）
            speed: 执行紧急度 [0, 1]
            current_price: 当前价格

        Returns:
            执行结果
        """
        ...

    # ===== 生命周期 =====

    async def on_tick(self) -> bool:
        """
        主循环：获取目标仓位，计算差值，执行交易
        """
        self._stats["ticks"] += 1

        if self._executor_state == ExecutorState.PAUSED:
            return False

        # 获取聚合的目标仓位
        targets = self.strategy_group.get_aggregated_targets()

        if not targets:
            return False

        self._executor_state = ExecutorState.EXECUTING

        try:
            await self._process_targets(targets)
        finally:
            self._executor_state = ExecutorState.IDLE

        return False

    async def _process_targets(self, targets: "AggregatedTargets") -> None:
        """
        处理所有目标仓位

        Args:
            targets: {(exchange_path, symbol): (target_usd, speed)}
        """
        for (exchange_path, symbol), (target_usd, speed) in targets.items():
            # 根据 exchange_path 获取交易所
            exchange = self._get_exchange_by_path(exchange_path)

            if not exchange:
                self.logger.debug("Exchange not found for path %s", exchange_path)
                continue

            try:
                await self._process_single_target(
                    exchange, symbol, target_usd, speed
                )
            except Exception as e:
                self.logger.warning(
                    "[%s] Error processing %s: %s",
                    exchange.name, symbol, e
                )

    async def _process_single_target(
        self,
        exchange: "BaseExchange",
        symbol: str,
        target_usd: float,
        speed: float,
    ) -> Optional[ExecutionResult]:
        """
        处理单个目标仓位

        Args:
            exchange: 交易所实例
            symbol: 交易对
            target_usd: 目标仓位（USD）
            speed: 执行紧急度

        Returns:
            执行结果，如果未执行则返回 None
        """
        # 1. 获取当前价格
        try:
            ticker = await exchange.fetch_ticker(symbol)
            price = ticker.get('last', 0)
            if price <= 0:
                return None
        except Exception as e:
            self.logger.debug("[%s] Failed to get ticker for %s: %s", exchange.name, symbol, e)
            return None

        # 2. 获取当前仓位
        try:
            positions = await exchange.medal_fetch_positions()
            current_amount = positions.get(symbol, 0.0)
            current_usd = current_amount * price
        except Exception as e:
            self.logger.debug("[%s] Failed to get positions: %s", exchange.name, e)
            current_usd = 0.0

        # 3. 计算差值
        delta_usd = target_usd - current_usd

        # 4. 检查是否需要执行
        # always=True 时跳过阈值检查（market making 模式）
        if not self.config.always and abs(delta_usd) < self.per_order_usd:
            return None  # 差值太小，不执行

        self._stats["executions"] += 1

        # 5. 执行交易（限制单笔大小）
        # 如果 delta 很大，分多次执行，每次最多 per_order_usd
        execute_usd = delta_usd
        if abs(execute_usd) > self.per_order_usd:
            execute_usd = self.per_order_usd if delta_usd > 0 else -self.per_order_usd

        self.logger.info(
            "[%s] %s: target=%.2f, current=%.2f, delta=%.2f, execute=%.2f USD",
            exchange.name, symbol, target_usd, current_usd, delta_usd, execute_usd
        )

        # 6. 调用子类实现的执行方法
        result = await self.execute_delta(
            exchange=exchange,
            symbol=symbol,
            delta_usd=execute_usd,
            speed=speed,
            current_price=price,
        )

        return result

    async def on_stop(self):
        """停止时取消所有活跃订单"""
        await self.cancel_all_orders()
        await super().on_stop()

    # ===== 控制方法 =====

    def pause(self) -> None:
        """暂停执行"""
        self._executor_state = ExecutorState.PAUSED
        self.logger.info("Executor paused")

    def resume(self) -> None:
        """恢复执行"""
        self._executor_state = ExecutorState.IDLE
        self.logger.info("Executor resumed")

    # ===== 状态 =====

    @property
    def log_state_dict(self) -> dict:
        return {
            "state": self._executor_state.value,
            "per_order_usd": self.per_order_usd,
            "active_orders": self.active_orders_count,
            **self._stats
        }

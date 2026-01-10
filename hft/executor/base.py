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
    from ..exchange.group import ExchangeGroups
    from ..exchange.base import BaseExchange
    from ..strategy.group import StrategyGroup, AggregatedTargets


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


class BaseExecutor(Listener):
    """
    执行器基类

    职责：
    - 每个 tick 从 StrategyGroup 获取聚合的目标仓位
    - 计算当前仓位与目标的差值
    - 当差值超过阈值时执行交易

    核心参数：
        per_order_usd: 单笔订单大小 / 执行阈值（USD）
            - delta 超过此值才会执行
            - 每次执行的订单大小

    子类需要实现：
        execute_delta(): 执行具体的交易逻辑
    """

    def __init__(self, name: str = "Executor"):
        """
        初始化执行器

        Args:
            name: 执行器名称

        配置参数从 root.config 获取：
            - executor_interval: tick 间隔（秒）
            - executor_per_order_usd: 单笔订单大小 / 执行阈值（USD）
        """
        super().__init__(name=name, interval=1.0)  # interval 会在 on_start 时更新

        # 状态
        self._executor_state = ExecutorState.IDLE

        # 执行统计
        self._stats = {
            "ticks": 0,
            "executions": 0,
            "orders_created": 0,
            "orders_failed": 0,
        }

    async def on_start(self):
        """启动时从 root.config 获取配置"""
        await super().on_start()
        self.interval = self.root.config.executor_interval

    # ===== 属性 =====

    @property
    def exchange_groups(self) -> "ExchangeGroups":
        """获取 ExchangeGroups"""
        return self.root.exchange_groups

    @property
    def strategy_group(self) -> "StrategyGroup":
        """获取 StrategyGroup"""
        return self.root.strategy_group

    @property
    def executor_state(self) -> ExecutorState:
        return self._executor_state

    @property
    def per_order_usd(self) -> float:
        """从 root.config 获取单笔订单大小"""
        return self.root.config.executor_per_order_usd

    @property
    def stats(self) -> dict:
        return self._stats.copy()

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
            targets: {exchange_class: {symbol: (target_usd, speed)}}
        """
        for exchange_class, symbols in targets.items():
            # 获取该类型的所有交易所
            exchanges = self.exchange_groups.get_exchanges_by_class(exchange_class)

            if not exchanges:
                self.logger.debug("No exchanges for class %s", exchange_class)
                continue

            for symbol, (target_usd, speed) in symbols.items():
                for exchange in exchanges:
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
        if abs(delta_usd) < self.per_order_usd:
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

        if result.success:
            self._stats["orders_created"] += 1
        else:
            self._stats["orders_failed"] += 1

        return result

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
            **self._stats
        }

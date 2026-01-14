"""
SmartExecutor - 智能路由执行器

根据市场条件动态选择最优子执行器：
- 显式路由：exchange.config.executor_map[symbol] 优先
- 速度阈值：speed > speed_threshold 使用 market（确保快速成交）
- 自动选择：基于公共 trades 数据判断 taker 是否有优势
- 默认回退：数据不足时使用 default_executor

设计原则：
- SmartExecutor 自身只做路由，不执行订单
- 子执行器使用 lazy_start=True，不独立 tick
- 委托 execute_delta 给选中的子执行器
"""
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional
from .base import BaseExecutor, ExecutionResult

if TYPE_CHECKING:
    from .config import SmartExecutorConfig
    from ..exchange.base import BaseExchange
    from ..datasource.trades_datasource import TradeData


@dataclass
class RoutingDecision:
    """路由决策结果"""
    executor_key: str           # 选中的执行器 key
    rule: str                   # 命中的规则
    edge_usd: Optional[float]   # taker 优势（USD），仅自动选择时有值
    trades_count: int           # 参与计算的 trades 数量


class SmartExecutor(BaseExecutor):
    """
    智能路由执行器

    路由规则（优先级从高到低）：
    1. 显式路由：exchange.config.executor_map[symbol]
    2. 速度阈值：speed > speed_threshold → market
    3. 自动选择：计算 taker 优势，正则 market，否则 as
    4. 默认回退：default_executor
    """

    def __init__(self, config: "SmartExecutorConfig"):
        super().__init__(config)
        self.config: "SmartExecutorConfig" = config

        # 子执行器实例缓存
        self._child_executors: dict[str, BaseExecutor] = {}

        # 路由统计
        self._routing_stats: dict[str, int] = {}  # {rule: count}

    # ===== 属性 =====

    @property
    def per_order_usd(self) -> float:
        """使用默认执行器的 per_order_usd"""
        default = self._get_child_executor(self.config.default_executor)
        if default:
            return default.per_order_usd
        return 100.0  # fallback

    # ===== 生命周期 =====

    async def on_start(self) -> None:
        """启动时加载子执行器配置"""
        await self._load_child_executors()

    async def _load_child_executors(self) -> None:
        """加载并初始化子执行器"""
        from .config import BaseExecutorConfig

        for key, config_path in self.config.children.items():
            try:
                # 加载配置并创建实例
                child_config = BaseExecutorConfig.load(config_path)
                child = child_config.instance

                # 设置为 lazy_start，不独立 tick
                child.lazy_start = True
                child.enabled = False

                # 添加为子节点
                self.add_child(child)
                self._child_executors[key] = child

                self.logger.info("Loaded child executor: %s -> %s", key, config_path)
            except Exception as e:
                self.logger.error("Failed to load child executor %s: %s", key, e)

    def _get_child_executor(self, key: str) -> Optional[BaseExecutor]:
        """获取子执行器"""
        return self._child_executors.get(key)

    async def on_stop(self) -> None:
        """停止时取消所有子执行器的订单"""
        for child in self._child_executors.values():
            try:
                await child.cancel_all_orders()
            except Exception as e:
                self.logger.warning("Failed to cancel orders for %s: %s", child.name, e)
        await super().on_stop()

    # ===== 路由逻辑 =====

    def _route(
        self,
        exchange: "BaseExchange",
        symbol: str,
        delta_usd: float,
        speed: float,
        current_price: float,
    ) -> RoutingDecision:
        """
        路由决策

        Args:
            exchange: 交易所实例
            symbol: 交易对
            delta_usd: 仓位差值（正=买，负=卖）
            speed: 执行紧急度 [0, 1]
            current_price: 当前价格

        Returns:
            RoutingDecision 包含选中的执行器和决策信息
        """
        # 规则 1: 显式路由
        executor_key = exchange.config.executor_map.get(symbol)
        if executor_key and executor_key in self._child_executors:
            return RoutingDecision(
                executor_key=executor_key,
                rule="explicit",
                edge_usd=None,
                trades_count=0,
            )

        # 规则 2: 速度阈值
        if speed > self.config.speed_threshold:
            if "market" in self._child_executors:
                return RoutingDecision(
                    executor_key="market",
                    rule="speed_threshold",
                    edge_usd=None,
                    trades_count=0,
                )

        # 规则 3: 自动选择（基于 trades 数据）
        decision = self._auto_select(exchange, symbol, delta_usd, current_price)
        if decision:
            return decision

        # 规则 4: 默认回退
        return RoutingDecision(
            executor_key=self.config.default_executor,
            rule="default",
            edge_usd=None,
            trades_count=0,
        )

    def _auto_select(
        self,
        exchange: "BaseExchange",
        symbol: str,
        delta_usd: float,
        current_price: float,
    ) -> Optional[RoutingDecision]:
        """
        基于 trades 数据自动选择执行器

        计算 taker 优势：
        - 买入方向：edge = buy_qty * (p_final - vwap_buy) - taker_fee * buy_notional
        - 卖出方向：edge = sell_qty * (vwap_sell - p_final) - taker_fee * sell_notional

        如果 edge > 0，说明 taker 近期能覆盖成本，选 market；否则选 as。

        Args:
            exchange: 交易所实例
            symbol: 交易对
            delta_usd: 仓位差值
            current_price: 当前价格

        Returns:
            RoutingDecision 或 None（数据不足）
        """
        # 获取 trades 数据
        trades = self._get_recent_trades(exchange, symbol)
        if len(trades) < self.config.min_trades:
            return None  # 数据不足，回退默认

        # 计算 taker 优势
        is_buy = delta_usd > 0
        edge_usd = self._calculate_taker_edge(
            trades, is_buy, current_price, exchange.config.swap_taker_fee
        )

        # 选择执行器
        if edge_usd > 0:
            executor_key = "market"
        else:
            executor_key = "as"

        # 确保选中的执行器存在
        if executor_key not in self._child_executors:
            executor_key = self.config.default_executor

        return RoutingDecision(
            executor_key=executor_key,
            rule="auto_select",
            edge_usd=edge_usd,
            trades_count=len(trades),
        )

    def _get_recent_trades(
        self,
        exchange: "BaseExchange",
        symbol: str,
    ) -> list["TradeData"]:
        """
        获取最近的 trades 数据

        从 TradingPairDataSource 的 TradesDataSource 获取缓存数据
        """
        try:
            # 尝试从 datasource_group 获取 trades
            datasource_group = self.root.datasource_group
            pair_key = f"{exchange.class_name}:{symbol}"

            if pair_key not in datasource_group.children:
                return []

            pair = datasource_group.children[pair_key]

            # 获取 trades datasource
            if not hasattr(pair, 'trades_datasource') or pair.trades_datasource is None:
                return []

            # 获取时间窗口内的 trades
            now = time.time() * 1000  # 转为毫秒
            window_ms = self.config.trades_window_seconds * 1000
            cutoff = now - window_ms

            trades = []
            for trade in pair.trades_datasource.data:
                if trade.timestamp >= cutoff:
                    trades.append(trade)

            return trades
        except Exception as e:
            self.logger.debug("Failed to get trades for %s: %s", symbol, e)
            return []

    def _calculate_taker_edge(
        self,
        trades: list["TradeData"],
        is_buy: bool,
        current_price: float,
        taker_fee: float,
    ) -> float:
        """
        计算 taker 优势

        公式：
        - 买入：edge = buy_qty * (p_final - vwap_buy) - taker_fee * buy_notional
        - 卖出：edge = sell_qty * (vwap_sell - p_final) - taker_fee * sell_notional

        Args:
            trades: 成交记录列表
            is_buy: 是否为买入方向
            current_price: 当前价格
            taker_fee: taker 手续费率

        Returns:
            edge（USD），正值表示 taker 有优势
        """
        if not trades:
            return 0.0

        # 按方向分类统计
        buy_qty = 0.0
        buy_notional = 0.0
        sell_qty = 0.0
        sell_notional = 0.0

        for trade in trades:
            if trade.side == "buy":
                buy_qty += trade.amount
                buy_notional += trade.cost
            else:
                sell_qty += trade.amount
                sell_notional += trade.cost

        if is_buy:
            # 买入方向
            if buy_qty <= 0:
                return 0.0
            vwap_buy = buy_notional / buy_qty
            edge = buy_qty * (current_price - vwap_buy) - taker_fee * buy_notional
        else:
            # 卖出方向
            if sell_qty <= 0:
                return 0.0
            vwap_sell = sell_notional / sell_qty
            edge = sell_qty * (vwap_sell - current_price) - taker_fee * sell_notional

        return edge

    # ===== 执行 =====

    async def execute_delta(
        self,
        exchange: "BaseExchange",
        symbol: str,
        delta_usd: float,
        speed: float,
        current_price: float,
    ) -> ExecutionResult:
        """
        执行仓位调整（委托给选中的子执行器）

        Args:
            exchange: 交易所实例
            symbol: 交易对
            delta_usd: 需要调整的 USD 价值
            speed: 执行紧急度 [0, 1]
            current_price: 当前价格

        Returns:
            执行结果
        """
        # 路由决策
        decision = self._route(exchange, symbol, delta_usd, speed, current_price)

        # 更新统计
        self._routing_stats[decision.rule] = self._routing_stats.get(decision.rule, 0) + 1

        # 记录路由日志
        self.logger.info(
            "[%s] %s: route=%s (rule=%s, edge=%.2f, trades=%d)",
            exchange.name,
            symbol,
            decision.executor_key,
            decision.rule,
            decision.edge_usd or 0.0,
            decision.trades_count,
        )

        # 获取子执行器
        child = self._get_child_executor(decision.executor_key)
        if not child:
            self.logger.error(
                "Child executor not found: %s, fallback to %s",
                decision.executor_key,
                self.config.default_executor,
            )
            child = self._get_child_executor(self.config.default_executor)
            if not child:
                return ExecutionResult(
                    exchange_class=exchange.class_name,
                    symbol=symbol,
                    exchange_name=exchange.name,
                    success=False,
                    error="No child executor available",
                )

        # 委托执行
        return await child.execute_delta(
            exchange=exchange,
            symbol=symbol,
            delta_usd=delta_usd,
            speed=speed,
            current_price=current_price,
        )

    # ===== 状态 =====

    @property
    def log_state_dict(self) -> dict:
        return {
            **super().log_state_dict,
            "children": list(self._child_executors.keys()),
            "routing_stats": self._routing_stats.copy(),
        }

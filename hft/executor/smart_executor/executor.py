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
import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Any

from simpleeval import simple_eval, NameNotDefined

from ..base import BaseExecutor, ExecutionResult

if TYPE_CHECKING:
    from .config import SmartExecutorConfig
    from ...datasource.trades_datasource import TradeData
    from ...exchange.base import BaseExchange


@dataclass
class RoutingDecision:
    """路由决策结果"""
    executor_key: str           # 选中的执行器 key
    rule: str                   # 命中的规则
    edge_usd: Optional[float]   # taker 优势（USD），仅自动选择时有值
    trades_count: int           # 参与计算的 trades 数量


@dataclass
class RoutingRule:
    """
    路由规则定义（阶段 0：基础设施）

    .. deprecated::
        RoutingRule 已被 config.RouteConfig (Pydantic 模型) 替代。
        此数据类仅作为阶段 0 设计参考保留，实际使用应采用 RouteConfig。

    用于配置化路由决策，支持条件表达式。

    Attributes:
        condition: 条件表达式（可选，None 表示无条件匹配）
                  示例："speed > 0.9", "len(trades) > 50 and notional > 10000"
        executor: 目标执行器 key（None 表示不执行）
        priority: 规则优先级（数字越小优先级越高，默认 0）

    Examples:
        >>> RoutingRule(condition="speed > 0.9", executor="market", priority=1)
        >>> RoutingRule(condition="len(trades) > 50", executor="as", priority=2)
        >>> RoutingRule(condition=None, executor="limit", priority=999)  # 默认规则

    See Also:
        config.RouteConfig: 推荐使用的 Pydantic 配置模型
    """
    condition: Optional[str] = None
    executor: Optional[str] = None
    priority: int = 0


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

        # 订单归属追踪（阶段 0：基础设施）
        # 记录每个 (exchange_path, symbol) 当前使用的执行器
        self._executor_mapping: dict[tuple[str, str], str] = {}
        # (exchange_path, symbol) -> executor_key

        # 记录每个 (exchange_path, symbol) 的活跃订单 ID
        self._active_orders: dict[tuple[str, str], list[str]] = {}
        # (exchange_path, symbol) -> [order_ids]

        # 并发保护锁
        self._tracking_lock = asyncio.Lock()

        # 路由上下文缓存（阶段 3：性能优化）
        # 缓存 trades/edge/notional 计算结果，避免同一 tick 重复计算
        self._route_context_cache: dict[tuple[str, str], dict] = {}
        # (exchange_path, symbol) -> {trades, edge, notional, timestamp}

    # ===== 订单追踪 CRUD（阶段 0：完善）=====

    async def _track_order(
        self,
        exchange_path: str,
        symbol: str,
        executor_key: str,
        order_ids: list[str],
    ) -> None:
        """
        记录订单归属

        Args:
            exchange_path: 交易所路径
            symbol: 交易对
            executor_key: 执行器 key
            order_ids: 订单 ID 列表
        """
        async with self._tracking_lock:
            key = (exchange_path, symbol)
            self._executor_mapping[key] = executor_key
            self._active_orders[key] = order_ids.copy()

    async def _get_tracked_executor(
        self,
        exchange_path: str,
        symbol: str,
    ) -> Optional[str]:
        """
        获取当前追踪的执行器

        Args:
            exchange_path: 交易所路径
            symbol: 交易对

        Returns:
            执行器 key 或 None
        """
        async with self._tracking_lock:
            key = (exchange_path, symbol)
            return self._executor_mapping.get(key)

    async def _get_tracked_orders(
        self,
        exchange_path: str,
        symbol: str,
    ) -> list[str]:
        """
        获取当前追踪的订单 ID

        Args:
            exchange_path: 交易所路径
            symbol: 交易对

        Returns:
            订单 ID 列表
        """
        async with self._tracking_lock:
            key = (exchange_path, symbol)
            return self._active_orders.get(key, []).copy()

    async def _clear_tracking(
        self,
        exchange_path: str,
        symbol: str,
    ) -> None:
        """
        清除追踪记录

        Args:
            exchange_path: 交易所路径
            symbol: 交易对
        """
        async with self._tracking_lock:
            key = (exchange_path, symbol)
            self._executor_mapping.pop(key, None)
            self._active_orders.pop(key, None)

    async def _cleanup_stale_tracking(self) -> None:
        """
        清理过期的追踪记录

        定期调用，清理长期无订单的 symbol（防止内存泄漏）
        """
        async with self._tracking_lock:
            # 查找无订单的 symbol
            stale_keys = [
                key for key, orders in self._active_orders.items()
                if not orders
            ]

            # 清理
            for key in stale_keys:
                self._executor_mapping.pop(key, None)
                self._active_orders.pop(key, None)

            if stale_keys:
                self.logger.debug("Cleaned up %d stale tracking records", len(stale_keys))

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
        # 阶段 0：验证路由配置
        self._validate_routes()

    async def _load_child_executors(self) -> None:
        """加载并初始化子执行器"""
        for key, config_path in self.config.children.items():
            try:
                # 加载配置并创建实例（config_path 现在是 ExecutorConfigPath）
                child_config = config_path.instance
                child = child_config.instance

                # 设置为 lazy_start，不独立 tick
                child.lazy_start = True
                child.enabled = False

                # 添加为子节点
                self.add_child(child)
                self._child_executors[key] = child

                self.logger.info("Loaded child executor: %s -> %s", key, config_path.name)
            except Exception as e:
                self.logger.error("Failed to load child executor %s: %s", key, e)

    def _validate_routes(self) -> None:
        """
        验证路由配置（阶段 0：增强）

        检查：
        1. default_executor 是否存在于 children 中
        2. routes 中引用的执行器是否都存在于 children 中
        3. 条件表达式语法是否正确（包括变量名检查）
        4. priority 是否有重复（警告）
        5. 是否有默认回退规则（condition=None 的规则）

        Raises:
            ValueError: 配置验证失败
        """
        # 检查 default_executor 是否存在
        if self.config.default_executor not in self._child_executors:
            raise ValueError(
                f"Default executor '{self.config.default_executor}' not found in children. "
                f"Available: {list(self._child_executors.keys())}"
            )

        # 如果有 routes 配置（阶段 2+ 才会使用）
        if self.config.routes:
            seen_priorities = {}  # {priority: rule_index}
            has_default_rule = False

            # 可用的上下文变量（用于条件表达式验证）
            # NOTE: 这里列出所有可能在路由条件中使用的变量
            available_vars = {
                # 内置变量
                'direction': 1,  # 示例值
                'buy': True,  # 示例值
                'sell': False,  # 示例值
                'speed': 0.0,  # 示例值
                # SmartExecutor 特有变量
                'notional': 0.0,  # 成交额（trades_notional）
                'target_notional': 0.0,  # 目标差额（abs(delta_usd)）
                'trades': [],  # 示例值
                'edge': 0.0,  # 示例值
                'trades_notional': 0.0,  # 成交额（与 notional 相同）
                # Indicator 注入变量（常见示例）
                'mid_price': 0.0,  # 示例值
                'medal_edge': 0.0,  # 示例值
                'volume': 0.0,  # 示例值
                'rsi': 50.0,  # 示例值
            }

            for idx, rule in enumerate(self.config.routes):
                # 1. 检查执行器引用（阶段 2：允许 executor=None 表示不执行）
                if rule.executor is not None and rule.executor not in self._child_executors:
                    raise ValueError(
                        f"Route {idx}: Executor '{rule.executor}' not found in children. "
                        f"Available: {list(self._child_executors.keys())}"
                    )

                # 2. 检查条件表达式语法和变量名
                if rule.condition:
                    try:
                        # 使用完整上下文测试，确保所有变量都能识别
                        # NOTE: 函数必须通过 functions 参数传递
                        simple_eval(
                            rule.condition,
                            names=available_vars,
                            functions=self.SAFE_FUNCTIONS,
                        )
                    except NameNotDefined as e:
                        # 变量名未定义 - 这是配置错误
                        raise ValueError(
                            f"Route {idx}: Undefined variable in condition '{rule.condition}': {e}. "
                            f"Available variables: {list(available_vars.keys())}"
                        )
                    except SyntaxError as e:
                        # 语法错误
                        raise ValueError(
                            f"Route {idx}: Invalid condition syntax '{rule.condition}': {e}"
                        )
                    except Exception as e:
                        # 其他错误（如类型错误、除零等）
                        raise ValueError(
                            f"Route {idx}: Condition evaluation error '{rule.condition}': {e}"
                        )
                else:
                    # condition=None 表示默认规则
                    has_default_rule = True

                # 3. 检查 priority 重复
                if rule.priority in seen_priorities:
                    self.logger.warning(
                        "Route %d has duplicate priority %d (same as route %d). "
                        "Rules will be evaluated in definition order.",
                        idx, rule.priority, seen_priorities[rule.priority]
                    )
                else:
                    seen_priorities[rule.priority] = idx

            # 4. 检查是否有默认回退规则
            if not has_default_rule:
                self.logger.info(
                    "No default route rule (condition=None) found. "
                    "Will fall back to default_executor '%s' if no rules match.",
                    self.config.default_executor
                )

        self.logger.info("Route configuration validated successfully")

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

    # ===== 表达式求值（阶段 0：完善）=====

    # 安全的内置函数白名单
    SAFE_FUNCTIONS = {
        'len': len,
        'abs': abs,
        'min': min,
        'max': max,
        'sum': sum,
        'round': round,
    }

    def _evaluate_condition(self, expression: str, context: dict[str, Any]) -> bool:
        """
        安全地求值条件表达式

        使用 simpleeval 库限制可用函数，防止代码注入攻击。
        只允许白名单中的函数和显式提供的上下文变量。

        Args:
            expression: 条件表达式字符串（如 "speed > 0.9"）
            context: 求值上下文（可用变量）

        Returns:
            bool: 表达式求值结果（错误时返回 False）

        Examples:
            >>> self._evaluate_condition("speed > 0.9", {"speed": 0.95})
            True
            >>> self._evaluate_condition("len(trades) > 50", {"trades": [1,2,3]})
            False
        """
        try:
            # 使用 simpleeval 安全求值
            # NOTE: 函数必须通过 functions 参数传递，变量通过 names 参数传递
            result = simple_eval(
                expression,
                names=context,
                functions=self.SAFE_FUNCTIONS,
            )
            return bool(result)
        except NameNotDefined as e:
            # 变量未定义（配置错误）- 记录错误而非静默
            self.logger.error(
                "Variable not defined in condition '%s': %s. Available variables: %s",
                expression, e, list(context.keys())
            )
            return False  # fail-safe
        except ZeroDivisionError as e:
            # 除零错误
            self.logger.warning("Division by zero in condition '%s': %s", expression, e)
            return False
        except Exception as e:
            # 其他错误（语法错误、类型错误等）
            self.logger.error("Condition evaluation failed: '%s' - %s", expression, e)
            return False  # fail-safe

    # ===== 路由逻辑 =====

    def _get_route_context(
        self,
        exchange: "BaseExchange",
        symbol: str,
        delta_usd: float,
        speed: float,
        current_price: float,
    ) -> dict:
        """
        获取路由上下文（Feature 0005：支持 Indicator 变量注入）

        构建条件表达式的求值上下文，包括：
        1. 内置变量：direction, buy, sell, speed, notional
        2. SmartExecutor 特有变量：trades, edge, trades_notional
        3. Indicator 注入变量：通过 requires 声明的 indicator 提供

        使用缓存机制避免同一 tick 周期内重复计算。

        Args:
            exchange: 交易所实例
            symbol: 交易对
            delta_usd: 仓位差值
            speed: 执行紧急度
            current_price: 当前价格

        Returns:
            上下文字典，包含所有可用变量
        """
        direction = 1 if delta_usd > 0 else -1

        # 1. 从 BaseExecutor 获取基础上下文（内置变量 + Indicator 变量）
        context = self.collect_context_vars(
            exchange_class=exchange.class_name,
            symbol=symbol,
            direction=direction,
            speed=speed,
            notional=abs(delta_usd),
        )

        # 2. 检查缓存（SmartExecutor 特有变量）
        cache_key = (exchange.name, symbol)
        current_time = time.time()

        cached = self._route_context_cache.get(cache_key)
        cache_ttl_seconds = 1.0  # 每个 symbol 独立缓存 1 秒

        # 缓存过期或不存在：重新计算
        if cached is None or (current_time - float(cached.get('timestamp', 0.0)) > cache_ttl_seconds):

            # 获取 trades 数据
            trades = self._get_recent_trades(exchange, symbol)

            # 计算 edge 和 trades_notional
            if trades:
                is_buy = delta_usd > 0
                edge = self._calculate_taker_edge(
                    trades, is_buy, current_price, exchange.config.swap_taker_fee
                )

                # 计算 trades_notional（该方向的成交额）
                if is_buy:
                    trades_notional = sum(t.cost for t in trades if t.side == "buy")
                else:
                    trades_notional = sum(t.cost for t in trades if t.side == "sell")
            else:
                # 数据缺失时的 fail-safe 默认值
                edge = 0.0
                trades_notional = 0.0

            # 更新缓存
            self._route_context_cache[cache_key] = {
                'trades': trades,
                'edge': edge,
                'trades_notional': trades_notional,
                'timestamp': current_time,
            }

        # 3. 从缓存获取 SmartExecutor 特有变量
        cached = self._route_context_cache[cache_key]

        # SmartExecutor 路由上下文中，notional 表示成交额（trades_notional）
        # target_notional 表示目标差额（abs(delta_usd)）
        context['target_notional'] = context['notional']  # 保留目标差额
        context['notional'] = cached['trades_notional']   # 覆盖为成交额

        context.update({
            'trades': cached['trades'],
            'edge': cached['edge'],
            'trades_notional': cached['trades_notional'],
        })

        return context

    def _route(
        self,
        exchange: "BaseExchange",
        symbol: str,
        delta_usd: float,
        speed: float,
        current_price: float,
    ) -> RoutingDecision:
        """
        路由决策（阶段 3：高级条件扩展）

        优先级（自高到低）：
        1. 显式路由：exchange.config.executor_map[symbol]
        2. 规则匹配：config.routes（按 priority 排序，自上而下）
        3. 速度阈值：speed > speed_threshold（保持向后兼容）
        4. 自动选择：基于 trades 数据（保持向后兼容）
        5. 默认回退：default_executor

        阶段 3 新增：规则匹配支持 trades/edge/notional 变量

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

        # 规则 2: 规则匹配（阶段 2/3）
        if self.config.routes:
            # 构建条件求值上下文（阶段 3：支持 trades/edge/notional）
            context = self._get_route_context(
                exchange, symbol, delta_usd, speed, current_price
            )

            # 按 priority 排序（数字越小优先级越高）
            sorted_routes = sorted(self.config.routes, key=lambda r: r.priority)

            for route in sorted_routes:
                # 无条件规则（默认分支）
                if route.condition is None:
                    return RoutingDecision(
                        executor_key=route.executor,
                        rule="route_default",
                        edge_usd=context.get('edge'),
                        trades_count=len(context.get('trades', [])),
                    )

                # 条件匹配
                if self._evaluate_condition(route.condition, context):
                    return RoutingDecision(
                        executor_key=route.executor,
                        rule=f"route_matched:{route.condition}",
                        edge_usd=context.get('edge'),
                        trades_count=len(context.get('trades', [])),
                    )

        # 规则 3: 速度阈值（保持向后兼容）
        if speed > self.config.speed_threshold:
            if "market" in self._child_executors:
                return RoutingDecision(
                    executor_key="market",
                    rule="speed_threshold",
                    edge_usd=None,
                    trades_count=0,
                )

        # 规则 4: 自动选择（基于 trades 数据，保持向后兼容）
        decision = self._auto_select(exchange, symbol, delta_usd, current_price)
        if decision:
            return decision

        # 规则 5: 默认回退
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

        从 IndicatorGroup 的 TradesDataSource 获取缓存数据
        """
        try:
            # 使用新架构 IndicatorGroup 获取 trades
            indicator_group = getattr(self.root, 'indicator_group', None)
            if indicator_group is None:
                return []

            trades_ds = indicator_group.get_indicator(
                "trades", exchange.class_name, symbol
            )
            if trades_ds is None:
                return []

            # 获取时间窗口内的 trades
            now = time.time() * 1000  # 转为毫秒
            window_ms = self.config.trades_window_seconds * 1000
            cutoff = now - window_ms

            trades = []
            for trade in trades_ds.data:
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
        计算 taker 优势（相对值）

        公式（量纲无关）：
        - 买入：edge = (p_final - vwap_buy) / p_final - taker_fee
        - 卖出：edge = (vwap_sell - p_final) / p_final - taker_fee

        Args:
            trades: 成交记录列表
            is_buy: 是否为买入方向
            current_price: 当前价格
            taker_fee: taker 手续费率

        Returns:
            edge（比例），正值表示 taker 有优势，如 0.001 表示 0.1%
        """
        if not trades or current_price <= 0:
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
            edge = (current_price - vwap_buy) / current_price - taker_fee
        else:
            # 卖出方向
            if sell_qty <= 0:
                return 0.0
            vwap_sell = sell_notional / sell_qty
            edge = (vwap_sell - current_price) / current_price - taker_fee

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

        阶段 1：实现切换清理逻辑
        - 先下新单（调用新执行器的 execute_delta）
        - 成功则取消旧单，更新映射
        - 失败则保持旧状态不变

        Args:
            exchange: 交易所实例
            symbol: 交易对
            delta_usd: 需要调整的 USD 价值
            speed: 执行紧急度 [0, 1]
            current_price: 当前价格

        Returns:
            执行结果
        """
        # 0. 检查 SmartExecutor 自己的 requires ready gate（Feature 0005）
        # 所有 BaseExecutor 行为应该一致
        if not self.check_requires_ready(exchange.class_name, symbol):
            return ExecutionResult(
                exchange_class=exchange.class_name,
                exchange_name=exchange.name,
                symbol=symbol,
                success=False,
                delta_usd=delta_usd,
                error="SmartExecutor requires not ready",
            )

        exchange_path = exchange.name
        tracking_key = (exchange_path, symbol)

        # 1. 获取当前追踪的执行器
        current_executor_key = await self._get_tracked_executor(exchange_path, symbol)

        # 2. 路由决策（选择新执行器）
        decision = self._route(exchange, symbol, delta_usd, speed, current_price)
        new_executor_key = decision.executor_key

        # 3. 更新统计
        self._routing_stats[decision.rule] = self._routing_stats.get(decision.rule, 0) + 1

        # 4. 记录路由日志
        if current_executor_key and current_executor_key != new_executor_key:
            # 发生切换
            self.logger.info(
                "[%s] %s: switch executor %s -> %s (rule=%s, edge=%.2f, trades=%d)",
                exchange.name,
                symbol,
                current_executor_key,
                new_executor_key or "None",  # None 显示为 "None"
                decision.rule,
                decision.edge_usd or 0.0,
                decision.trades_count,
            )
        else:
            # 首次路由或保持不变
            self.logger.info(
                "[%s] %s: route=%s (rule=%s, edge=%.2f, trades=%d)",
                exchange.name,
                symbol,
                new_executor_key or "None",  # None 显示为 "None"
                decision.rule,
                decision.edge_usd or 0.0,
                decision.trades_count,
            )

        # 5. 处理不执行模式（阶段 2：executor=None）
        if new_executor_key is None:
            # 路由到 None，表示不执行
            # 如果有旧订单，需要取消
            if current_executor_key:
                old_executor = self._get_child_executor(current_executor_key)
                if old_executor:
                    try:
                        cancelled = await old_executor.cancel_orders_for_symbol(
                            exchange.name, symbol
                        )
                        if cancelled > 0:
                            self.logger.info(
                                "[%s] %s: cancelled %d orders (no execution)",
                                exchange.name,
                                symbol,
                                cancelled,
                            )
                    except Exception as e:
                        self.logger.warning(
                            "[%s] %s: failed to cancel orders: %s",
                            exchange.name,
                            symbol,
                            e,
                        )

            # 清理追踪记录
            await self._clear_tracking(exchange_path, symbol)

            # 返回成功结果（没有实际下单）
            return ExecutionResult(
                exchange_class=exchange.class_name,
                symbol=symbol,
                exchange_name=exchange.name,
                success=True,
                delta_usd=0.0,  # 没有实际执行
            )

        # 6. 获取新执行器
        new_executor = self._get_child_executor(new_executor_key)
        if not new_executor:
            self.logger.error(
                "Child executor not found: %s, fallback to %s",
                new_executor_key,
                self.config.default_executor,
            )
            new_executor = self._get_child_executor(self.config.default_executor)
            new_executor_key = self.config.default_executor

            if not new_executor:
                return ExecutionResult(
                    exchange_class=exchange.class_name,
                    symbol=symbol,
                    exchange_name=exchange.name,
                    success=False,
                    error="No child executor available",
                )

        # 7. 检查子 executor 的 condition（Feature 0005）
        child_condition = getattr(new_executor, 'condition', None)
        if child_condition is not None:
            direction = 1 if delta_usd > 0 else -1
            child_context = new_executor.collect_context_vars(
                exchange_class=exchange.class_name,
                symbol=symbol,
                direction=direction,
                speed=speed,
                notional=abs(delta_usd),
            )
            child_context["mid_price"] = current_price
            if not new_executor.evaluate_condition(child_context):
                self.logger.debug(
                    "[%s] %s: child executor %s condition not met, skipping",
                    exchange.name, symbol, new_executor_key
                )
                return ExecutionResult(
                    exchange_class=exchange.class_name,
                    symbol=symbol,
                    exchange_name=exchange.name,
                    success=True,
                    delta_usd=0.0,
                )

        # 8. 执行新单
        result = await new_executor.execute_delta(
            exchange=exchange,
            symbol=symbol,
            delta_usd=delta_usd,
            speed=speed,
            current_price=current_price,
        )

        # 9. 切换清理逻辑（阶段 1）
        if result.success:
            # 新单成功
            # 9a. 更新追踪映射
            # NOTE: 这里简化处理，使用空订单列表
            # 因为 execute_delta 不直接返回订单 ID 列表
            # 实际订单由子执行器的 _active_orders 管理
            await self._track_order(exchange_path, symbol, new_executor_key, [])

            # 9b. 如果发生切换，取消旧执行器的订单
            if current_executor_key and current_executor_key != new_executor_key:
                old_executor = self._get_child_executor(current_executor_key)
                if old_executor:
                    try:
                        cancelled = await old_executor.cancel_orders_for_symbol(
                            exchange.name, symbol
                        )
                        if cancelled > 0:
                            self.logger.info(
                                "[%s] %s: cancelled %d old orders from %s",
                                exchange.name,
                                symbol,
                                cancelled,
                                current_executor_key,
                            )
                    except Exception as e:
                        # 旧单取消失败只记录警告，不影响新单（边界情况处理）
                        self.logger.warning(
                            "[%s] %s: failed to cancel old orders from %s: %s",
                            exchange.name,
                            symbol,
                            current_executor_key,
                            e,
                        )
        else:
            # 新单失败，保持旧状态不变（边界情况处理）
            self.logger.warning(
                "[%s] %s: new executor %s failed, keeping old executor %s",
                exchange.name,
                symbol,
                new_executor_key,
                current_executor_key or "none",
            )

        return result

    # ===== 状态 =====

    @property
    def log_state_dict(self) -> dict:
        return {
            **super().log_state_dict,
            "children": list(self._child_executors.keys()),
            "routing_stats": self._routing_stats.copy(),
        }

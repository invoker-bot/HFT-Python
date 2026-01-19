"""
Strategy 策略基类

策略只负责计算目标仓位，不负责执行。执行由 Executor 统一处理。

核心接口：
    get_target_positions_usd() -> TargetPositions
    返回策略期望的目标仓位（USD 计价）和执行紧急度

数据流：
    Executor.on_tick()
        -> 遍历所有 Strategy.get_target_positions_usd()
        -> 聚合目标仓位（position sum, speed 加权平均）
        -> 计算与当前仓位的差值
        -> 执行交易

退出流程：
1. Strategy.on_tick() 返回 True -> 策略从 StrategyGroup 中移除
2. StrategyGroup.is_finished 变为 True -> StrategyGroup.on_tick() 返回 True
3. AppCore.on_tick() 检测到策略组完成 -> 返回 True -> 程序正常退出

Feature 0008: Strategy 数据驱动增强
- 支持通用字典输出（StrategyOutput）
- 向后兼容旧格式（TargetPositions）
- 支持 requires、vars、conditional_vars
"""
import time
from abc import abstractmethod
from typing import Optional, Any, Union, TYPE_CHECKING
from ..core.listener import Listener
from .config import BaseStrategyConfig

if TYPE_CHECKING:
    from ..exchange.base import BaseExchange
    from ..indicator.base import BaseIndicator
    from .group import StrategyGroup


# 旧版目标仓位类型（向后兼容）: {(exchange_path, symbol): (position_usd, speed)}
# exchange_path: 交易所配置路径，如 "okx/main"
# symbol: 交易对，如 "BTC/USDT:USDT"
# position_usd: 正数=多仓，负数=空仓，单位 USD
# speed: 执行紧急度 [0.0, 1.0]，越高越急
TargetPositions = dict[tuple[str, str], tuple[float, float]]

# 新版 Strategy 输出类型（Feature 0008）: {(exchange_path, symbol): {"字段名": 值, ...}}
# 支持任意字段，如 position_usd, speed, position_amount, max_position_usd 等
# 所有字段都会传递给 Executor，聚合到 strategies namespace
StrategyOutput = dict[tuple[str, str], dict[str, Any]]


class BaseStrategy(Listener):
    """
    策略基类

    策略的核心职责是计算目标仓位，不直接执行交易。
    Executor 会在每个 tick 调用 get_target_positions_usd() 获取目标，
    然后根据与当前仓位的差值决定是否执行交易。

    核心方法：
        get_target_positions_usd() -> TargetPositions
            返回 {exchange_class: {symbol: (position_usd, speed)}}
            - position_usd: 目标仓位价值（USD），正数=多仓，负数=空仓
            - speed: 执行紧急度 [0.0, 1.0]

    多策略聚合：
        - position_usd: 直接求和
        - speed: 按仓位绝对值加权平均

    退出机制：
        当策略完成任务后，on_tick() 返回 True 即可触发退出。

    Feature 0008: 数据驱动增强
        - requires: 依赖的 Indicator 列表
        - vars: 变量列表（按顺序计算）
        - conditional_vars: 条件变量（条件满足时更新）

    Example:
        class MyStrategy(BaseStrategy):
            def get_target_positions_usd(self) -> TargetPositions:
                return {
                    "okx": {
                        "BTC/USDT:USDT": (5000.0, 0.3),  # $5000 多仓，不急
                        "ETH/USDT:USDT": (-2000.0, 0.8), # $2000 空仓，较急
                    }
                }

            async def on_tick(self) -> bool:
                # 策略逻辑（更新内部状态等）
                if self.should_exit():
                    return True
                return False

    Attributes:
        strategy_group: 所属的策略组（通过 parent 访问）
    """

    @property
    def strategy_group(self) -> Optional["StrategyGroup"]:
        """获取所属的策略组"""
        parent = self.parent
        from .group import StrategyGroup
        if isinstance(parent, StrategyGroup):
            return parent
        return None

    def __init__(self, config: 'BaseStrategyConfig'):
        super().__init__(name=config.path, interval=config.interval)
        self.config = config

        # Feature 0008: conditional_vars 状态持久化
        # {变量名: (当前值, 上次更新时间)}
        self._conditional_var_states: dict[str, tuple[Any, float]] = {}

        # Feature 0012: Scope 系统
        self.scope_manager: Optional['ScopeManager'] = None
        self.scope_trees: list[list['BaseScope']] = []

    # ============================================================
    # Feature 0008: 变量计算机制
    # ============================================================

    @property
    def indicator_group(self):
        """获取 IndicatorGroup"""
        if self.root is None:
            return None
        return getattr(self.root, 'indicator_group', None)

    def _get_indicator(
        self,
        indicator_id: str,
        exchange_class: Optional[str],
        symbol: Optional[str],
        exchange_path: Optional[str] = None,
    ) -> Optional["BaseIndicator"]:
        """获取 Indicator 实例"""
        indicator_group = self.indicator_group
        if indicator_group is None:
            return None
        return indicator_group.get_indicator(
            indicator_id, exchange_class, symbol, exchange_path=exchange_path
        )

    def collect_context_vars(
        self,
        exchange_path: str,
        symbol: str,
    ) -> dict[str, Any]:
        """
        收集上下文变量（Feature 0008）

        收集顺序：
        1. requires 中 Indicator 的变量
        2. vars 列表（按顺序计算，后面可引用前面）
        3. conditional_vars（条件满足时更新）

        Args:
            exchange_path: 交易所路径（如 "okx/main"）
            symbol: 交易对

        Returns:
            变量字典
        """
        context: dict[str, Any] = {}

        # 从 exchange_path 解析 exchange_class
        exchange_class = exchange_path.split('/')[0] if '/' in exchange_path else exchange_path

        # 1. 从 requires 中的 Indicator 收集变量
        for indicator_id in getattr(self.config, 'requires', []) or []:
            indicator = self._get_indicator(
                indicator_id, exchange_class, symbol, exchange_path=exchange_path
            )
            if indicator and indicator.is_ready():
                try:
                    vars_dict = indicator.calculate_vars(direction=0)
                    context.update(vars_dict)
                except Exception as e:
                    self.logger.warning(
                        "Failed to get vars from indicator %s: %s",
                        indicator_id, e
                    )

        # 2. 计算 vars 列表（支持条件变量）
        now = time.time()
        for var_def in getattr(self.config, 'vars', []) or []:
            try:
                # 检查是否有条件（on 字段）
                if hasattr(var_def, 'on') and var_def.on:
                    # 条件变量：检查条件是否满足
                    condition_met = self._safe_eval_bool(var_def.on, context)

                    if condition_met:
                        # 条件满足，更新值
                        value = self._safe_eval(var_def.value, context)
                        self._conditional_var_states[var_def.name] = (value, now)
                        context[var_def.name] = value
                    else:
                        # 条件不满足，使用缓存值或初始值
                        if var_def.name in self._conditional_var_states:
                            cached_value, _ = self._conditional_var_states[var_def.name]
                            context[var_def.name] = cached_value
                        else:
                            # 首次且条件不满足，使用 initial_value
                            initial = getattr(var_def, 'initial_value', None)
                            context[var_def.name] = initial
                            self._conditional_var_states[var_def.name] = (initial, 0.0)
                else:
                    # 普通变量：每次都计算
                    value = self._safe_eval(var_def.value, context)
                    context[var_def.name] = value
            except Exception as e:
                self.logger.warning(
                    "Failed to compute var %s: %s",
                    var_def.name, e
                )

        # 3. 计算 conditional_vars（DEPRECATED - 向后兼容）
        # 新代码应使用 vars 的 on 字段
        for var_name, var_def in (getattr(self.config, 'conditional_vars', {}) or {}).items():
            # 获取当前状态
            current_value, last_update = self._conditional_var_states.get(
                var_name, (var_def.default, 0.0)
            )

            # 检查条件
            try:
                condition_met = self._safe_eval_bool(var_def.on, context)
            except Exception as e:
                self.logger.warning(
                    "Failed to evaluate condition for %s: %s",
                    var_name, e
                )
                condition_met = False

            if condition_met:
                # 条件满足，更新值
                try:
                    new_value = self._safe_eval(var_def.value, context)
                    self._conditional_var_states[var_name] = (new_value, now)
                    context[var_name] = new_value
                except Exception as e:
                    self.logger.warning(
                        "Failed to compute conditional var %s: %s",
                        var_name, e
                    )
                    context[var_name] = current_value
            else:
                # 条件不满足，保持当前值
                context[var_name] = current_value

        return context

    def _safe_eval(self, expr: str, context: dict[str, Any]) -> Any:
        """安全求值表达式"""
        from simpleeval import EvalWithCompoundTypes, DEFAULT_OPERATORS

        # 辅助函数
        def avg(values):
            if not values:
                return 0.0
            return sum(values) / len(values)

        def clip(value, min_val, max_val):
            return max(min_val, min(max_val, value))

        safe_functions = {
            'len': len,
            'abs': abs,
            'min': min,
            'max': max,
            'sum': sum,
            'round': round,
            'avg': avg,
            'clip': clip,
        }

        evaluator = EvalWithCompoundTypes(
            names=context,
            functions=safe_functions,
            operators=DEFAULT_OPERATORS,
        )

        try:
            return evaluator.eval(expr)
        except Exception as e:
            self.logger.warning("Expression eval failed: %s - %s", expr, e)
            return None

    def _safe_eval_bool(self, expr: str, context: dict[str, Any]) -> bool:
        """安全求值布尔表达式"""
        result = self._safe_eval(expr, context)
        return bool(result) if result is not None else False

    # ============================================================
    # Feature 0012: Scope 系统集成
    # ============================================================

    def _get_filtered_symbols(self) -> list[str]:
        """
        获取过滤后的交易对列表

        根据 include_symbols 和 exclude_symbols 配置过滤交易对。

        Returns:
            符合过滤条件的交易对列表
        """
        from fnmatch import fnmatch

        # 获取所有可用的交易对（从所有 exchange 收集）
        all_symbols = set()
        if self.root is not None and hasattr(self.root, 'exchange_group'):
            for exchange in self.root.exchange_group.children.values():
                # 从 exchange 的 markets 获取 symbols
                if hasattr(exchange, 'markets') and exchange.markets:
                    all_symbols.update(exchange.markets.keys())

        # 如果没有找到任何 symbols，返回空列表
        if not all_symbols:
            return []

        # 应用 include_symbols 过滤
        included = set()
        for pattern in self.config.include_symbols:
            if pattern == '*':
                included.update(all_symbols)
            else:
                for symbol in all_symbols:
                    if fnmatch(symbol, pattern):
                        included.add(symbol)

        # 应用 exclude_symbols 过滤
        excluded = set()
        for pattern in self.config.exclude_symbols:
            for symbol in included:
                if fnmatch(symbol, pattern):
                    excluded.add(symbol)

        # 返回过滤后的结果
        return list(included - excluded)

    def _register_custom_scopes(self) -> None:
        """
        注册自定义 Scope 类型（由子类重写）

        子类可以重写此方法来注册自己的 Scope 类型。
        例如：
            self.scope_manager.register_scope_class(
                "CustomScope",
                CustomScope
            )
        """
        pass

    def _build_scope_trees(self) -> None:
        """
        根据 links 配置构建 Scope 树

        此方法会：
        1. 遍历所有 links
        2. 为每条 link 构建 Scope 树（遍历每一层的所有 children）
        3. 将叶子节点存储到 self.scope_trees

        Links 展开规则：
        - Links 定义层级关系，而非单一路径
        - 展开时会遍历每一层的所有 children
        - 例如 ["global", "exchange", "trading_pair"] 会展开为：
          - 第一层：global (1个实例)
          - 第二层：所有 exchange (如 okx/main, binance/spot)
          - 第三层：每个 exchange 的所有 trading_pair
        """
        if not self.config.links:
            return

        if self.scope_manager is None:
            self.logger.warning("ScopeManager not initialized")
            return

        # 构建 instance_ids_provider 函数
        def instance_ids_provider(scope_class_id: str, parent_scope) -> list[str]:
            """
            获取指定 scope_class_id 的所有实例 ID

            Args:
                scope_class_id: Scope 类型 ID（如 "global", "exchange"）
                parent_scope: 父 Scope（用于获取上下文信息）

            Returns:
                实例 ID 列表
            """
            scope_config = self.config.scopes.get(scope_class_id)
            if scope_config and scope_config.instance_id:
                # 如果配置中指定了 instance_id，直接使用
                return [scope_config.instance_id]

            # 否则根据 scope_class_id 对应的 class_name 动态获取
            if not scope_config:
                self.logger.warning(
                    "No config found for scope_class_id=%s, returning empty list",
                    scope_class_id
                )
                return []

            class_name = scope_config.class_name

            # 根据 Scope 类型动态获取实例 ID
            if class_name == "GlobalScope":
                # GlobalScope 通常只有一个实例
                return ["global"]

            elif class_name == "ExchangeClassScope":
                # 获取所有 exchange class 名称
                if self.root is None or not hasattr(self.root, 'exchange_group'):
                    return []
                exchange_classes = set()
                for exchange in self.root.exchange_group.children.values():
                    exchange_classes.add(exchange.class_name)
                return sorted(exchange_classes)

            elif class_name == "ExchangeScope":
                # 获取所有 exchange path
                if self.root is None or not hasattr(self.root, 'exchange_group'):
                    return []
                exchange_paths = []
                for exchange in self.root.exchange_group.children.values():
                    exchange_paths.append(exchange.config.path)
                return sorted(exchange_paths)

            elif class_name == "TradingPairClassScope":
                # 获取所有唯一的 symbol（从配置的 include_symbols/exclude_symbols）
                # 这里返回所有符合过滤条件的 symbol
                symbols = self._get_filtered_symbols()
                return sorted(symbols)

            elif class_name == "TradingPairScope":
                # 获取特定 exchange 的所有 trading pair
                # 需要从 parent_scope 获取 exchange_path
                if parent_scope is None:
                    return []

                # 根据 parent 类型获取 exchange_path
                exchange_path = None
                if parent_scope.scope_class_id == "exchange":
                    # 父节点是 ExchangeScope
                    exchange_path = parent_scope.get_var("exchange_path")
                elif parent_scope.scope_class_id == "trading_pair_class":
                    # 父节点是 TradingPairClassScope
                    # 从 parent 的 instance_id 获取 symbol
                    symbol = parent_scope.scope_instance_id

                    # 需要从更上层获取 exchange_path
                    # 向上遍历找到 ExchangeScope 或 ExchangeClassScope
                    current = parent_scope.parent
                    while current:
                        if current.scope_class_id == "exchange":
                            exchange_path = current.get_var("exchange_path")
                            break
                        elif current.scope_class_id == "exchange_class":
                            # ExchangeClassScope，需要获取该 class 的所有 exchange
                            exchange_class = current.scope_instance_id
                            if self.root and hasattr(self.root, 'exchange_group'):
                                exchange_paths = []
                                for exchange in self.root.exchange_group.children.values():
                                    if exchange.class_name == exchange_class:
                                        exchange_paths.append(exchange.config.path)
                                # 为每个 exchange 创建 trading_pair instance_id
                                return [f"{ep}:{symbol}" for ep in sorted(exchange_paths)]
                            return []
                        current = current.parent

                    # 如果没有找到 exchange 上下文，返回空列表
                    if not exchange_path:
                        return []

                    # 返回单个 trading_pair instance_id
                    return [f"{exchange_path}:{symbol}"]

                if not exchange_path:
                    return []

                # 获取该 exchange 的所有 symbols
                symbols = self._get_filtered_symbols()
                # 为每个 symbol 构建 trading_pair instance_id: "exchange_path:symbol"
                return [f"{exchange_path}:{symbol}" for symbol in sorted(symbols)]

            else:
                # 未知类型，返回空列表
                self.logger.warning(
                    "Unknown scope class_name=%s for scope_class_id=%s",
                    class_name, scope_class_id
                )
                return []

        # 构建 scope_configs 字典
        scope_configs = {}
        for scope_class_id, scope_config in self.config.scopes.items():
            scope_configs[scope_class_id] = {
                "class": scope_config.class_name,
                "instance_id": scope_config.instance_id
            }

        # 为每条 link 构建 Scope 树
        self.scope_trees = []
        for link in self.config.links:
            leaf_scopes = self.scope_manager.build_scope_tree(
                link=link,
                scope_configs=scope_configs,
                instance_ids_provider=instance_ids_provider
            )
            self.scope_trees.append(leaf_scopes)

    def _evaluate_targets(self, scope, exchange_path: str, symbol: str) -> dict:
        """
        匹配并求值 targets 配置

        Args:
            scope: 当前 Scope
            exchange_path: Exchange 路径
            symbol: 交易对

        Returns:
            求值后的字段字典，如果没有匹配的 target 则返回 None
        """
        if not self.config.targets:
            return None

        # 获取 exchange class
        exchange_class = None
        if self.root and hasattr(self.root, 'exchange_group'):
            for exchange in self.root.exchange_group.children.values():
                if exchange.config.path == exchange_path:
                    exchange_class = exchange.class_name
                    break

        # 遍历所有 targets，找到匹配的
        for target in self.config.targets:
            # 匹配 exchange
            if not self._match_pattern(target.exchange, exchange_path):
                continue

            # 匹配 exchange_class
            if exchange_class and not self._match_pattern(target.exchange_class, exchange_class):
                continue

            # 匹配 symbol
            if target.symbol != symbol:
                continue

            # 检查 target 级 condition
            if target.condition:
                try:
                    context = dict(scope._vars)
                    context['parent'] = scope.parent
                    context['children'] = scope.children
                    result = self._safe_eval(target.condition, context)
                    if not result:
                        continue
                except Exception as e:
                    self.logger.warning(
                        "Target condition evaluation failed for %s:%s: %s",
                        exchange_path, symbol, e
                    )
                    continue

            # 求值所有字段
            output = {}
            context = dict(scope._vars)
            context['parent'] = scope.parent
            context['children'] = scope.children

            # 求值标准字段
            for field_name in ["position_usd", "position_amount", "max_position_usd"]:
                field_value = getattr(target, field_name, None)
                if field_value is not None:
                    try:
                        output[field_name] = self._safe_eval(field_value, context)
                    except Exception as e:
                        self.logger.warning(
                            "Failed to evaluate %s for %s:%s: %s",
                            field_name, exchange_path, symbol, e
                        )

            # speed 是 float，直接使用
            if target.speed is not None:
                output["speed"] = target.speed

            # 求值额外字段（通过 model_extra）
            if hasattr(target, '__pydantic_extra__'):
                for field_name, field_value in target.__pydantic_extra__.items():
                    if isinstance(field_value, str):
                        try:
                            output[field_name] = self._safe_eval(field_value, context)
                        except Exception as e:
                            self.logger.warning(
                                "Failed to evaluate extra field %s for %s:%s: %s",
                                field_name, exchange_path, symbol, e
                            )
                    else:
                        output[field_name] = field_value

            return output

        return None

    def _match_pattern(self, pattern: str, value: str) -> bool:
        """
        匹配模式（支持通配符）

        Args:
            pattern: 模式字符串（如 '*', 'okx', 'okx/*'）
            value: 要匹配的值

        Returns:
            是否匹配
        """
        from fnmatch import fnmatch
        return pattern == "*" or fnmatch(value, pattern)

    def _get_all_trading_pairs(self) -> list[tuple[str, str]]:
        """
        获取所有需要处理的 (exchange_path, symbol) 对

        Returns:
            [(exchange_path, symbol), ...]
        """
        pairs = []

        if self.root is None or not hasattr(self.root, 'exchange_group'):
            return pairs

        # 获取过滤后的 symbols
        symbols = self._get_filtered_symbols()

        # 遍历所有 exchange
        for exchange in self.root.exchange_group.children.values():
            exchange_path = exchange.config.path

            # 为每个 symbol 创建 pair
            for symbol in symbols:
                # 检查该 exchange 是否支持该 symbol
                if hasattr(exchange, 'markets') and exchange.markets:
                    if symbol in exchange.markets:
                        pairs.append((exchange_path, symbol))

        return pairs

    def _get_or_create_scope_for_target(
        self,
        exchange_path: str,
        symbol: str,
        link_index: int = 0
    ) -> Optional['BaseScope']:
        """
        按需创建或获取指定 exchange_path 和 symbol 的 target_scope

        根据 links 配置，沿着 Scope 路径创建或获取 Scope。

        Args:
            exchange_path: Exchange 路径
            symbol: 交易对
            link_index: 使用哪条 link（默认第一条）

        Returns:
            target_scope 层级的 Scope，如果无法创建则返回 None
        """
        if not self.config.links or not self.scope_manager:
            return None

        if link_index >= len(self.config.links):
            return None

        link = self.config.links[link_index]

        # 从 exchange_path 解析 exchange_class
        exchange_class = exchange_path.split('/')[0] if '/' in exchange_path else exchange_path

        # 沿着 link 路径创建 Scope
        current_scope = None
        for scope_class_id in link:
            scope_config = self.config.scopes.get(scope_class_id, {})
            class_name = scope_config.class_name if scope_config else "GlobalScope"

            # 根据 scope 类型确定 instance_id
            if class_name == "GlobalScope":
                instance_id = "global"
            elif class_name == "ExchangeClassScope":
                instance_id = exchange_class
            elif class_name == "ExchangeScope":
                instance_id = exchange_path
            elif class_name == "TradingPairClassScope":
                instance_id = symbol
            elif class_name == "TradingPairScope":
                instance_id = f"{exchange_path}:{symbol}"
            else:
                # 未知类型
                self.logger.warning("Unsupported scope class: %s", class_name)
                return None

            # 创建或获取 Scope
            current_scope = self.scope_manager.get_or_create(
                scope_class_name=class_name,
                scope_class_id=scope_class_id,
                scope_instance_id=instance_id,
                parent=current_scope
            )

            # 设置基础变量
            if class_name == "GlobalScope":
                pass  # GlobalScope 不需要设置额外变量
            elif class_name == "ExchangeClassScope":
                current_scope.set_var("exchange_class", exchange_class)
            elif class_name == "ExchangeScope":
                current_scope.set_var("exchange_path", exchange_path)
                current_scope.set_var("exchange_class", exchange_class)
            elif class_name == "TradingPairClassScope":
                current_scope.set_var("symbol", symbol)
            elif class_name == "TradingPairScope":
                current_scope.set_var("exchange_path", exchange_path)
                current_scope.set_var("exchange_class", exchange_class)
                current_scope.set_var("symbol", symbol)

        return current_scope

    def _inject_indicator_vars_to_scope(self, scope: 'BaseScope') -> None:
        """
        注入 Indicator 变量到 Scope

        Args:
            scope: 目标 Scope
        """
        # 获取 exchange_path 和 symbol
        exchange_path = scope.get_var("exchange_path")
        symbol = scope.get_var("symbol")

        if not exchange_path or not symbol:
            return

        # 从 exchange_path 解析 exchange_class
        exchange_class = exchange_path.split('/')[0] if '/' in exchange_path else exchange_path

        # 从 requires 中的 Indicator 收集变量
        for indicator_id in self.config.requires:
            indicator = self._get_indicator(
                indicator_id, exchange_class, symbol, exchange_path=exchange_path
            )
            if indicator and indicator.is_ready():
                try:
                    vars_dict = indicator.calculate_vars(direction=0)
                    # 注入到 Scope
                    for var_name, var_value in vars_dict.items():
                        scope.set_var(var_name, var_value)
                except Exception as e:
                    self.logger.warning(
                        "Failed to inject vars from indicator %s to scope %s:%s: %s",
                        indicator_id, scope.scope_class_id, scope.scope_instance_id, e
                    )

    def _compute_scope_vars(self, scope: 'BaseScope') -> None:
        """
        计算 Scope 配置中的 vars

        Args:
            scope: 目标 Scope
        """
        scope_config = self.config.scopes.get(scope.scope_class_id)
        if not scope_config or not scope_config.vars:
            return

        # 获取上下文（包含当前 scope 的变量 + parent/children 符号）
        context = dict(scope._vars)

        # 注入 parent 和 children 符号
        context['parent'] = scope.parent
        context['children'] = scope.children

        # 计算每个 var
        for var_def in scope_config.vars:
            try:
                # 检查条件
                if var_def.on:
                    condition_result = self._safe_eval(var_def.on, context)
                    if not condition_result:
                        # 条件不满足，使用 initial_value
                        if var_def.initial_value is not None:
                            scope.set_var(var_def.name, var_def.initial_value)
                        continue

                # 计算值
                value = self._safe_eval(var_def.value, context)
                scope.set_var(var_def.name, value)

                # 更新上下文
                context[var_def.name] = value

            except Exception as e:
                self.logger.warning(
                    "Failed to compute var %s in scope %s:%s: %s",
                    var_def.name, scope.scope_class_id, scope.scope_instance_id, e
                )

    def get_output(self) -> StrategyOutput:
        """
        获取策略输出（Feature 0012）

        基于 Scope 系统计算策略输出。

        流程：
        1. 获取所有需要处理的 (exchange_path, symbol) 对
        2. 第一次遍历：按需创建 Scope + 注入 Indicator 变量
        3. 第二次遍历：计算 Scope vars + 求值 targets
        4. 应用 condition gate

        Returns:
            {(exchange_path, symbol): {"field": value, ...}}
        """
        if not self.config.links or not self.scope_manager:
            # 如果没有配置 Scope 系统，返回空输出
            return {}

        # 1. 获取所有需要处理的 trading pairs
        trading_pairs = self._get_all_trading_pairs()

        if not trading_pairs:
            return {}

        # 2. 第一次遍历：按需创建 Scope + 注入 Indicator 变量
        # 支持多条 links
        scopes_map = {}  # {(link_index, exchange_path, symbol): scope}
        for link_index in range(len(self.config.links)):
            for exchange_path, symbol in trading_pairs:
                scope = self._get_or_create_scope_for_target(
                    exchange_path, symbol, link_index
                )
                if scope:
                    key = (link_index, exchange_path, symbol)
                    scopes_map[key] = scope
                    self._inject_indicator_vars_to_scope(scope)

        # 3. 第二次遍历：计算 Scope vars + 求值 targets
        output = {}
        for (link_index, exchange_path, symbol), scope in scopes_map.items():
            # 计算 Scope vars（从 root 到 leaf）
            self._compute_scope_vars_recursive(scope)

            # 检查全局 condition
            if self.config.condition:
                try:
                    context = dict(scope._vars)
                    result = self._safe_eval(self.config.condition, context)
                    if not result:
                        continue
                except Exception as e:
                    self.logger.warning("Global condition evaluation failed: %s", e)
                    continue

            # 匹配并求值 targets 配置
            target_output = self._evaluate_targets(scope, exchange_path, symbol)

            if target_output:
                # 如果同一个 (exchange_path, symbol) 有多个 link 的输出
                # 需要合并（这里简单覆盖，实际可能需要更复杂的合并逻辑）
                key = (exchange_path, symbol)
                if key in output:
                    # 合并输出
                    output[key].update(target_output)
                else:
                    output[key] = target_output

        return output

    def _compute_scope_vars_recursive(self, scope: 'BaseScope') -> None:
        """
        递归计算 Scope vars（从 root 到 leaf）

        Args:
            scope: 目标 Scope
        """
        # 先计算 parent 的 vars
        if scope.parent:
            self._compute_scope_vars_recursive(scope.parent)

        # 再计算当前 Scope 的 vars
        self._compute_scope_vars(scope)

    async def on_start(self) -> None:
        """
        启动回调（Feature 0012）

        初始化 Scope 系统。
        """
        await super().on_start()

        # 如果配置了 Scope 系统，进行初始化
        if self.config.links:
            # 获取 ScopeManager
            if self.root is not None:
                self.scope_manager = getattr(self.root, 'scope_manager', None)

            if self.scope_manager is None:
                self.logger.warning("ScopeManager not found in root")
                return

            # 注册自定义 Scope 类型
            self._register_custom_scopes()

            # NOTE: 不再预先构建 Scope 树，改为按需创建（在 get_output() 中）
            # 这样可以支持动态的交易对变化（新币上线等）
            # self._build_scope_trees()

    @abstractmethod
    def get_target_positions_usd(self) -> Union[TargetPositions, StrategyOutput]:
        """
        获取策略的目标仓位

        这是策略的核心输出方法。Executor 会在每个 tick 调用此方法，
        聚合所有策略的目标仓位后执行交易。

        返回格式（支持两种，向后兼容）：

        旧格式 TargetPositions（仍然支持）：
            {(exchange_path, symbol): (position_usd, speed)}

        新格式 StrategyOutput（Feature 0008 推荐）：
            {(exchange_path, symbol): {"position_usd": ..., "speed": ..., ...}}

        新格式可包含任意字段，所有字段都会传递给 Executor，
        聚合到 strategies namespace。

        Example (旧格式):
            return {
                ("okx/main", "BTC/USDT:USDT"): (5000.0, 0.5),
                ("okx/main", "ETH/USDT:USDT"): (-2000.0, 0.8),
            }

        Example (新格式):
            return {
                ("okx/main", "BTC/USDT:USDT"): {
                    "position_usd": 5000.0,
                    "speed": 0.5,
                    "max_position_usd": 10000.0,
                },
            }
        """

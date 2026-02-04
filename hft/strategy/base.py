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
# pylint: disable=import-outside-toplevel,protected-access
import time
from abc import abstractmethod
from collections import deque
from typing import TYPE_CHECKING, Any, Optional, Union
from younotyou import Matcher
from ..core.listener import Listener
# from ..core.scope.instance_ids import get_all_instance_ids
if TYPE_CHECKING:
    from ..core.scope.manager import ScopeManager
    from ..exchange.base import BaseExchange
    from ..indicator.base import BaseIndicator
    from .config import BaseStrategyConfig



# 旧版目标仓位类型（向后兼容）: {(exchange_path, symbol): (position_usd, speed)}
# exchange_path: 交易所配置路径，如 "okx/main"
# symbol: 交易对，如 "BTC/USDT:USDT"
# position_usd: 正数=多仓，负数=空仓，单位 USD
# speed: 执行紧急度 [0.0, 1.0]，越高越急
# TargetPositions = dict[tuple[str, str], tuple[float, float]]

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
    # def __init__(self, config: 'BaseStrategyConfig'):
    #     super().__init__(name=config.path, interval=config.interval)
    #     self.config = config
    #
    #     # Feature 0008: conditional_vars 状态持久化
    #     # {变量名: (当前值, 上次更新时间)}
    #     self._conditional_var_states: dict[str, tuple[Any, float]] = {}
    #
    #     # Feature 0012: Scope 系统
    #     self.scope_manager: Optional['ScopeManager'] = None
    #     self.scope_trees: list['LinkedScopeTree'] = []
    #     # 节点到树的映射（用于快速查找节点所属的树）
    #     self._node_to_tree: dict['LinkedScopeNode', 'LinkedScopeTree'] = {}

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self.config: 'BaseStrategyConfig' = kwargs['config']

    @property
    def scope_manager(self) -> 'ScopeManager':
        return self.root.scope_manager

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

    def _safe_eval(self, expr: str, context: dict[str, Any]) -> Any:
        """
        安全求值表达式（使用 VirtualMachine）

        Args:
            expr: 表达式字符串
            context: 上下文变量字典

        Returns:
            求值结果，失败返回 None
        """
        try:
            # 创建临时 scope 用于求值
            from ..core.scope.base import BaseScope
            temp_scope = BaseScope("temp", "temp")
            temp_scope.update_vars(context)
            return self.vm.eval(expr, temp_scope)
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

        # 应用 include_symbols 和 exclude_symbols 过滤
        matcher = Matcher(
            include_patterns=self.config.include_symbols,
            exclude_patterns=self.config.exclude_symbols
        )
        included = {symbol for symbol in all_symbols if symbol in matcher}

        # 返回过滤后的结果
        return list(included)

    def _get_group_id_for_symbol(self, symbol: str) -> Optional[str]:
        """
        获取交易对的分组 ID

        默认实现：使用 symbol 的第一部分（如 ETH/USDT → ETH）
        子类可以重写此方法提供自定义分组逻辑。

        Args:
            symbol: 交易对（如 "ETH/USDT"）

        Returns:
            分组 ID（如 "ETH"），如果无法确定则返回 None
        """
        # 检查是否有 trading_pair_group 配置（在子类配置中）
        trading_pair_group = getattr(self.config, 'trading_pair_group', None)
        if trading_pair_group and symbol in trading_pair_group:
            return trading_pair_group[symbol]

        # 检查是否有 default_trading_pair_group 表达式
        default_expr = getattr(self.config, 'default_trading_pair_group', None)
        if default_expr:
            try:
                context = {"symbol": symbol}
                return self._safe_eval(default_expr, context)
            except Exception:
                pass

        # 降级：使用 symbol 的第一部分
        if '/' in symbol:
            return symbol.split('/')[0]
        return symbol

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

    def _build_children(
        self,
        parent_node: 'LinkedScopeNode',
        link: list[str],
        index: int
    ) -> None:
        """
        递归构建子节点

        Args:
            parent_node: 父节点
            link: 完整的 link 链路
            index: 当前处理的 link 索引
        """
        from ..core.scope.tree import LinkedScopeNode

        if index >= len(link):
            return

        scope_class_id = link[index]
        instance_ids = self._get_instance_ids(scope_class_id, parent_node)

        for instance_id in instance_ids:
            scope = self._create_scope(scope_class_id, instance_id)
            child_node = LinkedScopeNode(scope=scope, parent=parent_node)
            parent_node.add_child(child_node)

            # 递归构建下一层
            if index + 1 < len(link):
                self._build_children(child_node, link, index + 1)

    def _create_scope(self, scope_class_id: str, instance_id: str) -> 'BaseScope':
        """
        创建 Scope 实例

        Args:
            scope_class_id: Scope 类型 ID
            instance_id: 实例 ID

        Returns:
            Scope 实例
        """
        scope_config = self.config.scopes.get(scope_class_id)
        if not scope_config:
            raise ValueError(f"No config for scope_class_id: {scope_class_id}")

        return self.scope_manager.get_or_create(
            scope_class_name=scope_config.class_name,
            scope_class_id=scope_class_id,
            scope_instance_id=instance_id,
            app_core=self.root,
        )

    def _get_instance_ids(self, scope_class_id: str, parent_node: Optional['LinkedScopeNode']) -> list[str]:
        """
        获取指定 scope_class_id 对于parent node 的所有实例 ID

        Args:
            scope_class_id: Scope 类型 ID（如 "global", "exchange"）
            parent_node: 父 LinkedScopeNode（用于获取上下文信息）

        Returns:
            实例 ID 列表
        """
        scope_config = self.root.config.scopes[scope_class_id]

        # 根据 class_name 动态获取实例 ID
        class_name = scope_config.class_name
        class_type = self.root.scope_manager.all_scopes[class_name]
        instance_ids = get_all_instance_ids(self.root, parent_node.scope, class_type)
        # TODO: 过滤 instance_ids（如 exchange_path、trading_pair 等）
        return instance_ids
    # def _get_exchange_classes(self) -> list[str]:
    #     """获取所有 exchange class 名称"""
    #     if not self.root or not hasattr(self.root, 'exchange_group'):
    #         return []
    #     classes = {ex.class_name for ex in self.root.exchange_group.children.values()}
    #     return sorted(classes)

    def _get_exchange_paths(self) -> list[str]:
        """获取所有 exchange path"""
        if not self.root or not hasattr(self.root, 'exchange_group'):
            return []
        paths = [ex.config.path for ex in self.root.exchange_group.children.values()]
        return sorted(paths)

    def _get_trading_pair_ids(self, parent_node: 'LinkedScopeNode') -> list[str]:
        """获取 trading pair 实例 ID"""
        if not parent_node:
            return []

        parent_scope = parent_node.scope

        # 从 ExchangeScope 父节点获取
        if parent_scope.scope_class_id == "exchange":
            exchange_path = parent_scope.get_var("exchange_path")
            if exchange_path:
                symbols = self._get_filtered_symbols()
                return [f"{exchange_path}:{s}" for s in sorted(symbols)]

        # 从 TradingPairClassScope 父节点获取
        elif parent_scope.scope_class_id == "trading_pair_class":
            symbol = parent_scope.scope_instance_id
            exchange_paths = self._find_exchange_paths_from_ancestors(parent_node)
            if exchange_paths:
                return [f"{ep}:{symbol}" for ep in sorted(exchange_paths)]

        return []

    def _find_exchange_paths_from_ancestors(self, node: 'LinkedScopeNode') -> list[str]:
        """从祖先节点中查找 exchange paths"""
        current = node.parent
        while current:
            scope = current.scope
            if scope.scope_class_id == "exchange":
                return [scope.get_var("exchange_path")]
            elif scope.scope_class_id == "exchange_class":
                return self._get_exchange_paths_by_class(scope.scope_instance_id)
            current = current.parent
        return []

    def _get_exchange_paths_by_class(self, exchange_class: str) -> list[str]:
        """根据 exchange class 获取所有 exchange paths"""
        if not self.root or not hasattr(self.root, 'exchange_group'):
            return []
        paths = [
            ex.config.path for ex in self.root.exchange_group.children.values()
            if ex.class_name == exchange_class
        ]
        return sorted(paths)

    def _get_group_ids(self) -> list[str]:
        """获取所有 group IDs"""
        symbols = self._get_filtered_symbols()
        groups = {self._get_group_id_for_symbol(s) for s in symbols}
        return sorted(g for g in groups if g)

    def _build_node_to_tree_mapping(self, node: 'LinkedScopeNode', tree: 'LinkedScopeTree') -> None:
        """
        递归构建节点到树的映射

        Args:
            node: 当前节点
            tree: 节点所属的树
        """
        self._node_to_tree[node] = tree
        for child in node.children.values():
            self._build_node_to_tree_mapping(child, tree)

    def _evaluate_targets(
        self,
        scope,
        exchange_path: str,
        symbol: str,
        node: 'LinkedScopeNode' = None,
        tree: 'LinkedScopeTree' = None
    ) -> dict:
        """
        匹配并求值 targets 配置（Feature 0012）

        支持新格式（vars 列表）和旧格式（直接字段）。

        Args:
            scope: 当前 Scope（向后兼容，优先使用 node）
            exchange_path: Exchange 路径
            symbol: 交易对
            node: LinkedScopeNode（新 API）
            tree: LinkedScopeTree（新 API）

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

        # 遍历所有 targets，找到匹配的（贪婪匹配：取第一个）
        for target in self.config.targets:
            # 匹配 exchange_id（优先）或 exchange（向后兼容）
            exchange_pattern = target.exchange_id
            if not self._match_pattern(exchange_pattern, exchange_path):
                continue

            # 匹配 exchange_class
            if exchange_class and not self._match_pattern(target.exchange_class, exchange_class):
                continue

            # 匹配 symbol（支持通配符）
            if not self._match_pattern(target.symbol, symbol):
                continue

            # 检查 target 级 condition
            if target.condition:
                try:
                    # 获取变量上下文
                    if node and tree:
                        context = dict(tree.get_vars(node))
                    else:
                        # 向后兼容：直接使用 scope._vars（不包含祖先变量）
                        context = dict(scope._vars)
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
            # 获取变量上下文
            if node and tree:
                context = dict(tree.get_vars(node))
            else:
                # 向后兼容：直接使用 scope._vars（不包含祖先变量）
                context = dict(scope._vars)

            # 新格式：计算 vars 列表
            if target.vars:
                for var_def in target.vars:
                    try:
                        value = self._safe_eval(var_def.value, context)
                        output[var_def.name] = value
                        # 更新上下文，后续 var 可以引用前面的 var
                        context[var_def.name] = value
                    except Exception as e:
                        self.logger.warning(
                            "Failed to evaluate target var %s for %s:%s: %s",
                            var_def.name, exchange_path, symbol, e
                        )

            # 向后兼容：求值标准字段（如果 vars 为空）
            if not target.vars:
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


    def _inject_indicator_vars_to_scope(self, scope: 'BaseScope') -> None:
        """
        注入 Indicator 变量到 Scope

        如果 Indicator not ready，则标记该 scope 及其所有 children 为 not ready。

        Args:
            scope: 目标 Scope
        """
        # 获取 exchange_path 和 symbol
        exchange_path = scope.get_var("exchange_id") or scope.get_var("exchange_path")
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

            if indicator is None:
                # Indicator 不存在，标记为 not ready
                scope.not_ready = True
                continue

            if not indicator.is_ready():
                # Indicator not ready → 标记该 scope 及其所有 children 为 not ready
                scope.not_ready = True
                continue

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
                # 注入失败也标记为 not ready
                scope.not_ready = True

    def _compute_scope_vars(
        self,
        scope: 'BaseScope',
        post: bool = False,
        node: 'LinkedScopeNode' = None,
        tree: 'LinkedScopeTree' = None
    ) -> None:
        """
        计算 Scope 配置中的 vars

        Args:
            scope: 目标 Scope（向后兼容）
            post: 是否只计算 post=True 的 vars（默认 False，只计算 post=False 的 vars）
            node: LinkedScopeNode（新 API）
            tree: LinkedScopeTree（新 API）
        """
        scope_config = self.config.scopes.get(scope.scope_class_id)
        if not scope_config or not scope_config.vars:
            return

        # 获取上下文（包含当前 scope 的变量 + parent/children 符号）
        if node and tree:
            # 新 API：使用 tree.get_vars(node) 获取包含祖先的变量
            context = dict(tree.get_vars(node))
        else:
            # 向后兼容：只使用当前 scope 的变量
            context = dict(scope._vars)

        # 注入 parent 和 children 符号
        if node:
            context['parent'] = node.parent.scope if node.parent else None
            context['children'] = {child.scope.scope_instance_id: child.scope for child in node.children.values()}
        else:
            # 向后兼容：parent 和 children 为 None
            context['parent'] = None
            context['children'] = {}

        # 辅助函数：聚合 children 的变量
        def child_values(children, var_name):
            """收集所有 children 的指定变量值"""
            values = []
            if isinstance(children, dict):
                for child in children.values():
                    val = child.get_var(var_name)
                    if val is not None:
                        values.append(val)
            return values

        context['child_values'] = child_values

        # 计算每个 var（只计算 post 属性匹配的 var）
        for var_def in scope_config.vars:
            # 检查 post 属性是否匹配
            var_post = getattr(var_def, 'post', False)
            if var_post != post:
                continue

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

        流程（三遍计算）：
        1. 使用预构建的 scope_trees
        2. 第一遍：requires（Indicator 注入）
        3. 第二遍：计算 post=false 的 vars
        4. 第三遍：计算 post=true 的 vars
        5. target 匹配与输出

        Returns:
            {(exchange_path, symbol): {"field": value, ...}}
        """
        if not self.scope_trees or not self.scope_manager:
            return {}

        # 重置所有 scope 的 ready 状态
        self.scope_manager.reset_all_ready_states()

        output = {}

        # 遍历所有树
        for tree in self.scope_trees:
            # 收集所有节点（广度优先）
            all_nodes = self._breadth_first_traversal([tree.root])

            # 追踪已计算的节点（去重）
            computed_set = set()

            # 第一遍：requires（Indicator 注入）
            for node in all_nodes:
                node_id = id(node)
                if node_id in computed_set or node.scope.not_ready:
                    continue

                # 注入 Indicator 变量
                self._inject_indicator_vars_to_scope(node.scope)

                computed_set.add(node_id)

            # 第二遍：计算 post=false 的 vars
            computed_set.clear()
            for node in all_nodes:
                node_id = id(node)
                if node_id in computed_set or node.scope.not_ready:
                    continue

                self._compute_scope_vars(node.scope, post=False, node=node, tree=tree)
                computed_set.add(node_id)

            # 第三遍：计算 post=true 的 vars
            computed_set.clear()
            for node in all_nodes:
                node_id = id(node)
                if node_id in computed_set or node.scope.not_ready:
                    continue

                self._compute_scope_vars(node.scope, post=True, node=node, tree=tree)
                computed_set.add(node_id)

            # target 匹配与输出（收集叶子节点）
            leaf_nodes = self._collect_leaf_nodes(tree.root)
            for node in leaf_nodes:
                if node.scope.not_ready:
                    continue

                # 获取 exchange_path 和 symbol
                scope = node.scope
                exchange_path = scope.get_var("exchange_id") or scope.get_var("exchange_path")
                symbol = scope.get_var("symbol")

                if not exchange_path or not symbol:
                    continue

                # 检查全局 condition
                if self.config.condition:
                    try:
                        context = dict(tree.get_vars(node))
                        result = self._safe_eval(self.config.condition, context)
                        if not result:
                            continue
                    except Exception as e:
                        self.logger.warning("Global condition evaluation failed: %s", e)
                        continue

                # 匹配并求值 targets 配置
                target_output = self._evaluate_targets(scope, exchange_path, symbol, node=node, tree=tree)

                if target_output:
                    key = (exchange_path, symbol)
                    if key in output:
                        output[key].update(target_output)
                    else:
                        output[key] = target_output

        return output

    async def on_start(self) -> None:
        """
        启动回调（Feature 0012）

        初始化 Scope 系统。
        """
        await super().on_start()

        # 如果配置了 Scope 系统，进行初始化
        # if self.config.links:
        #     # 注册自定义 Scope 类型
        #     self._register_custom_scopes()
        #
        #     # NOTE: 不再预先构建 Scope 树，改为按需创建（在 get_output() 中）
        #     # 这样可以支持动态的交易对变化（新币上线等）
        #     # self._build_scope_trees()

    # @abstractmethod
    # def get_targets(self) -> Union[TargetPositions, StrategyOutput]:
        """
        获取策略计算的目标仓位

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

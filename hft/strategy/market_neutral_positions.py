"""MarketNeutralPositionsStrategy - 市场中性对冲策略

基于 Scope 系统实现的市场中性策略。

Feature 0013: MarketNeutralPositions 策略
"""
from typing import Dict, Any, Optional, List, ClassVar, Type
from pydantic import Field
from .base import BaseStrategy, StrategyOutput
from .config import BaseStrategyConfig


class MarketNeutralPositionsConfig(BaseStrategyConfig):
    """
    MarketNeutralPositions 策略配置

    支持：
    - 交易对分组（通过 trading_pair_group 配置）
    - 公平价格计算（通过 FairPriceIndicator）
    - Direction 计算（根据价差阈值）
    - Ratio 平衡（确保市场中性）
    """
    class_name: ClassVar[str] = "market_neutral_positions"
    class_dir: ClassVar[str] = "conf/strategy/market_neutral_positions"

    # 分组配置
    max_trading_pair_groups: int = Field(
        10,
        description="最大交易对分组数量"
    )
    default_trading_pair_group: str = Field(
        "symbol.split('/')[0]",
        description="默认分组规则表达式（如 symbol.split('/')[0]）"
    )
    trading_pair_group: Dict[str, str] = Field(
        default_factory=dict,
        description="交易对到分组的映射（如 {'WBETH/USDT': 'ETH'}）"
    )

    # 仓位配置
    max_position_usd: float = Field(
        2000.0,
        description="每个分组的最大仓位（USD）"
    )
    weights: Dict[str, float] = Field(
        default_factory=dict,
        description="交易所权重配置（如 {'okx/main': 0.5, 'binance/spot': 0.5}）"
    )

    # 阈值配置
    entry_price_threshold: float = Field(
        0.001,
        description="开仓价差阈值（0.1% = 0.001）"
    )
    exit_price_threshold: float = Field(
        0.0005,
        description="平仓价差阈值（0.05% = 0.0005）"
    )
    score_threshold: float = Field(
        0.001,
        description="最小 score 阈值（用于选择 top groups）"
    )

    @classmethod
    def get_class_type(cls) -> Type["MarketNeutralPositionsStrategy"]:
        return MarketNeutralPositionsStrategy


class MarketNeutralPositionsStrategy(BaseStrategy):
    """
    市场中性对冲策略

    特性：
    - 保持 ratio 总和为 0（市场中性）
    - 支持三种套利模式（现货-现货、现货-合约、合约-合约）
    - 基于 Scope 系统的多层级计算

    计算流程：
    1. 构建 Scope 树（包含 TradingPairClassGroupScope）
    2. 注入 Indicator 变量（TickerDataSource, FairPriceIndicator, MedalAmountDataSource）
    3. 计算 fair_price_min/max、score
    4. 计算 Direction（根据价差阈值）
    5. 选择 Top Groups（根据 score 和已有仓位）
    6. 计算并平衡 Ratio（确保组内总和为 0）
    7. 生成输出（position_usd = ratio * max_position_usd）
    """

    # Direction 常量
    DIRECTION_ENTRY_SHORT = -1  # 建议开空仓
    DIRECTION_EXIT = 0          # 建议平仓
    DIRECTION_ENTRY_LONG = 1    # 建议开多仓
    DIRECTION_HOLD = None       # 建议持仓不动

    def __init__(self, config: MarketNeutralPositionsConfig):
        super().__init__(config)
        self.config: MarketNeutralPositionsConfig = config

    def _get_group_id(self, symbol: str) -> str:
        """
        获取交易对的分组 ID

        Args:
            symbol: 交易对（如 "ETH/USDT"）

        Returns:
            分组 ID（如 "ETH"）
        """
        # 优先使用配置的映射
        if symbol in self.config.trading_pair_group:
            return self.config.trading_pair_group[symbol]

        # 使用默认规则
        try:
            context = {"symbol": symbol}
            return self._safe_eval(self.config.default_trading_pair_group, context)
        except Exception as e:
            self.logger.warning(
                "Failed to evaluate default_trading_pair_group for %s: %s",
                symbol, e
            )
            # 降级：使用 symbol 的第一部分
            return symbol.split('/')[0] if '/' in symbol else symbol

    def _compute_direction(
        self,
        delta_price: float,
        is_min: bool
    ) -> Optional[int]:
        """
        根据价差计算 Direction

        Args:
            delta_price: 价差（相对于 fair_price_min 或 fair_price_max）
            is_min: True 表示计算 delta_min_direction，False 表示 delta_max_direction

        Returns:
            Direction: -1 (Entry Short), 0 (Exit), 1 (Entry Long), None (Hold)
        """
        if delta_price > self.config.entry_price_threshold:
            return self.DIRECTION_ENTRY_SHORT if is_min else self.DIRECTION_ENTRY_LONG
        elif delta_price > self.config.exit_price_threshold:
            return self.DIRECTION_EXIT
        else:
            return self.DIRECTION_HOLD

    def _adjust_ratio_by_direction(
        self,
        ratio: float,
        delta_min_direction: Optional[int],
        delta_max_direction: Optional[int]
    ) -> float:
        """
        根据 Direction 调整 Ratio

        Args:
            ratio: 初始 ratio（已 clip 到 [-1, 1]）
            delta_min_direction: 相对于最低价的方向
            delta_max_direction: 相对于最高价的方向

        Returns:
            调整后的 ratio
        """
        # Direction 组合表（见 Feature 0013 文档）
        d_min = delta_min_direction
        d_max = delta_max_direction

        # 不应出现的组合（内部逻辑错误）
        invalid_combinations = [
            (-1, -1), (0, -1), (1, -1), (1, 0), (1, 1), (1, None), (None, -1)
        ]
        if (d_min, d_max) in invalid_combinations:
            self.logger.warning(
                "Invalid direction combination: (%s, %s), returning 0",
                d_min, d_max
            )
            return 0.0

        # 根据组合调整 ratio
        if d_min == -1 and d_max == 0:
            return min(ratio, 0)
        elif d_min == -1 and d_max == 1:
            return ratio  # 不变
        elif d_min == -1 and d_max is None:
            return -1.0
        elif d_min == 0 and d_max == 0:
            return ratio  # 不变
        elif d_min == 0 and d_max == 1:
            return max(ratio, 0)
        elif d_min == 0 and d_max is None:
            return min(ratio, 0)
        elif d_min is None and d_max == 0:
            return max(ratio, 0)
        elif d_min is None and d_max == 1:
            return 1.0
        elif d_min is None and d_max is None:
            return ratio  # 不变
        else:
            return ratio

    def _balance_ratios(self, group_scopes: List[Any]) -> None:
        """
        平衡组内 Ratio（确保总和为 0）

        Args:
            group_scopes: 同一组内的所有 TradingPairClassScope
        """
        if len(group_scopes) < 2:
            return

        # 计算当前 ratio 总和
        ratio_sum = sum(s.get_var("ratio") or 0.0 for s in group_scopes)

        if abs(ratio_sum) < 1e-10:
            return  # 已经平衡

        # 找到最高价和最低价的 scope
        sorted_scopes = sorted(
            group_scopes,
            key=lambda s: s.get_var("trading_pair_std_price") or 0
        )
        min_price_scope = sorted_scopes[0]
        max_price_scope = sorted_scopes[-1]

        # 调整使总和为 0
        if ratio_sum > 0:
            # 从最高价的 ratio 中减去
            current = max_price_scope.get_var("ratio") or 0.0
            max_price_scope.set_var("ratio", current - ratio_sum)
        else:
            # 从最低价的 ratio 中加上
            current = min_price_scope.get_var("ratio") or 0.0
            min_price_scope.set_var("ratio", current - ratio_sum)

    def _adjust_hedge_ratios(self, group_scopes: List[Any]) -> None:
        """
        对冲调整（确保 ratio_min - ratio_max = 2）

        Args:
            group_scopes: 同一组内的所有 TradingPairClassScope
        """
        if len(group_scopes) < 2:
            return

        # 找到最高价和最低价的 scope
        sorted_scopes = sorted(
            group_scopes,
            key=lambda s: s.get_var("trading_pair_std_price") or 0
        )
        min_price_scope = sorted_scopes[0]
        max_price_scope = sorted_scopes[-1]

        ratio_min = min_price_scope.get_var("ratio") or 0.0
        ratio_max = max_price_scope.get_var("ratio") or 0.0

        # 计算调整量使 ratio_min - ratio_max = 2
        # delta_ratio = (ratio_min - ratio_max) / 2 - 1
        delta_ratio = (ratio_min - ratio_max) / 2 - 1

        # 调整
        min_price_scope.set_var("ratio", ratio_min - delta_ratio)
        max_price_scope.set_var("ratio", ratio_max + delta_ratio)

    def _select_top_groups(
        self,
        group_scopes: Dict[str, Any]
    ) -> List[str]:
        """
        选择需要返回的 top groups

        Args:
            group_scopes: {group_id: TradingPairClassGroupScope}

        Returns:
            选中的 group_id 列表
        """
        # 优先级 1: 包含已有仓位的 group
        groups_with_positions = set()

        if self.root is not None:
            exchange_group = getattr(self.root, 'exchange_group', None)
            if exchange_group is not None:
                for exchange in exchange_group.exchanges.values():
                    # 检查 positions
                    for symbol in exchange.positions.keys():
                        group_id = self._get_group_id(symbol)
                        if group_id in group_scopes:
                            groups_with_positions.add(group_id)

                    # 检查 balance
                    for symbol in exchange.balance.keys():
                        group_id = self._get_group_id(symbol)
                        if group_id in group_scopes:
                            groups_with_positions.add(group_id)

        # 优先级 2: 按 score 排序
        scored_groups = []
        for group_id, scope in group_scopes.items():
            score = scope.get_var("score")
            if score is not None and score >= self.config.score_threshold:
                scored_groups.append((group_id, score))

        # 按 score 降序排序
        scored_groups.sort(key=lambda x: x[1], reverse=True)

        # 合并两个优先级
        selected = list(groups_with_positions)
        for group_id, _ in scored_groups:
            if group_id not in selected:
                selected.append(group_id)
            if len(selected) >= self.config.max_trading_pair_groups:
                break

        return selected

    def _compute_directions(self, all_nodes: List[Any]) -> None:
        """
        计算所有 trading_pair_class scope 的 Direction

        Args:
            all_nodes: 所有 LinkedScopeNode 列表
        """
        for node in all_nodes:
            scope = node.scope
            scope_class_name = scope.__class__.__name__
            if scope_class_name != "TradingPairClassScope":
                continue

            # 获取必要的变量
            trading_pair_std_price = scope.get_var("trading_pair_std_price")
            if trading_pair_std_price is None:
                continue

            parent_node = node.parent
            if parent_node is None:
                continue
            parent = parent_node.scope

            fair_price_min = parent.get_var("fair_price_min")
            fair_price_max = parent.get_var("fair_price_max")

            if fair_price_min is None or fair_price_max is None:
                continue

            # 计算 delta_min_price 和 delta_max_price
            delta_min_price = trading_pair_std_price - fair_price_min
            delta_max_price = fair_price_max - trading_pair_std_price

            # 计算 Direction
            delta_min_direction = self._compute_direction(delta_min_price, is_min=True)
            delta_max_direction = self._compute_direction(delta_max_price, is_min=False)

            # 存储到 scope
            scope.set_var("delta_min_price", delta_min_price)
            scope.set_var("delta_max_price", delta_max_price)
            scope.set_var("delta_min_direction", delta_min_direction)
            scope.set_var("delta_max_direction", delta_max_direction)

    def _collect_group_scopes(self, all_scopes: List[Any]) -> Dict[str, Any]:
        """
        收集所有 TradingPairClassGroupScope

        Args:
            all_scopes: 所有 Scope 列表

        Returns:
            {group_id: TradingPairClassGroupScope}
        """
        group_scopes = {}
        for scope in all_scopes:
            scope_class_name = scope.__class__.__name__
            if scope_class_name == "TradingPairClassGroupScope":
                group_id = scope.get_var("group_id")
                if group_id:
                    group_scopes[group_id] = scope
        return group_scopes

    def _collect_pair_class_scopes(self, group_node: Any) -> List[Any]:
        """
        收集 group 下的所有 TradingPairClassScope

        Args:
            group_node: TradingPairClassGroupScope 的 LinkedScopeNode

        Returns:
            TradingPairClassScope 列表
        """
        pair_class_scopes = []
        for child_node in group_node.children:
            child_scope = child_node.scope
            if child_scope.__class__.__name__ == "TradingPairClassScope":
                # 过滤掉 trading_pair_std_price 为 None 的
                if child_scope.get_var("trading_pair_std_price") is not None:
                    pair_class_scopes.append(child_scope)
        return pair_class_scopes

    def _compute_initial_ratios(self, pair_class_scopes: List[Any]) -> None:
        """
        计算初始 ratio（从 ratio_est）

        Args:
            pair_class_scopes: TradingPairClassScope 列表
        """
        for scope in pair_class_scopes:
            ratio_est = scope.get_var("ratio_est") or 0.0
            # Clip to [-1, 1]
            ratio = max(-1.0, min(1.0, ratio_est))
            scope.set_var("ratio", ratio)

    def _adjust_ratios_by_directions(self, pair_class_scopes: List[Any]) -> None:
        """
        根据 Direction 调整所有 Ratio

        Args:
            pair_class_scopes: TradingPairClassScope 列表
        """
        for scope in pair_class_scopes:
            ratio = scope.get_var("ratio") or 0.0
            delta_min_direction = scope.get_var("delta_min_direction")
            delta_max_direction = scope.get_var("delta_max_direction")

            adjusted_ratio = self._adjust_ratio_by_direction(
                ratio, delta_min_direction, delta_max_direction
            )
            scope.set_var("ratio", adjusted_ratio)

    def _register_custom_scopes(self) -> None:
        """
        注册自定义 Scope 类型
        """
        if self.scope_manager is None:
            return

        from ..core.scope.scopes import TradingPairClassGroupScope
        self.scope_manager.register_scope_class(
            "TradingPairClassGroupScope",
            TradingPairClassGroupScope
        )

    def get_output(self) -> StrategyOutput:
        """
        获取策略输出（重写基类方法）

        MarketNeutralPositions 特有流程：
        1. 调用基类 get_output() 完成基础计算（Indicator 注入 + vars 计算）
        2. 计算 Direction（delta_min_direction, delta_max_direction）
        3. 选择 Top Groups
        4. 计算并调整 Ratio
        5. 平衡 Ratio（确保组内总和为 0）
        6. 对冲调整（确保 ratio_min - ratio_max = 2）
        7. 生成最终输出

        Returns:
            {(exchange_path, symbol): {"position_usd": ..., "ratio": ..., ...}}
        """
        if not self.config.links or not self.scope_manager:
            return {}

        # 步骤 1: 调用基类完成基础计算
        # 这会完成 Scope 树构建、Indicator 注入、vars 计算
        # 但我们不使用基类的 target 输出，而是自己处理

        # 重置所有 scope 的 ready 状态
        self.scope_manager.reset_all_ready_states()

        # 构建 LinkTree
        trading_pairs = self._get_all_trading_pairs()
        if not trading_pairs:
            return {}

        # 为每条 link 构建 Scope 树
        link_trees = []
        for link_index in range(len(self.config.links)):
            root_scopes = []
            leaf_scopes = []

            for exchange_path, symbol in trading_pairs:
                scope = self._get_or_create_scope_for_target(
                    exchange_path, symbol, link_index
                )
                if scope:
                    leaf_scopes.append(scope)
                    root = scope
                    while root.parent is not None:
                        root = root.parent
                    if root not in root_scopes:
                        root_scopes.append(root)

            if root_scopes:
                link_trees.append((link_index, root_scopes, leaf_scopes))

        # 对每条 link 执行三遍计算
        output = {}
        for link_index, root_scopes, leaf_scopes in link_trees:
            all_scopes = self._breadth_first_traversal(root_scopes)
            computed_set = set()

            # 第一遍：Indicator 注入
            for scope in all_scopes:
                scope_id = id(scope)
                if scope_id in computed_set or scope.is_not_ready:
                    continue
                self._inject_indicator_vars_to_scope(scope)
                computed_set.add(scope_id)

            # 第二遍：计算 post=false 的 vars
            computed_set.clear()
            for scope in all_scopes:
                scope_id = id(scope)
                if scope_id in computed_set or scope.is_not_ready:
                    continue
                self._compute_scope_vars(scope, post=False)
                computed_set.add(scope_id)

            # 第三遍：计算 post=true 的 vars
            computed_set.clear()
            for scope in all_scopes:
                scope_id = id(scope)
                if scope_id in computed_set or scope.is_not_ready:
                    continue
                self._compute_scope_vars(scope, post=True)
                computed_set.add(scope_id)

            # 步骤 2: 计算 Direction
            self._compute_directions(all_scopes)

            # 步骤 3: 收集 group scopes
            group_scopes = self._collect_group_scopes(all_scopes)

            # 步骤 4: 选择 Top Groups
            selected_groups = self._select_top_groups(group_scopes)

            # 步骤 5-7: 对每个选中的 group 进行 Ratio 计算和平衡
            for group_id in selected_groups:
                group_scope = group_scopes[group_id]

                # 收集该 group 下的所有 trading_pair_class scopes
                pair_class_scopes = self._collect_pair_class_scopes(group_scope)

                if len(pair_class_scopes) < 2:
                    # 单个交易对无法套利，跳过
                    continue

                # 计算初始 ratio
                self._compute_initial_ratios(pair_class_scopes)

                # 根据 Direction 调整 Ratio
                self._adjust_ratios_by_directions(pair_class_scopes)

                # 平衡 Ratio（确保总和为 0）
                self._balance_ratios(pair_class_scopes)

                # 对冲调整（确保 ratio_min - ratio_max = 2）
                self._adjust_hedge_ratios(pair_class_scopes)

            # 步骤 8: 生成输出（遍历叶子节点）
            for scope in leaf_scopes:
                if scope.is_not_ready:
                    continue

                exchange_path = scope.get_var("exchange_id")
                symbol = scope.get_var("symbol")

                if not exchange_path or not symbol:
                    continue

                # 获取 ratio（从 parent trading_pair_class scope）
                parent = scope.parent
                if parent is None:
                    continue

                ratio = parent.get_var("ratio")
                if ratio is None or abs(ratio) < 1e-10:
                    continue

                # 计算 position_usd
                max_position_usd = scope.get_var("max_position_usd") or self.config.max_position_usd
                position_usd = ratio * max_position_usd

                # 检查 target condition
                if self.config.targets:
                    target_output = self._evaluate_targets(scope, exchange_path, symbol)
                    if target_output:
                        key = (exchange_path, symbol)
                        target_output["ratio"] = ratio
                        target_output["delta_min_direction"] = parent.get_var("delta_min_direction")
                        target_output["delta_max_direction"] = parent.get_var("delta_max_direction")
                        output[key] = target_output
                else:
                    # 没有 targets 配置，直接输出
                    key = (exchange_path, symbol)
                    output[key] = {
                        "position_usd": position_usd,
                        "ratio": ratio,
                        "delta_min_direction": parent.get_var("delta_min_direction"),
                        "delta_max_direction": parent.get_var("delta_max_direction"),
                    }

        return output

    def get_target_positions_usd(self) -> StrategyOutput:
        """
        获取目标仓位

        Returns:
            策略输出：{(exchange_path, symbol): {"position_usd": ..., "speed": ..., ...}}
        """
        return self.get_output()

    async def on_tick(self) -> bool:
        """
        Tick 回调

        MarketNeutralPositions 策略不需要特殊的 tick 逻辑。
        目标仓位计算在 get_target_positions_usd() 中完成。

        Returns:
            False: 继续运行
        """
        return False

    # TODO: 实现以下方法以支持完整的 MarketNeutralPositions 逻辑
    #
    # 当前实现使用基类的 get_output()，它会：
    # 1. 构建 Scope 树
    # 2. 注入 Indicator 变量
    # 3. 计算 vars（包括 fair_price_min/max、score 等）
    # 4. 匹配 targets 并生成输出
    #
    # 完整的 MarketNeutralPositions 策略还需要：
    # 1. 在 trading_pair_class_group 层级选择 top groups
    # 2. 计算 Direction 并调整 Ratio
    # 3. 平衡 Ratio（确保组内总和为 0）
    # 4. 对冲调整（确保 ratio_min - ratio_max = 2）
    #
    # 这些逻辑可以通过以下方式实现：
    # - 在 Scope vars 中定义 score、direction 等变量
    # - 在 Strategy 中重写 get_output() 添加选择和平衡逻辑
    # - 或者使用 post=True 的 vars 进行后处理

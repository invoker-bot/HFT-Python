"""MarketNeutralPositionsStrategy - 市场中性对冲策略

基于 Scope 系统实现的市场中性策略。

Feature 0013: MarketNeutralPositions 策略
"""
from typing import Any, ClassVar, Dict, List, Optional, Type

from pydantic import Field

from .base import BaseStrategy
from .config import BaseStrategyConfig
from ..core.scope.base import BaseScope, FlowScopeNode, ScopeInstanceId
from ..core.scope.scopes import ExchangeClassScope, TradingPairClassScope


# ============================================================
# TradingPairClassGroupScope - 策略私有 Scope
# ============================================================

def trading_pair_class_to_group_scope(current_instance_id: ScopeInstanceId) -> ScopeInstanceId:
    """TradingPairClassScope instance_id -> TradingPairClassGroupScope instance_id"""
    exchange_class, symbol = current_instance_id
    # 提取 base currency 作为 group_id（如 "ETH/USDT" -> "ETH"）
    group_id = symbol.split('/')[0] if '/' in symbol else symbol
    return (exchange_class, group_id)


def group_to_exchange_class_scope(current_instance_id: ScopeInstanceId) -> ScopeInstanceId:
    """TradingPairClassGroupScope instance_id -> ExchangeClassScope instance_id"""
    exchange_class, _group_id = current_instance_id
    return (exchange_class, )


class TradingPairClassGroupScope(BaseScope):
    """
    交易对分组 Scope（策略私有）

    将同一 exchange_class 下的交易对按 base currency 分组。
    例如 ETH/USDT 和 WBETH/USDT 可以归入 "ETH" 组。

    instance_id: (exchange_class, group_id)，如 ("okx", "ETH")
    """
    flow_mapper = {
        ExchangeClassScope: [group_to_exchange_class_scope],
    }

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        exchange_class, group_id = self.instance_id
        self.set_var("exchange_class", exchange_class)
        self.set_var("group_id", group_id)

    @classmethod
    def get_all_instance_ids(cls, app_core: 'Any') -> set[ScopeInstanceId]:
        """遍历所有交易对，提取 base currency 作为 group_id"""
        exchange_group = app_core.exchange_group
        results = set()
        for exchange_class, exchange_paths in exchange_group.exchange_group.items():
            for exchange_path in exchange_paths:
                instance = exchange_group.exchange_instances[exchange_path]
                if instance.ready:
                    markets = instance.markets.get_data()
                    for symbol in markets.keys():
                        group_id = symbol.split('/')[0] if '/' in symbol else symbol
                        results.add((exchange_class, group_id))
        return results


# 动态添加 TradingPairClassScope -> TradingPairClassGroupScope 的映射
# __init_subclass__ 已自动将 TradingPairClassGroupScope 注册到 BaseScope.classes
# 但 TradingPairClassScope 需要知道如何映射到 TradingPairClassGroupScope
TradingPairClassScope.flow_mapper[TradingPairClassGroupScope] = [
    trading_pair_class_to_group_scope
]
# 传播映射到 TradingPairClassScope 的子类（如 TradingPairScope）
TradingPairClassScope.update_flow_mapper(
    TradingPairClassGroupScope,
    [trading_pair_class_to_group_scope]
)


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

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self.config: MarketNeutralPositionsConfig = kwargs['config']

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

        # 默认规则：提取 base currency
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

    def _balance_ratios(self, group_scopes: list[FlowScopeNode]) -> None:
        """
        平衡组内 Ratio（确保总和为 0）

        Args:
            group_scopes: 同一组内的所有 TradingPairClassScope 节点
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

    def _adjust_hedge_ratios(self, group_scopes: list[FlowScopeNode]) -> None:
        """
        对冲调整（确保 ratio_min - ratio_max = 2）

        Args:
            group_scopes: 同一组内的所有 TradingPairClassScope 节点
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

    def _compute_directions(self, pair_class_nodes: list[FlowScopeNode]) -> None:
        """
        计算所有 TradingPairClassScope 节点的 Direction

        Args:
            pair_class_nodes: TradingPairClassScope 的 FlowScopeNode 列表
        """
        for node in pair_class_nodes:
            trading_pair_std_price = node.get_var("trading_pair_std_price")
            if trading_pair_std_price is None:
                continue

            # 通过 prev 回溯获取 group 层的 fair_price
            fair_price_min = node.get_var("fair_price_min")
            fair_price_max = node.get_var("fair_price_max")

            if fair_price_min is None or fair_price_max is None:
                continue

            delta_min_price = trading_pair_std_price - fair_price_min
            delta_max_price = fair_price_max - trading_pair_std_price

            delta_min_direction = self._compute_direction(delta_min_price, is_min=True)
            delta_max_direction = self._compute_direction(delta_max_price, is_min=False)

            node.set_var("delta_min_price", delta_min_price)
            node.set_var("delta_max_price", delta_max_price)
            node.set_var("delta_min_direction", delta_min_direction)
            node.set_var("delta_max_direction", delta_max_direction)

    def _collect_group_nodes(
        self,
        all_nodes: dict[ScopeInstanceId, FlowScopeNode]
    ) -> dict[str, FlowScopeNode]:
        """
        从 flow 结果中收集所有 TradingPairClassGroupScope 节点

        Args:
            all_nodes: calculate_flow_nodes() 返回的节点字典

        Returns:
            {group_id: FlowScopeNode}
        """
        group_nodes = {}
        for instance_id, node in all_nodes.items():
            if isinstance(node.scope, TradingPairClassGroupScope):
                group_id = node.scope.get_var("group_id")
                if group_id:
                    group_nodes[group_id] = node
        return group_nodes

    def _collect_pair_class_nodes(
        self,
        group_id: str,
        all_nodes: dict[ScopeInstanceId, FlowScopeNode]
    ) -> list[FlowScopeNode]:
        """
        收集属于指定 group 的所有 TradingPairClassScope 节点

        FlowScopeNode 没有 children 属性，改为从所有节点中按 group_id 过滤。

        Args:
            group_id: 分组 ID（如 "ETH"）
            all_nodes: calculate_flow_nodes() 返回的节点字典

        Returns:
            匹配的 TradingPairClassScope FlowScopeNode 列表
        """
        pair_class_nodes = []
        for instance_id, node in all_nodes.items():
            if not isinstance(node.scope, TradingPairClassScope):
                continue
            # 检查该交易对是否属于此 group
            symbol = node.scope.get_var("symbol")
            if symbol is None:
                continue
            node_group_id = self._get_group_id(symbol)
            if node_group_id != group_id:
                continue
            # 过滤掉 trading_pair_std_price 为 None 的
            if node.get_var("trading_pair_std_price") is not None:
                pair_class_nodes.append(node)
        return pair_class_nodes

    def _compute_initial_ratios(self, pair_class_nodes: list[FlowScopeNode]) -> None:
        """
        计算初始 ratio（从 ratio_est）

        Args:
            pair_class_nodes: TradingPairClassScope 的 FlowScopeNode 列表
        """
        for node in pair_class_nodes:
            ratio_est = node.get_var("ratio_est") or 0.0
            ratio = max(-1.0, min(1.0, ratio_est))
            node.set_var("ratio", ratio)

    def _adjust_ratios_by_directions(self, pair_class_nodes: list[FlowScopeNode]) -> None:
        """
        根据 Direction 调整所有 Ratio

        Args:
            pair_class_nodes: TradingPairClassScope 的 FlowScopeNode 列表
        """
        for node in pair_class_nodes:
            ratio = node.get_var("ratio") or 0.0
            delta_min_direction = node.get_var("delta_min_direction")
            delta_max_direction = node.get_var("delta_max_direction")

            adjusted_ratio = self._adjust_ratio_by_direction(
                ratio, delta_min_direction, delta_max_direction
            )
            node.set_var("ratio", adjusted_ratio)

    """
    def get_output(self) -> StrategyOutput:
        if not self.config.flow:
            return {}

        # 步骤 1: 通过 flow 系统完成基础计算（Indicator 注入 + vars 计算）
        # calculate_flow_nodes() 返回最终层的 {instance_id: FlowScopeNode}
        final_nodes = self.calculate_flow_nodes()
        if not final_nodes:
            return {}

        # 收集所有层的节点（通过 prev 回溯可访问上层变量）
        # final_nodes 是最终层（TradingPairScope 或 TradingPairClassScope）

        # 步骤 2: 找出 TradingPairClassScope 节点用于 Direction 计算
        pair_class_nodes = [
            node for node in final_nodes.values()
            if isinstance(node.scope, TradingPairClassScope)
        ]
        # 如果最终层不是 TradingPairClassScope，从 prev 中提取
        if not pair_class_nodes:
            seen = set()
            for node in final_nodes.values():
                for prev_node in node.prev:
                    if isinstance(prev_node.scope, TradingPairClassScope):
                        node_id = id(prev_node)
                        if node_id not in seen:
                            pair_class_nodes.append(prev_node)
                            seen.add(node_id)

        # 步骤 3: 计算 Direction
        self._compute_directions(pair_class_nodes)

        # 步骤 4: 收集 group 信息（从 pair_class 节点的 prev 中提取）
        group_nodes: dict[str, FlowScopeNode] = {}
        for node in pair_class_nodes:
            for prev_node in node.prev:
                if isinstance(prev_node.scope, TradingPairClassGroupScope):
                    group_id = prev_node.scope.get_var("group_id")
                    if group_id and group_id not in group_nodes:
                        group_nodes[group_id] = prev_node

        # 步骤 5: 选择 Top Groups
        group_scopes = {gid: node.scope for gid, node in group_nodes.items()}
        selected_groups = self._select_top_groups(group_scopes)

        # 构建 pair_class 节点的 instance_id -> node 映射
        pair_class_map = {
            node.scope.instance_id: node for node in pair_class_nodes
        }

        # 步骤 6-8: 对每个选中的 group 进行 Ratio 计算和平衡
        for group_id in selected_groups:
            pair_nodes = self._collect_pair_class_nodes(
                group_id, pair_class_map
            )
            if len(pair_nodes) < 2:
                continue

            self._compute_initial_ratios(pair_nodes)
            self._adjust_ratios_by_directions(pair_nodes)
            self._balance_ratios(pair_nodes)
            self._adjust_hedge_ratios(pair_nodes)

        # 步骤 9: 生成输出
        output = {}
        for instance_id, node in final_nodes.items():
            exchange_path = node.get_var("exchange_path")
            symbol = node.get_var("symbol")
            if not exchange_path or not symbol:
                continue

            # 获取 ratio（从 prev 中的 TradingPairClassScope）
            ratio = node.get_var("ratio")
            if ratio is None or abs(ratio) < 1e-10:
                continue

            max_position_usd = (
                node.get_var("max_position_usd")
                or self.config.max_position_usd
            )
            position_usd = ratio * max_position_usd

            key = (exchange_path, symbol)
            output[key] = {
                "position_usd": position_usd,
                "ratio": ratio,
                "delta_min_direction": node.get_var("delta_min_direction"),
                "delta_max_direction": node.get_var("delta_max_direction"),
            }

        return output"""

    async def on_tick(self) -> bool:
        """
        Tick 回调

        MarketNeutralPositions 策略不需要特殊的 tick 逻辑。
        目标仓位计算在 get_target_positions_usd() 中完成。

        Returns:
            False: 继续运行
        """
        return False


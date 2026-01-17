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

        # 2. 计算 vars 列表
        for var_def in getattr(self.config, 'vars', []) or []:
            try:
                value = self._safe_eval(var_def.value, context)
                context[var_def.name] = value
            except Exception as e:
                self.logger.warning(
                    "Failed to compute var %s: %s",
                    var_def.name, e
                )

        # 3. 计算 conditional_vars
        now = time.time()
        for var_name, var_def in (getattr(self.config, 'conditional_vars', {}) or {}).items():
            # 获取当前状态
            current_value, last_update = self._conditional_var_states.get(
                var_name, (var_def.default, 0.0)
            )

            # 计算 duration（距上次更新的秒数）
            duration = now - last_update if last_update > 0 else float('inf')

            # 构建求值上下文（包含 duration）
            eval_context = {**context, "duration": duration}

            # 检查条件
            try:
                condition_met = self._safe_eval_bool(var_def.on, eval_context)
            except Exception as e:
                self.logger.warning(
                    "Failed to evaluate condition for %s: %s",
                    var_name, e
                )
                condition_met = False

            if condition_met:
                # 条件满足，更新值
                try:
                    new_value = self._safe_eval(var_def.value, eval_context)
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

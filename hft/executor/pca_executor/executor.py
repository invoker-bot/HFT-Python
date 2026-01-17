"""
PCAExecutor - Position Cost Averaging 执行器

Feature 0010 Phase 5: 使用统一的 Order 配置格式

支持两种配置模式：
1. 新格式：entry_order / exit_order（推荐）
2. 旧格式：base_order_usd / spread_open 等（向后兼容）

特点：
- 入场单：在更优价格等待加仓（多仓在下方买，空仓在上方卖）
- 出场单：在盈利价格等待止盈
- 订单挂出后不频繁变更，等待成交或超时
- 支持 entry_level / exit_level 追踪
- 支持 reset 条件（重置统计）
"""
import time
from typing import TYPE_CHECKING, Optional, Any
from dataclasses import dataclass, field

from ..base import BaseExecutor, ExecutionResult, OrderIntent
from ..order_config import OrderDefinition

if TYPE_CHECKING:
    from ...exchange.base import BaseExchange
    from .config import PCAExecutorConfig


@dataclass
class PCAState:
    """
    PCA 状态追踪（Feature 0010 Phase 5）

    per (exchange, symbol) 追踪：
    - 当前入场档位
    - 当前出场档位
    - 累计入场数量/金额
    - 平均入场价格
    """
    entry_level: int = 0           # 当前入场档位
    exit_level: int = 0            # 当前出场档位
    total_entry_amount: float = 0  # 累计入场数量
    total_entry_usd: float = 0     # 累计入场金额
    last_position_amount: float = 0  # 上次仓位数量（用于检测成交）

    # 旧格式兼容
    addition_count: int = 0        # 已加仓次数
    cost_price: float = 0          # 成本价

    @property
    def average_entry_price(self) -> float:
        """平均入场价格"""
        if self.total_entry_amount == 0:
            return 0
        return self.total_entry_usd / self.total_entry_amount

    def reset(self):
        """重置状态"""
        self.entry_level = 0
        self.exit_level = 0
        self.total_entry_amount = 0
        self.total_entry_usd = 0
        self.addition_count = 0
        self.cost_price = 0


class PCAExecutor(BaseExecutor):
    """
    Position Cost Averaging 执行器

    Feature 0010 Phase 5: 支持新格式配置

    新格式内置变量：
    - entry_level: 当前入场档位（0-based）
    - exit_level: 当前出场档位（0-based）
    - total_entry_amount: 累计入场数量
    - total_entry_usd: 累计入场金额
    - average_entry_price: 平均入场价格
    - delta_position_amount: 当前仓位与目标的数量差
    - delta_position_usd: 当前仓位与目标的 USD 差
    """

    config: "PCAExecutorConfig"

    def __init__(self, config: "PCAExecutorConfig"):
        super().__init__(config)
        # 追踪每个 (exchange, symbol) 的状态
        self._pca_states: dict[tuple[str, str], PCAState] = {}
        # Order 级别的 conditional_vars 状态
        # key = (exchange_name, symbol, order_type, var_name)
        self._order_conditional_var_states: dict[tuple[str, str, str, str], tuple[Any, float]] = {}

    @property
    def per_order_usd(self) -> float:
        return self.config.base_order_usd

    @property
    def cancel_delay(self) -> float:
        # PCA 不需要 cancel_delay 机制，用 timeout 控制
        return self.config.timeout + 1

    def _get_state(self, exchange_name: str, symbol: str) -> PCAState:
        """获取或创建状态"""
        key = (exchange_name, symbol)
        if key not in self._pca_states:
            self._pca_states[key] = PCAState()
        return self._pca_states[key]

    def _check_reset_condition(
        self,
        state: PCAState,
        context: dict[str, Any],
    ) -> bool:
        """
        检查重置条件（Feature 0010 Phase 5）

        Returns:
            True 如果需要重置
        """
        if self.config.reset is None:
            return False

        try:
            return self._safe_eval_bool(self.config.reset, context)
        except Exception as e:
            self.logger.warning("Failed to evaluate reset condition: %s", e)
            return False

    def _build_order_context(
        self,
        base_context: dict[str, Any],
        state: PCAState,
        order_type: str,  # "entry" or "exit"
        level: int,
        exchange_name: str,
        symbol: str,
    ) -> dict[str, Any]:
        """
        构建订单级别的上下文（Feature 0010 Phase 5）

        添加 PCA 特有的变量。
        """
        context = {**base_context}

        # 添加 PCA 内置变量
        context["entry_level"] = state.entry_level if order_type == "entry" else level
        context["exit_level"] = state.exit_level if order_type == "exit" else level
        context["total_entry_amount"] = state.total_entry_amount
        context["total_entry_usd"] = state.total_entry_usd
        context["average_entry_price"] = state.average_entry_price
        context["level"] = level  # 当前 level（用于 order 表达式）

        return context

    def _evaluate_order(
        self,
        order_def: OrderDefinition,
        context: dict[str, Any],
        exchange_name: str,
        symbol: str,
        order_type: str,
        mid_price: float,
    ) -> Optional[OrderIntent]:
        """
        求值单个 Order 定义（Feature 0010 Phase 4）

        Returns:
            OrderIntent 或 None（如果条件不满足或求值失败）
        """
        # 计算 order 级 vars
        for var_def in order_def.vars:
            try:
                value = self._safe_eval(var_def.value, context)
                if value is not None:
                    context[var_def.name] = value
            except Exception as e:
                self.logger.warning("Failed to compute order var %s: %s", var_def.name, e)

        # 计算 order 级 conditional_vars
        now = time.time()
        for var_name, var_def in order_def.conditional_vars.items():
            state_key = (exchange_name, symbol, order_type, var_name)
            current_value, last_update = self._order_conditional_var_states.get(
                state_key, (var_def.default, 0.0)
            )
            duration = now - last_update if last_update > 0 else float('inf')
            eval_ctx = {**context, "duration": duration}

            try:
                condition_met = self._safe_eval_bool(var_def.on, eval_ctx)
            except Exception:
                condition_met = False

            if condition_met:
                try:
                    new_value = self._safe_eval(var_def.value, eval_ctx)
                    self._order_conditional_var_states[state_key] = (new_value, now)
                    context[var_name] = new_value
                except Exception:
                    context[var_name] = current_value
            else:
                context[var_name] = current_value

        # 检查条件
        if order_def.condition is not None:
            try:
                if not self._safe_eval_bool(order_def.condition, context):
                    return None
            except Exception:
                return None

        # 计算价格
        price = None
        if order_def.price is not None:
            price = self.evaluate_param(order_def.price, context)
        elif order_def.spread is not None:
            spread = self.evaluate_param(order_def.spread, context)
            if spread is not None:
                direction = context.get("direction", 0)
                if direction > 0:
                    price = mid_price - spread  # 买单：更低价格
                else:
                    price = mid_price + spread  # 卖单：更高价格

        if price is None or price <= 0:
            return None

        # 计算数量
        amount = None
        side = None

        if order_def.order_amount is not None:
            amount = self.evaluate_param(order_def.order_amount, context)
            if amount is not None:
                side = "buy" if amount > 0 else "sell"
                amount = abs(amount)
        elif order_def.order_usd is not None:
            order_usd = self.evaluate_param(order_def.order_usd, context)
            if order_usd is not None and mid_price > 0:
                side = "buy" if order_usd > 0 else "sell"
                amount = abs(order_usd) / mid_price

        if amount is None or amount <= 0 or side is None:
            return None

        # 计算其他参数
        timeout = self.evaluate_param(order_def.timeout, context) or 60.0
        refresh_tolerance = self.evaluate_param(order_def.refresh_tolerance, context) or 0.5

        level = context.get("level", 0)

        return OrderIntent(
            side=side,
            level=level,
            price=price,
            amount=amount,
            timeout=timeout,
            refresh_tolerance=refresh_tolerance,
        )

    def _update_state_on_position_change(
        self,
        state: PCAState,
        current_amount: float,
        current_price: float,
    ):
        """
        根据仓位变化更新状态

        检测成交并更新 entry_level / total_entry_amount 等
        """
        old_amount = state.last_position_amount
        delta_amount = abs(current_amount) - abs(old_amount)

        if delta_amount > 0.0001:  # 仓位增加 = 入场成交
            # 更新累计入场
            added_usd = delta_amount * current_price
            state.total_entry_amount += delta_amount
            state.total_entry_usd += added_usd
            state.entry_level += 1

            # 更新成本价（旧格式兼容）
            if state.cost_price > 0:
                old_value = abs(old_amount) * state.cost_price
                new_value = old_value + added_usd
                state.cost_price = new_value / abs(current_amount)
            else:
                state.cost_price = current_price

            state.addition_count += 1

            self.logger.debug(
                "Entry filled: level=%d, amount=%.6f, avg_price=%.4f",
                state.entry_level, state.total_entry_amount, state.average_entry_price
            )

        elif delta_amount < -0.0001:  # 仓位减少 = 出场成交
            state.exit_level += 1
            self.logger.debug("Exit filled: level=%d", state.exit_level)

        # 方向反转或清仓
        old_sign = 1 if old_amount > 0 else (-1 if old_amount < 0 else 0)
        new_sign = 1 if current_amount > 0 else (-1 if current_amount < 0 else 0)
        if new_sign != old_sign:
            state.reset()
            state.cost_price = current_price if current_amount != 0 else 0

        state.last_position_amount = current_amount

    async def execute_delta(
        self,
        exchange: "BaseExchange",
        symbol: str,
        delta_usd: float,
        speed: float,
        current_price: float,
    ) -> ExecutionResult:
        """执行 PCA 策略"""
        try:
            await exchange.medal_initialize_symbol(symbol)

            # 1. 获取当前仓位
            positions = await exchange.medal_fetch_positions()
            current_amount = positions.get(symbol, 0.0)

            # 2. 获取状态
            state = self._get_state(exchange.name, symbol)

            # 3. 检测仓位变化并更新状态
            self._update_state_on_position_change(state, current_amount, current_price)

            # 4. 构建基础上下文
            direction = 1 if delta_usd > 0 else (-1 if delta_usd < 0 else 0)
            position_usd = current_amount * current_price
            delta_position_amount = delta_usd / current_price if current_price > 0 else 0

            base_context = {
                "direction": direction,
                "buy": direction == 1,
                "sell": direction == -1,
                "speed": speed,
                "notional": abs(delta_usd),
                "mid_price": current_price,
                "current_position_amount": current_amount,
                "current_position_usd": position_usd,
                "delta_position_amount": delta_position_amount,
                "delta_position_usd": delta_usd,
            }

            # 5. 检查重置条件
            reset_context = {
                **base_context,
                "entry_level": state.entry_level,
                "exit_level": state.exit_level,
                "total_entry_amount": state.total_entry_amount,
                "total_entry_usd": state.total_entry_usd,
                "average_entry_price": state.average_entry_price,
            }
            if self._check_reset_condition(state, reset_context):
                self.logger.info("[%s] Reset condition met, resetting state", symbol)
                state.reset()

            # 6. 计算订单意图
            if self.config.use_new_format:
                intents = self._calculate_intents_new_format(
                    exchange, symbol, current_price, state, base_context
                )
            else:
                intents = self._calculate_intents_legacy(
                    exchange, symbol, current_price, state, delta_usd
                )

            # 7. 管理订单
            if intents:
                created, cancelled, reused = await self.manage_limit_orders(
                    exchange, symbol, intents, current_price
                )

            return ExecutionResult(
                exchange_class=exchange.class_name,
                symbol=symbol,
                success=True,
                exchange_name=exchange.name,
                delta_usd=delta_usd,
            )

        except Exception as e:
            self.logger.exception("[%s] Error: %s", exchange.name, e)
            return ExecutionResult(
                exchange_class=exchange.class_name,
                symbol=symbol,
                success=False,
                exchange_name=exchange.name,
                delta_usd=delta_usd,
                error=str(e),
            )

    def _calculate_intents_new_format(
        self,
        exchange: "BaseExchange",
        symbol: str,
        current_price: float,
        state: PCAState,
        base_context: dict[str, Any],
    ) -> list[OrderIntent]:
        """
        计算订单意图（新格式）

        Feature 0010 Phase 5
        """
        intents = []

        # === 入场订单 ===
        entry_orders = self.config.entry_orders or (
            [self.config.entry_order] if self.config.entry_order else []
        )

        for level in range(self.config.entry_order_levels):
            for order_def in entry_orders:
                context = self._build_order_context(
                    base_context, state, "entry", level,
                    exchange.name, symbol
                )
                intent = self._evaluate_order(
                    order_def, context, exchange.name, symbol, "entry", current_price
                )
                if intent:
                    # 设置 level 为 entry 类型
                    intent.level = level
                    intents.append(intent)

        # === 出场订单 ===
        exit_orders = self.config.exit_orders or (
            [self.config.exit_order] if self.config.exit_order else []
        )

        for level in range(self.config.exit_order_levels):
            for order_def in exit_orders:
                context = self._build_order_context(
                    base_context, state, "exit", level,
                    exchange.name, symbol
                )
                intent = self._evaluate_order(
                    order_def, context, exchange.name, symbol, "exit", current_price
                )
                if intent:
                    # 设置 level 为 exit 类型（用负数区分）
                    intent.level = -(level + 1)
                    intents.append(intent)

        return intents

    def _calculate_intents_legacy(
        self,
        exchange: "BaseExchange",
        symbol: str,
        current_price: float,
        state: PCAState,
        delta_usd: float,
    ) -> list[OrderIntent]:
        """
        计算订单意图（旧格式，向后兼容）

        保持原有的马丁格尔逻辑。
        """
        intents = []
        position_usd = state.last_position_amount * current_price

        # 确定方向
        if delta_usd > 0:
            open_side = "buy"
            close_side = "sell"
        else:
            open_side = "sell"
            close_side = "buy"

        # === 开仓单 ===
        if state.addition_count < self.config.max_additions:
            order_usd = self.config.base_order_usd * (
                self.config.amount_multiplier ** state.addition_count
            )
            spread = self.config.spread_open * (
                self.config.spread_multiplier ** state.addition_count
            )

            # 开仓价格：更优价格
            if open_side == "buy":
                open_price = current_price * (1 - spread)
            else:
                open_price = current_price * (1 + spread)

            amount = abs(self.usd_to_amount(
                exchange, symbol, order_usd, current_price
            ))

            intents.append(OrderIntent(
                side=open_side,
                level=0,
                price=open_price,
                amount=amount,
                timeout=self.config.timeout,
                refresh_tolerance=self.config.refresh_tolerance,
            ))

        # === 平仓单 ===
        has_long = position_usd > self.config.base_order_usd * 0.5
        has_short = position_usd < -self.config.base_order_usd * 0.5

        if (has_long and close_side == "sell") or (has_short and close_side == "buy"):
            cost = state.cost_price if state.cost_price > 0 else current_price

            if has_long:
                close_price = cost * (1 + self.config.spread_close)
            else:
                close_price = cost * (1 - self.config.spread_close)

            close_amount = abs(state.last_position_amount)

            intents.append(OrderIntent(
                side=close_side,
                level=1,
                price=close_price,
                amount=close_amount,
                timeout=self.config.timeout,
                refresh_tolerance=self.config.refresh_tolerance,
            ))

        return intents

    @property
    def log_state_dict(self) -> dict:
        # 汇总状态信息
        entries = {}
        for (ex, sym), state in self._pca_states.items():
            if state.entry_level > 0 or state.total_entry_amount > 0:
                entries[f"{ex}:{sym}"] = {
                    "entry_level": state.entry_level,
                    "total_amount": round(state.total_entry_amount, 6),
                    "avg_price": round(state.average_entry_price, 4),
                }

        return {
            **super().log_state_dict,
            "pca_states": entries if entries else "none",
        }


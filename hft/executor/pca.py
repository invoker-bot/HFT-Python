"""
PCAExecutor - Position Cost Averaging 执行器

马丁格尔/DCA 风格的执行器：
- 开仓单：在更优价格等待加仓（多仓在下方买，空仓在上方卖）
- 平仓单：在盈利价格等待止盈
- 订单挂出后不频繁变更，等待成交或超时

加仓逻辑：
- 第 n 次加仓金额 = base_order_usd * (amount_multiplier ^ n)
- 第 n 次加仓距离 = spread_open * (spread_multiplier ^ n)
"""
from typing import TYPE_CHECKING, Optional
from dataclasses import dataclass
from .base import BaseExecutor, ExecutionResult, OrderIntent
from .config import PCAExecutorConfig

if TYPE_CHECKING:
    from ..exchange.base import BaseExchange


@dataclass
class PositionInfo:
    """仓位信息"""
    amount: float          # 仓位数量（正=多，负=空）
    cost_price: float      # 成本价
    addition_count: int    # 已加仓次数


class PCAExecutor(BaseExecutor):
    """
    Position Cost Averaging 执行器

    特点：
    - 订单不频繁变更（长 timeout）
    - 根据加仓次数递增金额和距离
    - 同时维护开仓单和平仓单
    """

    config: PCAExecutorConfig

    def __init__(self, config: PCAExecutorConfig):
        super().__init__(config)
        # 追踪每个 (exchange, symbol) 的加仓状态
        self._position_info: dict[tuple[str, str], PositionInfo] = {}

    @property
    def per_order_usd(self) -> float:
        return self.config.base_order_usd

    @property
    def cancel_delay(self) -> float:
        # PCA 不需要 cancel_delay 机制，用 timeout 控制
        return self.config.timeout + 1

    def _get_position_info(
        self,
        exchange_name: str,
        symbol: str,
        current_amount: float,
        current_price: float,
    ) -> PositionInfo:
        """获取或更新仓位信息"""
        key = (exchange_name, symbol)
        info = self._position_info.get(key)

        if info is None:
            # 首次：初始化
            info = PositionInfo(
                amount=current_amount,
                cost_price=current_price if current_amount != 0 else 0,
                addition_count=0,
            )
            self._position_info[key] = info
        else:
            # 检测仓位变化
            old_sign = 1 if info.amount > 0 else (-1 if info.amount < 0 else 0)
            new_sign = 1 if current_amount > 0 else (-1 if current_amount < 0 else 0)

            if new_sign != old_sign:
                # 方向变化或清仓：重置
                info.amount = current_amount
                info.cost_price = current_price if current_amount != 0 else 0
                info.addition_count = 0
            elif abs(current_amount) > abs(info.amount) * 1.01:
                # 仓位增加（加仓成交）：更新成本价
                old_value = abs(info.amount) * info.cost_price
                added_amount = abs(current_amount) - abs(info.amount)
                # 假设新增部分以当前价格成交（近似）
                new_value = old_value + added_amount * current_price
                info.cost_price = new_value / abs(current_amount)
                info.amount = current_amount
                info.addition_count += 1
            elif abs(current_amount) < abs(info.amount) * 0.99:
                # 仓位减少（平仓成交）：保持成本价，可能重置加仓次数
                info.amount = current_amount
                if abs(current_amount) < self.config.base_order_usd / current_price * 0.1:
                    # 几乎清仓
                    info.addition_count = 0
                    info.cost_price = 0
            else:
                # 仓位基本不变
                info.amount = current_amount

        return info

    def _calculate_order_usd(self, addition_count: int) -> float:
        """计算第 n 次加仓的金额"""
        return self.config.base_order_usd * (
            self.config.amount_multiplier ** addition_count
        )

    def _calculate_open_spread(self, addition_count: int) -> float:
        """计算第 n 次加仓的距离"""
        return self.config.spread_open * (
            self.config.spread_multiplier ** addition_count
        )

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

            # 2. 更新仓位信息
            info = self._get_position_info(
                exchange.name, symbol, current_amount, current_price
            )

            # 3. 计算订单意图
            intents = self._calculate_intents(
                exchange, symbol, current_price, info, delta_usd
            )

            # 4. 管理订单
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

    def _calculate_intents(
        self,
        exchange: "BaseExchange",
        symbol: str,
        current_price: float,
        info: PositionInfo,
        delta_usd: float,
    ) -> list[OrderIntent]:
        """
        计算订单意图

        根据 delta_usd 方向和当前仓位：
        - delta > 0: 想做多，开仓单=买，平仓单=卖（如果有空仓）
        - delta < 0: 想做空，开仓单=卖，平仓单=买（如果有多仓）
        """
        intents = []
        position_usd = info.amount * current_price

        # 确定方向
        if delta_usd > 0:
            open_side = "buy"
            close_side = "sell"
        else:
            open_side = "sell"
            close_side = "buy"

        # === 开仓单 ===
        if info.addition_count < self.config.max_additions:
            order_usd = self._calculate_order_usd(info.addition_count)
            spread = self._calculate_open_spread(info.addition_count)

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
                level=0,  # 开仓单固定 level=0
                price=open_price,
                amount=amount,
                timeout=self.config.timeout,
                refresh_tolerance=self.config.refresh_tolerance,
            ))

            self.logger.debug(
                "[%s] %s open: add=%d, usd=%.0f, spread=%.2f%%, price=%.4f",
                exchange.name, symbol, info.addition_count,
                order_usd, spread * 100, open_price
            )

        # === 平仓单 ===
        # 只有有仓位且方向相反时才挂平仓单
        has_long = position_usd > self.config.base_order_usd * 0.5
        has_short = position_usd < -self.config.base_order_usd * 0.5

        if (has_long and close_side == "sell") or (has_short and close_side == "buy"):
            # 平仓价格：相对成本价的盈利价格
            cost = info.cost_price if info.cost_price > 0 else current_price

            if has_long:
                # 多仓平仓：在成本价上方卖
                close_price = cost * (1 + self.config.spread_close)
            else:
                # 空仓平仓：在成本价下方买
                close_price = cost * (1 - self.config.spread_close)

            # 平仓数量：全部仓位
            close_amount = abs(info.amount)

            intents.append(OrderIntent(
                side=close_side,
                level=1,  # 平仓单固定 level=1
                price=close_price,
                amount=close_amount,
                timeout=self.config.timeout,
                refresh_tolerance=self.config.refresh_tolerance,
            ))

            self.logger.debug(
                "[%s] %s close: cost=%.4f, target=%.4f, amount=%.6f",
                exchange.name, symbol, cost, close_price, close_amount
            )

        return intents

    @property
    def log_state_dict(self) -> dict:
        # 汇总加仓信息
        additions = {}
        for (ex, sym), info in self._position_info.items():
            if info.addition_count > 0:
                additions[f"{ex}:{sym}"] = info.addition_count

        return {
            **super().log_state_dict,
            "additions": additions if additions else "none",
        }

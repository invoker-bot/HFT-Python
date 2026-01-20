"""
LimitExecutor - 限价单执行器

做市类执行器，支持多层限价单。
子类只需计算目标价格，订单生命周期由基类管理。

Feature 0005: 支持动态参数（表达式或字面量）
"""
from typing import TYPE_CHECKING

from ..base import BaseExecutor, ExecutionResult, OrderIntent

if TYPE_CHECKING:
    from ...exchange.base import BaseExchange
    from .config import LimitExecutorConfig


class LimitExecutor(BaseExecutor):
    """
    限价单执行器

    功能：
    - 多层订单，每层独立的 spread, timeout, per_order_usd
    - 订单复用、取消延迟由基类处理

    子类职责：
    - 只需计算每层的目标价格
    """

    config: "LimitExecutorConfig"

    def __init__(self, config: "LimitExecutorConfig"):
        super().__init__(config)

    @property
    def per_order_usd(self) -> float:
        if self.config.orders:
            return self.config.orders[0].per_order_usd
        return 100.0

    async def execute_delta(
        self,
        exchange: "BaseExchange",
        symbol: str,
        delta_usd: float,
        speed: float,
        current_price: float,
    ) -> ExecutionResult:
        """执行限价单策略"""
        try:
            await exchange.medal_initialize_symbol(symbol)

            # 计算订单意图
            intents = self._calculate_intents(
                exchange, symbol, delta_usd, current_price, speed
            )

            # 调用基类管理订单
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
        delta_usd: float,
        current_price: float,
        speed: float = 0.5,
    ) -> list[OrderIntent]:
        """
        计算订单意图

        Feature 0005: 支持动态参数求值

        Args:
            exchange: 交易所实例
            symbol: 交易对
            delta_usd: 仓位差值
            current_price: 当前价格
            speed: 执行紧急度

        Returns:
            OrderIntent 列表
        """
        intents = []
        direction = 1 if delta_usd > 0 else -1

        # 收集上下文变量
        context = self.collect_context_vars(
            exchange_class=exchange.class_name,
            symbol=symbol,
            direction=direction,
            speed=speed,
            notional=abs(delta_usd),
        )
        # 添加 mid_price 到上下文
        context["mid_price"] = current_price

        for idx, level in enumerate(self.config.orders):
            # 求值动态参数
            reverse = self.evaluate_param(level.reverse, context)
            spread = self.evaluate_param(level.spread, context)
            refresh_tolerance = self.evaluate_param(level.refresh_tolerance, context)
            timeout = self.evaluate_param(level.timeout, context)
            per_order_usd = self.evaluate_param(level.per_order_usd, context)

            # 类型转换和默认值
            reverse = bool(reverse) if reverse is not None else False
            spread = float(spread) if spread is not None else 0.001
            refresh_tolerance = float(refresh_tolerance) if refresh_tolerance is not None else 0.5
            timeout = float(timeout) if timeout is not None else 60.0
            per_order_usd = float(per_order_usd) if per_order_usd is not None else 100.0

            # 确定方向
            if reverse:
                side = "sell" if delta_usd > 0 else "buy"
            else:
                side = "buy" if delta_usd > 0 else "sell"

            # 计算价格（spread 现在是绝对价差）
            if side == "buy":
                price = current_price - spread
            else:
                price = current_price + spread

            # 计算数量
            amount = abs(self.usd_to_amount(
                exchange, symbol, per_order_usd, current_price
            ))

            intents.append(OrderIntent(
                side=side,
                level=idx,
                price=price,
                amount=amount,
                timeout=timeout,
                refresh_tolerance=refresh_tolerance,
            ))

        return intents

    @property
    def log_state_dict(self) -> dict:
        return {
            **super().log_state_dict,
            "levels": len(self.config.orders),
        }

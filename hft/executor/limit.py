"""
LimitExecutor - 限价单执行器

做市类执行器，支持多层限价单。
子类只需计算目标价格，订单生命周期由基类管理。
"""
from typing import TYPE_CHECKING
from .base import BaseExecutor, ExecutionResult, OrderIntent
from .config import LimitExecutorConfig

if TYPE_CHECKING:
    from ..exchange.base import BaseExchange


class LimitExecutor(BaseExecutor):
    """
    限价单执行器

    功能：
    - 多层订单，每层独立的 spread, timeout, per_order_usd
    - 订单复用、取消延迟由基类处理

    子类职责：
    - 只需计算每层的目标价格
    """

    config: LimitExecutorConfig

    def __init__(self, config: LimitExecutorConfig):
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
                exchange, symbol, delta_usd, current_price
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
    ) -> list[OrderIntent]:
        """
        计算订单意图

        Returns:
            OrderIntent 列表
        """
        intents = []

        for idx, level in enumerate(self.config.orders):
            # 确定方向
            if level.reverse:
                side = "sell" if delta_usd > 0 else "buy"
            else:
                side = "buy" if delta_usd > 0 else "sell"

            # 计算价格
            if side == "buy":
                price = current_price * (1 - level.spread)
            else:
                price = current_price * (1 + level.spread)

            # 计算数量
            amount = abs(self.usd_to_amount(
                exchange, symbol, level.per_order_usd, current_price
            ))

            intents.append(OrderIntent(
                side=side,
                level=idx,
                price=price,
                amount=amount,
                timeout=level.timeout,
                refresh_tolerance=level.refresh_tolerance,
            ))

        return intents

    @property
    def log_state_dict(self) -> dict:
        return {
            **super().log_state_dict,
            "levels": len(self.config.orders),
        }

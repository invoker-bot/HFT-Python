"""
MarketExecutor - 市价单执行器

Feature 0005: 支持动态参数（表达式或字面量）

执行流程：
1. BaseExecutor 调用 execute_delta() 传入差值（USD）
2. 将 USD 差值转换为交易数量
3. 执行市价单
"""
from typing import TYPE_CHECKING

from ..base import BaseExecutor, ExecutionResult

if TYPE_CHECKING:
    from ...exchange.base import BaseExchange
    from .config import MarketExecutorConfig


class MarketExecutor(BaseExecutor):
    """
    市价单执行器

    Feature 0005: 支持 condition 和动态 per_order_usd
    """

    config: "MarketExecutorConfig"

    def __init__(self, config: "MarketExecutorConfig"):
        super().__init__(config)

    @property
    def per_order_usd(self) -> float:
        """从配置获取单笔订单大小（静态值）"""
        val = self.config.per_order_usd
        if isinstance(val, (int, float)):
            return float(val)
        return 100.0  # 默认值

    def get_dynamic_per_order_usd(
        self,
        exchange_class: str,
        symbol: str,
        direction: int,
        speed: float,
        notional: float,
    ) -> float:
        """
        获取动态 per_order_usd（支持表达式）

        覆盖 BaseExecutor 的默认实现，支持表达式求值。
        """
        val = self.config.per_order_usd
        if isinstance(val, (int, float)):
            return float(val)

        # 表达式求值
        context = self.collect_context_vars(
            exchange_class=exchange_class,
            symbol=symbol,
            direction=direction,
            speed=speed,
            notional=notional,
        )
        result = self.evaluate_param(val, context)
        return float(result) if result is not None else 100.0

    async def execute_delta(
        self,
        exchange: "BaseExchange",
        symbol: str,
        delta_usd: float,
        speed: float,
        current_price: float,
    ) -> ExecutionResult:
        """
        执行仓位调整

        使用市价单执行给定的 USD 差值。

        Args:
            exchange: 交易所实例
            symbol: 交易对
            delta_usd: 需要调整的 USD 价值（正=买入，负=卖出）
            speed: 执行紧急度 [0, 1]（市价单不使用此参数）
            current_price: 当前价格

        Returns:
            执行结果
        """
        try:
            # 1. 初始化交易对（设置杠杆等）
            await exchange.medal_initialize_symbol(symbol)

            # 2. 将 USD 转换为交易数量
            amount = abs(self.usd_to_amount(exchange, symbol, delta_usd, current_price))
            if amount <= 0:
                return ExecutionResult(
                    exchange_class=exchange.class_name,
                    symbol=symbol,
                    success=False,
                    exchange_name=exchange.name,
                    delta_usd=delta_usd,
                    error="Invalid price or amount"
                )
            side = "buy" if delta_usd > 0 else "sell"

            # 3. 执行市价单
            order = await exchange.create_order(
                symbol=symbol,
                type="market",
                side=side,
                amount=amount,
            )

            if order is None:
                return ExecutionResult(
                    exchange_class=exchange.class_name,
                    symbol=symbol,
                    success=False,
                    exchange_name=exchange.name,
                    delta_usd=delta_usd,
                    error="Order creation returned None"
                )

            filled_amount = float(order.get('filled', amount))
            average_price = float(order.get('average', current_price))

            self.logger.info(
                "[%s] %s %s: filled=%.6f @ %.2f",
                exchange.name, side.upper(), symbol, filled_amount, average_price
            )

            return ExecutionResult(
                exchange_class=exchange.class_name,
                symbol=symbol,
                success=True,
                exchange_name=exchange.name,
                delta_usd=delta_usd,
                order_id=order.get('id'),
                filled_amount=filled_amount,
                average_price=average_price,
            )

        except Exception as e:
            self.logger.exception(
                "[%s] Error executing %s on %s: %s",
                exchange.name, "BUY" if delta_usd > 0 else "SELL", symbol, e
            )
            return ExecutionResult(
                exchange_class=exchange.class_name,
                symbol=symbol,
                success=False,
                exchange_name=exchange.name,
                delta_usd=delta_usd,
                error=str(e)
            )

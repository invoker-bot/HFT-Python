"""
MarketExecutor - 市价单执行器

最简单的执行器实现，适合以下场景：
- 快速原型开发和测试
- 对执行价格不敏感的策略
- 流动性充足的主流交易对

执行流程：
1. BaseExecutor 调用 execute_delta() 传入差值（USD）
2. 将 USD 差值转换为交易数量
3. 执行市价单

注意事项：
- 大额订单可能产生较大滑点
- 不支持分批执行，不适合大仓位
- 所有账户同步执行，适合个人多账户管理

Example config (conf/executor/market/default.yaml):
    class_name: market
    per_order_usd: 100.0
    interval: 1.0
"""
from typing import TYPE_CHECKING
from .base import BaseExecutor, ExecutionResult
from .config import MarketExecutorConfig

if TYPE_CHECKING:
    from ..exchange.base import BaseExchange


class MarketExecutor(BaseExecutor):
    """
    市价单执行器

    继承自 BaseExecutor，实现最简单的市价单执行逻辑。

    执行逻辑：
    1. 从 BaseExecutor 接收 delta_usd（需要调整的仓位价值）
    2. 初始化交易对（设置杠杆等）
    3. 将 USD 价值转换为交易数量
    4. 执行市价单

    配置参数（MarketExecutorConfig）：
        per_order_usd: 单笔订单大小 / 执行阈值（USD）
        interval: tick 间隔（秒）

    特点：
        - 简单直接，无拆单逻辑
        - 所有账户并行执行
        - 适合流动性好的交易对
    """

    config: MarketExecutorConfig

    def __init__(self, config: MarketExecutorConfig):
        """
        初始化市价单执行器

        Args:
            config: 市价单执行器配置
        """
        super().__init__(config)

    @property
    def per_order_usd(self) -> float:
        """从配置获取单笔订单大小"""
        return self.config.per_order_usd

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

"""
账户权益数据源

Feature 0008: Strategy 数据驱动增强

MedalEquationDataSource 是 ExchangePath 级别的 Indicator，
用于获取特定交易所实例的账户总权益（USD）。

"""
from typing import Any
from ..base import BaseExchangeDataIndicator


class MedalEquationDataSource(BaseExchangeDataIndicator[float]):
    """
    账户权益数据源（Feature 0008）

    ExchangePath 级别的 Indicator，定期获取账户总权益。

    提供变量：
    - equation_usd: 账户总权益（USD）

    使用场景：
    - Strategy 根据账户权益动态计算目标仓位
    - 如 position_usd = 0.6 * equation_usd
    """
    DEFAULT_IS_ARRAY = False

    @property
    def interval(self) -> float:
        return 10.0  # 每 10 秒获取一次

    async def on_tick(self):
        if not self.exchange.ready:
            return
        usd = await self.exchange.medal_fetch_total_balance_usd()
        await self.data.update(usd)

    def get_vars(self) -> dict[str, Any]:
        """
        返回账户权益变量

        提供：
        - equation_usd: 账户总权益（USD）
        """
        equation_usd = self.data.get_data()
        if equation_usd is not None:
            return {"equation_usd": equation_usd}
        raise ValueError("Equation USD is not available")

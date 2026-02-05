"""
账户权益数据源

Feature 0008: Strategy 数据驱动增强

MedalEquationDataSource 是 ExchangePath 级别的 Indicator，
用于获取特定交易所实例的账户总权益（USD）。

"""
from typing import TYPE_CHECKING, Any

from .base import BaseExchangeDataSource

if TYPE_CHECKING:
    from ...core.app import AppCore


class MedalEquationDataSource(BaseExchangeDataSource[float]):
    """
    账户权益数据源（Feature 0008）

    ExchangePath 级别的 Indicator，定期获取账户总权益。

    提供变量：
    - equation_usd: 账户总权益（USD）

    使用场景：
    - Strategy 根据账户权益动态计算目标仓位
    - 如 position_usd = 0.6 * equation_usd
    """
    DEFAULT_HEALTHY_RANGE = 0.01

    @property
    def interval(self) -> float:
        return 10.0  # 每 10 秒获取一次

    async def on_tick(self):
        await super().on_tick()
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
        result = {
            "equation_usd_history": self.data.data_list
        }
        equation_usd = self.data.get_data()
        if equation_usd is not None:
            result["equation_usd"] = equation_usd
        return result

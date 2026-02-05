"""
MedalAmountDataSource - 账户余额数据源

用于 MarketNeutralPositions 策略，获取合约/现货账户的真实存量。
"""
from typing import Any, Dict
from .base import BaseTradingPairDataSource


class MedalAmountDataSource(BaseTradingPairDataSource[float]):
    """
    账户余额数据源

    特性：
    - 汇总合约/现货账户的真实存量
    - 形成标准 amount 字段
    - 注入到 exchange scope
    """
    async def on_tick(self) -> bool:
        """定期获取账户余额"""
        await super().on_tick()
        if not self.exchange.ready:
            return
        amount = await self.exchange.medal_get_pair_amount(self.symbol)
        await self.data.update(amount)

    def get_vars(self) -> Dict[str, Any]:
        """
        计算变量（注入到 exchange scope）

        Returns:
            变量字典：{"amount": amount}
        """
        latest = self.data.get_data()
        if latest is not None:
            return {"amount": latest}

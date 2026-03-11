"""
SimulatedOKXExchange - 模拟 OKX 交易所
"""
from typing import ClassVar, Type
from .base import SimulatedExchange, SimulatedExchangeConfig


class SimulatedOKXExchangeConfig(SimulatedExchangeConfig):
    """模拟 OKX 配置"""
    class_name: ClassVar[str] = "sim_okx"

    @classmethod
    def get_class_type(cls) -> Type["SimulatedOKXExchange"]:
        return SimulatedOKXExchange


class SimulatedOKXExchange(SimulatedExchange):
    """模拟 OKX 交易所"""
    class_name: ClassVar[str] = "sim_okx"
    unified_account: ClassVar[bool] = True  # OKX 统一账户

    def _default_order_params(self) -> dict:
        return {"posSide": "net"}

    async def medal_fetch_balance_usd(self, ccxt_instance_key: str) -> float:
        balance = self.balance_tracker.to_ccxt_format(ccxt_instance_key)
        try:
            return float(balance['info']['data'][0]['totalEq'])
        except (KeyError, IndexError):
            return self.balance_tracker.get_usdt_balance()

    async def medal_fetch_total_balance_usd(self) -> float:
        return self.balance_tracker.get_usdt_balance()

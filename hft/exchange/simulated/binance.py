"""
SimulatedBinanceExchange - 模拟 Binance 交易所
"""
from typing import ClassVar, Type
from .base import SimulatedExchange, SimulatedExchangeConfig


class SimulatedBinanceExchangeConfig(SimulatedExchangeConfig):
    """模拟 Binance 配置"""
    class_name: ClassVar[str] = "sim_binance"

    @classmethod
    def get_class_type(cls) -> Type["SimulatedBinanceExchange"]:
        return SimulatedBinanceExchange


class SimulatedBinanceExchange(SimulatedExchange):
    """模拟 Binance 交易所"""
    class_name: ClassVar[str] = "sim_binance"
    unified_account: ClassVar[bool] = False  # Binance 现货和合约分开

    async def medal_fetch_balance_usd(self, ccxt_instance_key: str) -> float:
        balance = self.balance_tracker.to_ccxt_format(ccxt_instance_key)
        wallet_balance = balance.get('info', {}).get('totalWalletBalance', '0')
        return float(wallet_balance)

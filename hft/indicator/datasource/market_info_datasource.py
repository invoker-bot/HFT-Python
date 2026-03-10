"""
市场信息数据源

向 scope 注入交易对的 market/currency 基本信息，
如 is_spot, is_swap, can_withdraw, can_deposit 等。
"""
import time
from typing import Any
from ..base import BaseTradingPairClassDataIndicator


class MarketInfoDataSource(BaseTradingPairClassDataIndicator[dict]):
    """
    市场信息数据源

    TradingPairClass 级别的 Indicator，定期获取交易对的市场和币种信息。

    提供变量：
    - is_spot, is_swap, is_future, is_option, is_margin: 市场类型
    - is_contract, is_linear, is_inverse: 合约类型
    - is_active: 市场是否激活
    - taker_fee, maker_fee: 手续费率
    - contract_size: 合约面值
    - can_withdraw, can_deposit: 币种是否可提现/充值
    """
    DEFAULT_IS_ARRAY = False
    DEFAULT_MAX_AGE = 120.0  # 市场信息变化慢，放宽过期时间

    @property
    def interval(self) -> float:
        return 60.0

    async def on_tick(self):
        if not self.exchange.ready:
            return
        markets = await self.exchange.get_markets_data()
        market = markets.get(self.symbol)
        if market is None:
            return

        info = {
            # market 类型
            "is_spot": bool(market.get("spot")),
            "is_swap": bool(market.get("swap")),
            "is_future": bool(market.get("future")),
            "is_option": bool(market.get("option")),
            "is_margin": bool(market.get("margin")),
            # 合约属性
            "is_contract": bool(market.get("contract")),
            "is_linear": bool(market.get("linear")),
            "is_inverse": bool(market.get("inverse")),
            # 状态
            "is_active": bool(market.get("active")),
            # 费率
            "taker_fee": float(market.get("taker") or 0),
            "maker_fee": float(market.get("maker") or 0),
            # 合约面值
            "contract_size": float(market.get("contractSize") or 1),
        }

        # 获取 base currency 的充提信息
        base = market.get("base")
        if base:
            try:
                currencies = await self.exchange.get_currencies_data()
                currency = currencies.get(base)
                if currency:
                    info["can_withdraw"] = bool(currency.get("withdraw"))
                    info["can_deposit"] = bool(currency.get("deposit"))
            except Exception:
                pass

        info.setdefault("can_withdraw", False)
        info.setdefault("can_deposit", False)

        await self.data.update(info, time.time())

    def get_vars(self) -> dict[str, Any]:
        """返回市场信息变量"""
        data = self.data.get_data()
        if data is not None:
            return data
        raise ValueError("Market info is not available")

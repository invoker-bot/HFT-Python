"""
Binance 交易所实现
"""
from typing import ClassVar

from cachetools import TTLCache
from cachetools_async import cached

from ..base import BaseExchange, FundingRate, FundingRateBill


class BinanceExchange(BaseExchange):
    """
    Binance 交易所实现

    支持：
    - U本位永续合约
    - 资金费率获取
    - 健康检查
    """
    class_name: ClassVar[str] = "binance"

    # API 端点
    REST_URL = "https://fapi.binance.com"
    FUNDING_INFO_ENDPOINT = "/fapi/v1/fundingInfo"
    PREMIUM_INDEX_ENDPOINT = "/fapi/v1/premiumIndex"
    EXCHANGE_INFO_ENDPOINT = "/fapi/v1/exchangeInfo"
    PING_ENDPOINT = "/fapi/v1/ping"

    def medal_balance_usd(self, data):
        return float(data['info'].get('totalWalletBalance', 0.0))

    @cached(TTLCache(maxsize=32, ttl=30))
    async def __fetch_symbols(self) -> dict[str, dict]:
        """获取所有永续合约交易对"""
        data = await self.exchanges['swap'].fetch(f"{self.REST_URL}{self.EXCHANGE_INFO_ENDPOINT}")
        return {
            s["symbol"]: s
            for s in data.get("symbols", [])
            if s.get("contractType") == "PERPETUAL" and s.get("status") == "TRADING"
        }

    @staticmethod
    def __to_ccxt_symbol_id(symbol_data: dict) -> str:
        """将原始交易对数据转换为 ccxt 交易对 ID"""
        base = symbol_data['baseAsset']
        quote = symbol_data['quoteAsset']
        margin = symbol_data["marginAsset"]
        return f"{base}/{quote}:{margin}"

    @cached(TTLCache(maxsize=32, ttl=30))
    async def __fetch_fundings(self) -> dict[str, dict]:
        """获取资金费率信息"""
        fundings = await self.exchanges['swap'].fetch(f"{self.REST_URL}{self.FUNDING_INFO_ENDPOINT}")
        return {f["symbol"]: f for f in fundings}

    async def __fetch_premium_indices(self) -> dict[str, dict]:
        """获取溢价指数"""
        indices = await self.exchanges['swap'].fetch(f"{self.REST_URL}{self.PREMIUM_INDEX_ENDPOINT}")
        # for idx in indices:
        #     symbol = idx["symbol"]
        #     ts = float(idx['time']) / 1000.0
        #     # self._mark_prices_cache[symbol].append(ts, float(idx['markPrice']))
        #     # self._index_prices_cache[symbol].append(ts, float(idx['indexPrice']))
        return {indice["symbol"]: indice for indice in indices}

    @cached(TTLCache(maxsize=32, ttl=3))
    async def medal_fetch_funding_rates(self) -> dict[str, FundingRate]:
        """获取所有交易对的资金费率"""
        funding_rates = {}

        symbols_dict = await self.__fetch_symbols()
        fundings_dict = await self.__fetch_fundings()
        indices_dict = await self.__fetch_premium_indices()

        # 更新资金费率缓存
        # for raw_symbol, idx in indices_dict.items():
        #     ts = float(idx['time']) / 1000.0
        #     rate = float(idx['lastFundingRate'])
        #     self._funding_rates_cache[raw_symbol].append(ts, rate)

        for raw_symbol, symbol_data in symbols_dict.items():
            info = fundings_dict.get(raw_symbol, None)
            indices = indices_dict.get(raw_symbol, None)

            if info and indices:
                ts = float(indices['time']) / 1000.0
                symbol = self.__to_ccxt_symbol_id(symbol_data)
                funding_interval_hours = int(info['fundingIntervalHours'])
                base_funding_rate = float(info['interestRate']) * funding_interval_hours / 8.0
                funding_rate = FundingRate(
                    exchange=self.class_name,
                    symbol=symbol,
                    timestamp=ts,
                    expiry=float(symbol_data['deliveryDate']) / 1000,
                    base_funding_rate=base_funding_rate,
                    next_funding_rate=float(indices['lastFundingRate']),
                    next_funding_timestamp=float(indices['nextFundingTime']) / 1000,
                    funding_interval_hours=funding_interval_hours,
                    mark_price=float(indices['markPrice']),
                    mark_price_timestamp=ts,
                    index_price=float(indices['indexPrice']),
                    index_price_timestamp=ts,
                    minimum_funding_rate=float(info.get('adjustedFundingRateFloor', -0.02)),
                    maximum_funding_rate=float(info.get('adjustedFundingRateCap', 0.02)),
                )
                funding_rates[symbol] = funding_rate

        return funding_rates

    async def medal_fetch_funding_rates_history(self) -> list[FundingRateBill]:
        """获取资金费率账单"""
        bills = []
        try:
            symbols_dict = await self.__fetch_symbols()
            raws = await self.exchanges["swap"].fapiprivate_get_income({
                "incomeType": "FUNDING_FEE",
                "limit": 100
            })
            for raw in raws:
                raw_symbol = raw.get('symbol', '')
                symbol_data = symbols_dict.get(raw_symbol, None)
                if symbol_data is None:
                    continue
                symbol = self.__to_ccxt_symbol_id(symbol_data)
                bill = FundingRateBill(
                    id=str(raw['tranId']),
                    symbol=symbol,
                    funding_time=float(raw['time']) / 1000.0,
                    funding_amount=float(raw['income']),
                )
                bills.append(bill)
        except Exception as e:
            self.logger.warning("Failed to fetch funding history: %s", e)
        return bills

    async def on_health_check(self):
        """健康检查"""
        await super().on_health_check()
        await self.config.ccxt_instance.fetch(f"{self.REST_URL}{self.PING_ENDPOINT}")
        return True

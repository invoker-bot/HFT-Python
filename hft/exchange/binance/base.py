"""
Binance 交易所实现
"""
import logging
from typing import ClassVar, Type
from cachetools import TTLCache
from cachetools_async import cached
from ..base import BaseExchange, FundingRate, FundingRateBill

logger = logging.getLogger(__name__)


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

    @cached(TTLCache(maxsize=32, ttl=30))
    async def _fetch_symbols(self) -> dict[str, dict]:
        """获取所有永续合约交易对"""
        data = await self.exchange.fetch(f"{self.REST_URL}{self.EXCHANGE_INFO_ENDPOINT}")
        return {
            s["symbol"]: s
            for s in data.get("symbols", [])
            if s.get("contractType") == "PERPETUAL" and s.get("status") == "TRADING"
        }

    @cached(TTLCache(maxsize=32, ttl=30))
    async def _fetch_fundings(self) -> dict[str, dict]:
        """获取资金费率信息"""
        fundings = await self.exchange.fetch(f"{self.REST_URL}{self.FUNDING_INFO_ENDPOINT}")
        return {f["symbol"]: f for f in fundings}

    async def _fetch_premium_indices(self) -> dict[str, dict]:
        """获取溢价指数"""
        indices = await self.exchange.fetch(f"{self.REST_URL}{self.PREMIUM_INDEX_ENDPOINT}")
        for idx in indices:
            symbol = idx["symbol"]
            ts = float(idx['time']) / 1000.0
            self._mark_prices_cache[symbol].append(ts, float(idx['markPrice']))
            self._index_prices_cache[symbol].append(ts, float(idx['indexPrice']))
        return {idx["symbol"]: idx for idx in indices}

    @cached(TTLCache(maxsize=32, ttl=5))
    async def fetch_funding_rates(self) -> dict[str, FundingRate]:
        """获取所有交易对的资金费率"""
        funding_rates = {}

        symbols_dict = await self._fetch_symbols()
        fundings_dict = await self._fetch_fundings()
        indices_dict = await self._fetch_premium_indices()

        # 更新资金费率缓存
        for raw_symbol, idx in indices_dict.items():
            ts = float(idx['time']) / 1000.0
            rate = float(idx['lastFundingRate'])
            self._funding_rates_cache[raw_symbol].append(ts, rate)

        for raw_symbol, symbol_data in symbols_dict.items():
            info = fundings_dict.get(raw_symbol)
            idx = indices_dict.get(raw_symbol)

            if info and idx:
                base = symbol_data['baseAsset']
                quote = symbol_data['quoteAsset']
                symbol = f"{base}/{quote}:{quote}"

                funding_rate = FundingRate(
                    exchange=self.class_name,
                    symbol=symbol,
                    funding_rate=float(idx['lastFundingRate']),
                    next_funding_rate=None,
                    funding_timestamp=float(idx['nextFundingTime']) / 1000,
                    funding_interval_hours=int(info.get('fundingIntervalHours', 8)),
                    mark_price=float(idx['markPrice']),
                    index_price=float(idx['indexPrice']),
                    min_funding_rate=float(info.get('adjustedFundingRateFloor', -0.03)),
                    max_funding_rate=float(info.get('adjustedFundingRateCap', 0.03)),
                )
                funding_rates[symbol] = funding_rate

        return funding_rates

    async def fetch_funding_rates_history(self) -> list[FundingRateBill]:
        """获取资金费率账单"""
        bills = []
        try:
            raws = await self.exchange.fapiprivate_get_income({
                "incomeType": "FUNDING_FEE",
                "limit": 100
            })
            for raw in raws:
                raw_symbol = raw.get('symbol', '')
                if raw_symbol.endswith("USDT"):
                    base = raw_symbol[:-4]
                    quote = "USDT"
                    symbol = f"{base}/{quote}:{quote}"
                    bill = FundingRateBill(
                        id=str(raw['tranId']),
                        symbol=symbol,
                        funding_time=float(raw['time']) / 1000.0,
                        funding_amount=float(raw['income']),
                    )
                    bills.append(bill)
        except Exception as e:
            logger.warning(f"[{self.class_name}] Failed to fetch funding history: {e}")
        return bills

    async def on_health_check(self) -> None:
        """健康检查"""
        if not self.ready:
            self._health = False
            return
        try:
            await self.exchange.fetch(f"{self.REST_URL}{self.PING_ENDPOINT}")
            self._health = True
        except Exception:
            self._health = False

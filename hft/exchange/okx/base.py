"""
OKX 交易所实现
"""
import logging
from typing import ClassVar
from ...core.cache_decorator import instance_cache
from ..base import BaseExchange, FundingRate, FundingRateBill
logger = logging.getLogger(__name__)


class OKXExchange(BaseExchange):
    """
    OKX 交易所实现

    支持：
    - U本位永续合约
    - 资金费率获取
    - 健康检查
    """
    class_name: ClassVar[str] = "okx"
    unified_account: ClassVar[bool] = True  # OKX 使用统一账户模式

    # API 端点
    REST_URL = "https://www.okx.com"
    FUNDING_ENDPOINT = "/api/v5/public/funding-rate"
    INSTRUMENTS_ENDPOINT = "/api/v5/public/instruments"
    TIME_ENDPOINT = "/api/v5/public/time"
    TICKER_ENDPOINT = "/api/v5/market/tickers"
    INDEX_PRICE_ENDPOINT = "/api/v5/market/index-tickers"
    MARK_PRICE_ENDPOINT = "/api/v5/public/mark-price"

    def _default_order_params(self) -> dict:
        """OKX 需要在所有订单中指定 posSide 参数（单向持仓模式）"""
        return {"posSide": "net"}

    async def set_leverage_and_cross_margin_mode(self, symbol: str, leverage: int):
        """设置保证金模式和持仓模式"""
        exchange = self.get_exchange(symbol)
        # 设置持仓模式为单向模式（net mode）
        try:
            await exchange.set_position_mode(hedged=False, symbol=symbol)
            self.logger.info("Set position mode to one-way (net)")
        except Exception as e:
            self.logger.debug("Failed to set position mode (may already be net): %s", e)
        # 设置保证金模式为全仓
        await exchange.set_margin_mode("cross", symbol, {
            "posSide": "net",
            "leverage": leverage
        })
        await exchange.set_leverage(leverage, symbol)

    def medal_balance_usd(self, data):
        return float(data['info']['data'][0]['totalEq'])

    async def medal_fetch_balance_usd(self, ccxt_instance_key):
        data = await self.exchanges[ccxt_instance_key].fetch_balance()
        return self.medal_balance_usd(data)

    async def medal_fetch_total_balance_usd(self):
        data = await self.config.ccxt_instance.fetch_balance()
        return self.medal_balance_usd(data)

    @staticmethod
    def _parse_response(result: dict) -> dict[str, dict]:
        """解析 OKX API 响应"""
        if int(result.get('code', -1)) != 0:
            raise ValueError(f"OKX API error: {result.get('msg', 'Unknown error')}")
        return {
            item['instId']: item
            for item in result.get('data', [])
            if item.get('instType') == 'SWAP'
        }

    @staticmethod
    def __to_ccxt_symbol_id(item: dict) -> str:
        """将原始交易对数据转换为 ccxt 交易对 ID"""
        base, quote = item['uly'].split('-')
        settle = item['settleCcy']
        return f"{base}/{quote}:{settle}"

    @instance_cache(ttl=30)
    async def __fetch_instruments(self) -> dict[str, dict]:
        """获取所有永续合约"""
        result = await self.exchanges["swap"].fetch(
            f"{self.REST_URL}{self.INSTRUMENTS_ENDPOINT}?instType=SWAP"
        )
        return self._parse_response(result)

    @instance_cache(ttl=30)
    async def __fetch_fundings(self) -> dict[str, dict]:
        """获取资金费率信息"""
        result = await self.exchanges["swap"].fetch(
            f"{self.REST_URL}{self.FUNDING_ENDPOINT}?instType=SWAP&instId=ANY"
        )
        return self._parse_response(result)

    @instance_cache(ttl=15)
    async def __fetch_tickers(self) -> dict[str, dict]:
        """获取所有 ticker"""
        result = await self.exchanges["swap"].fetch(
            f"{self.REST_URL}{self.TICKER_ENDPOINT}?instType=SWAP"
        )
        return self._parse_response(result)

    async def __fetch_index_prices(self) -> dict[str, dict]:
        """获取所有指数价格"""
        result = await self.exchanges["swap"].fetch(
            f"{self.REST_URL}{self.INDEX_PRICE_ENDPOINT}?instType=SWAP&quoteCcy=USDT"
        )
        return self._parse_response(result)

    async def __fetch_mark_prices(self) -> dict[str, dict]:
        """获取所有标记价格"""
        result = await self.exchanges["swap"].fetch(
            f"{self.REST_URL}{self.MARK_PRICE_ENDPOINT}?instType=SWAP&quoteCcy=USDT"
        )
        return self._parse_response(result)

    # async def _update_price_cache(self) -> None:
    #     """更新标记价格和指数价格缓存"""
    #     mark_res = await self.exchange.fetch(
    #         f"{self.REST_URL}{self.MARK_PRICE_ENDPOINT}?instType=SWAP"
    #     )
    #     index_res = await self.exchange.fetch(
    #         f"{self.REST_URL}{self.INDEX_PRICE_ENDPOINT}?quoteCcy=USDT"
    #     )
    #
    #     for mark in mark_res.get('data', []):
    #         inst_id = mark['instId']
    #         ts = float(mark['ts']) / 1000.0
    #         self._mark_prices_cache[inst_id].append(ts, float(mark['markPx']))
    #
    #     for index in index_res.get('data', []):
    #         inst_id = index['instId'] + "-SWAP"
    #         ts = float(index['ts']) / 1000.0
    #         self._index_prices_cache[inst_id].append(ts, float(index['idxPx']))

    # @instance_cache(ttl=5)
    async def medal_fetch_funding_rates_internal(self) -> dict[str, FundingRate]:
        """获取所有交易对的资金费率"""
        funding_rates = {}

        instruments_dict = await self.__fetch_instruments()
        fundings_dict = await self.__fetch_fundings()
        index_prices_dict = await self.__fetch_index_prices()
        mark_prices_dict = await self.__fetch_mark_prices()

        for inst_id, instrument in instruments_dict.items():
            try:
                funding = fundings_dict.get(inst_id)
                index_price_data = index_prices_dict.get(inst_id)
                mark_price_data = mark_prices_dict.get(inst_id)

                if not funding or not index_price_data or not mark_price_data or instrument['state'] != 'live':
                    continue

                symbol = self.__to_ccxt_symbol_id(instrument)

                # 计算资金费率间隔
                funding_time = int(funding["fundingTime"]) / 1000.0
                next_funding_time = int(funding["nextFundingTime"]) / 1000.0
                funding_interval_hours = round((next_funding_time - funding_time) / 3600.0)

                # 获取价格（使用中间价）
                index_price = float(index_price_data['idxPx'])
                mark_price = float(mark_price_data['markPx'])
                # (float(ticker['askPx']) + float(ticker['bidPx'])) / 2.0  # do not use last price
                # 过期时间
                exp = instrument.get('expTime', '')
                if exp == '':
                    expiry = self.current_time + 100 * 365 * 24 * 3600  # 100 年后
                else:
                    expiry = float(exp) / 1000.0
                # interestRate
                funding_rate = FundingRate(
                    exchange=self.class_name,
                    symbol=symbol,
                    timestamp=float(funding['ts']) / 1000.0,
                    expiry=expiry,
                    base_funding_rate=float(funding['interestRate']),
                    next_funding_rate=float(funding['fundingRate']),
                    next_funding_timestamp=funding_time,  # intentionally using funding_time to match OKX's definition
                    funding_interval_hours=funding_interval_hours,
                    mark_price=mark_price,
                    mark_price_timestamp=float(mark_price_data['ts']) / 1000.0,
                    index_price=index_price,
                    index_price_timestamp=float(index_price_data['ts']) / 1000.0,
                    minimum_funding_rate=float(funding.get('minFundingRate', -0.015)),
                    maximum_funding_rate=float(funding.get('maxFundingRate', 0.015)),
                )
                funding_rates[symbol] = funding_rate

            except (ValueError, KeyError) as e:
                self.logger.warning("Error processing %s: %s", inst_id, e)

        return funding_rates

    @instance_cache(ttl=30)
    async def medal_fetch_funding_rates_history(self) -> list[FundingRateBill]:
        """获取资金费率账单"""
        bills = []
        try:
            instruments_dict = await self.__fetch_instruments()
            result = await self.exchanges["swap"].privateGetAccountBills({
                'instType': 'SWAP',
                'limit': 100
            })
            for raw in result.get('data', []):
                # subType 173/174 是资金费率
                if (raw.get('instType') == 'SWAP' and
                    int(raw.get('subType', 0)) in (173, 174) and
                    raw.get('ccy') in ('USDT', 'USDC', 'USD')):
                    inst_id = raw['instId']
                    inst = instruments_dict.get(inst_id, None)
                    if inst is None:
                        continue
                    symbol = self.__to_ccxt_symbol_id(inst)
                    # parts = inst_id.split("-")
                    # if len(parts) >= 2:
                    #     base, quote = parts[0], parts[1]
                    # symbol = f"{base}/{quote}:{quote}"
                    bill = FundingRateBill(
                        id=raw['billId'],
                        symbol=symbol,
                        funding_time=float(raw['ts']) / 1000.0,
                        funding_amount=float(raw['pnl']),
                    )
                    bills.append(bill)
        except Exception as e:
            self.logger.warning("Failed to fetch funding history: %s", e)
        return bills

    async def on_health_check(self):
        """健康检查"""
        await self.load_time_diff()
        await self.load_markets()
        result = await self.config.ccxt_instance.fetch(f"{self.REST_URL}{self.TIME_ENDPOINT}")
        return int(result.get('code', -1)) == 0

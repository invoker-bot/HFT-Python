"""
OKX 交易所实现
"""
import time
import logging
from typing import ClassVar, Optional
from cachetools import TTLCache
from cachetools_async import cached
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

    # API 端点
    REST_URL = "https://www.okx.com"
    FUNDING_ENDPOINT = "/api/v5/public/funding-rate"
    INSTRUMENTS_ENDPOINT = "/api/v5/public/instruments"
    TIME_ENDPOINT = "/api/v5/public/time"
    TICKER_ENDPOINT = "/api/v5/market/tickers"
    INDEX_PRICE_ENDPOINT = "/api/v5/market/index-tickers"
    MARK_PRICE_ENDPOINT = "/api/v5/public/mark-price"

    async def set_leverage(self, symbol: str, leverage: int, params: Optional[dict] = None) -> dict:
        """设置杠杆（OKX 需要 mgnMode 参数）"""
        margin_mode = params.get('mgnMode', 'cross') if params else 'cross'
        return await self.exchange.set_leverage(leverage, symbol, {
            **(params or {}),
            'mgnMode': margin_mode
        })

    async def set_margin_mode(self, symbol: str, margin_mode: str) -> dict:
        """设置保证金模式"""
        return await self.exchange.set_margin_mode(margin_mode, symbol, {
            "posSide": "net",
        })

    async def initialize_symbol(self, symbol: str, leverage: Optional[int] = None) -> None:
        """初始化交易对"""
        if self._swaps is None:
            await self.load_swaps()

        swap = self._swaps.get(symbol)
        if swap is None:
            return

        max_leverage = swap['limits']['leverage']['max'] or 125
        target_leverage = min(leverage or self.config.leverage or 10, max_leverage)

        try:
            await self.exchange.set_margin_mode('cross', symbol, {
                "leverage": target_leverage,
                "posSide": "net",
            })
            await self.exchange.set_leverage(target_leverage, symbol)
            logger.info(f"[{self.class_name}] Initialized {symbol} with {target_leverage}x leverage")
        except Exception as e:
            logger.warning(f"[{self.class_name}] Failed to initialize {symbol}: {e}")

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

    @cached(TTLCache(maxsize=32, ttl=30))
    async def _fetch_instruments(self) -> dict[str, dict]:
        """获取所有永续合约"""
        result = await self.exchange.fetch(
            f"{self.REST_URL}{self.INSTRUMENTS_ENDPOINT}?instType=SWAP"
        )
        return self._parse_response(result)

    @cached(TTLCache(maxsize=32, ttl=30))
    async def _fetch_fundings(self) -> dict[str, dict]:
        """获取资金费率信息"""
        result = await self.exchange.fetch(
            f"{self.REST_URL}{self.FUNDING_ENDPOINT}?instType=SWAP&instId=ANY"
        )
        return self._parse_response(result)

    @cached(TTLCache(maxsize=32, ttl=30))
    async def _fetch_tickers(self) -> dict[str, dict]:
        """获取所有 ticker"""
        result = await self.exchange.fetch(
            f"{self.REST_URL}{self.TICKER_ENDPOINT}?instType=SWAP"
        )
        return self._parse_response(result)

    async def _update_price_cache(self) -> None:
        """更新标记价格和指数价格缓存"""
        mark_res = await self.exchange.fetch(
            f"{self.REST_URL}{self.MARK_PRICE_ENDPOINT}?instType=SWAP"
        )
        index_res = await self.exchange.fetch(
            f"{self.REST_URL}{self.INDEX_PRICE_ENDPOINT}?quoteCcy=USDT"
        )

        for mark in mark_res.get('data', []):
            inst_id = mark['instId']
            ts = float(mark['ts']) / 1000.0
            self._mark_prices_cache[inst_id].append(ts, float(mark['markPx']))

        for index in index_res.get('data', []):
            inst_id = index['instId'] + "-SWAP"
            ts = float(index['ts']) / 1000.0
            self._index_prices_cache[inst_id].append(ts, float(index['idxPx']))

    @cached(TTLCache(maxsize=32, ttl=5))
    async def fetch_funding_rates(self) -> dict[str, FundingRate]:
        """获取所有交易对的资金费率"""
        funding_rates = {}

        await self._update_price_cache()
        instruments_dict = await self._fetch_instruments()
        fundings_dict = await self._fetch_fundings()
        tickers_dict = await self._fetch_tickers()

        # 更新资金费率缓存
        for inst_id, funding in fundings_dict.items():
            ts = float(funding['ts']) / 1000.0
            rate = float(funding['fundingRate'])
            self._funding_rates_cache[inst_id].append(ts, rate)

        for inst_id, instrument in instruments_dict.items():
            try:
                funding = fundings_dict.get(inst_id)
                ticker = tickers_dict.get(inst_id)

                if not funding or not ticker:
                    continue

                # 解析交易对
                parts = inst_id.split("-")
                if len(parts) < 2:
                    continue
                base, quote = parts[0], parts[1]
                symbol = f"{base}/{quote}:{quote}"

                # 计算资金费率间隔
                funding_time = int(funding["fundingTime"]) / 1000.0
                next_funding_time = int(funding["nextFundingTime"]) / 1000.0
                funding_interval_hours = round((next_funding_time - funding_time) / 3600.0)

                # 计算指数价格（使用中间价）
                index_price = (float(ticker['askPx']) + float(ticker['bidPx'])) / 2.0

                # 过期时间
                exp = instrument.get('expTime', '')
                if exp == '':
                    exp_timestamp = time.time() + 100 * 365 * 24 * 3600  # 100 年后
                else:
                    exp_timestamp = float(exp) / 1000.0

                funding_rate = FundingRate(
                    exchange=self.class_name,
                    symbol=symbol,
                    funding_rate=float(funding['fundingRate']),
                    next_funding_rate=float(funding.get('nextFundingRate', funding['fundingRate'])),
                    funding_timestamp=next_funding_time,
                    funding_interval_hours=max(1, funding_interval_hours),
                    mark_price=float(ticker.get('last', index_price)),
                    index_price=index_price,
                    min_funding_rate=float(funding.get('minFundingRate', -0.03)),
                    max_funding_rate=float(funding.get('maxFundingRate', 0.03)),
                )
                funding_rates[symbol] = funding_rate

            except (ValueError, KeyError) as e:
                logger.warning(f"[{self.class_name}] Error processing {inst_id}: {e}")

        return funding_rates

    async def fetch_funding_rates_history(self) -> list[FundingRateBill]:
        """获取资金费率账单"""
        bills = []
        try:
            result = await self.exchange.privateGetAccountBills({
                'instType': 'SWAP',
                'limit': 100
            })
            for raw in result.get('data', []):
                # subType 173/174 是资金费率
                if (raw.get('instType') == 'SWAP' and
                    int(raw.get('subType', 0)) in (173, 174) and
                    raw.get('ccy') == 'USDT'):

                    inst_id = raw['instId']
                    parts = inst_id.split("-")
                    if len(parts) >= 2:
                        base, quote = parts[0], parts[1]
                        symbol = f"{base}/{quote}:{quote}"
                        bill = FundingRateBill(
                            id=raw['billId'],
                            symbol=symbol,
                            funding_time=float(raw['ts']) / 1000.0,
                            funding_amount=float(raw['pnl']),
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
            result = await self.exchange.fetch(f"{self.REST_URL}{self.TIME_ENDPOINT}")
            self._health = int(result.get('code', -1)) == 0
        except Exception:
            self._health = False

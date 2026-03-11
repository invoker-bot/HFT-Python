"""FairPriceIndicator - 公允价格指标

返回接近 1 的值，表示交易对价格相对于组内公允价格的偏离：
- > 1: 做空有利（价格偏高）
- < 1: 做多有利（价格偏低）

现货: fair_price = mid_price / 组内 volume 加权现货均价
合约: fair_price = 1 + (actual_spread - expected_spread × time_to_next)
  其中 actual_spread = (index_price - mark_price) / mark_price
  expected_spread 由 FairFundingRateIndicator 根据组内平均无偏资费率预测
  time_to_next = 距下次结算的时间比例 (0~1)，用于修正不同平台结算时间差异
"""
import time
import logging
from typing import Any, Optional

from ..base import BaseIndicator
from ...core.scope.base import FlowScopeNode
from ...core.scope.scopes import ExchangeClassScope, TradingPairClassScope

logger = logging.getLogger(__name__)


class FairPriceIndicator(BaseIndicator):
    """
    公允价格指标（TradingPairClassScope 级别）

    通过跨平台聚合组内交易对数据，计算每个交易对的公允价格偏离度。
    """
    supported_scope = TradingPairClassScope

    def initialize(self, **kwargs):
        super().initialize(**kwargs)
        self._trade_group = kwargs.get("trade_group", {})
        self._ticker_id = kwargs.get("ticker_id", "ticker")
        self._ticker_volume_id = kwargs.get("ticker_volume_id", "ticker_volume")
        self._funding_rate_id = kwargs.get("funding_rate_id", "funding_rate")
        self._funding_rate_meta_id = kwargs.get("funding_rate_meta_id", "funding_rate_meta")
        self._index_price_id = kwargs.get("index_price_id", "index_price")
        self._mark_price_id = kwargs.get("mark_price_id", "mark_price")
        self._fair_funding_rate_id = kwargs.get("fair_funding_rate_id", "fair_funding_rate")

    def _get_group_id(self, symbol: str) -> str:
        """获取交易对的组 ID"""
        cfg = self._trade_group.get(symbol)
        if cfg and isinstance(cfg, dict) and "to" in cfg:
            return cfg["to"]
        return symbol.split('/')[0] if '/' in symbol else symbol

    def _is_spot(self, exchange, symbol: str) -> bool:
        """判断交易对是否为现货"""
        markets = exchange.markets.get_data()
        if markets is not None:
            market = markets.get(symbol)
            if market is not None:
                return market.get("type") == "spot"
        return ':' not in symbol

    def _get_scope_node(self, exchange_class: str, symbol: str) -> Optional[FlowScopeNode]:
        """为指定的 (exchange_class, symbol) 创建临时 FlowScopeNode"""
        scope_manager = self.root.scope_manager
        scope = scope_manager.get_or_create(
            "TradingPairClassScope",
            (exchange_class, symbol),
        )
        return FlowScopeNode(scope, prev=[])

    def _get_exchange_class_scope_node(self, exchange_class: str) -> Optional[FlowScopeNode]:
        """为指定的 exchange_class 创建临时 FlowScopeNode"""
        scope_manager = self.root.scope_manager
        scope = scope_manager.get_or_create(
            "ExchangeClassScope",
            (exchange_class,),
        )
        return FlowScopeNode(scope, prev=[])

    def _query_indicator_safe(self, indicator_id: str, scope_node: FlowScopeNode):
        """安全查询 indicator，不 ready 返回 None"""
        try:
            ind = self.root.query_indicator(indicator_id, scope_node)
            if ind is not None and ind.ready:
                return ind
        except (KeyError, ValueError):
            pass
        return None

    def _collect_group_data(self, group_id: str):
        """
        收集组内所有交易对的数据

        Returns:
            (spot_data, futures_data):
            spot_data: list of (mid_price, volume)
            futures_data: list of (daily_excess_fr, volume, exchange_class)
        """
        exchange_group = self.root.exchange_group
        spot_data = []
        futures_data = []

        for exchange_class, exchange_paths in exchange_group.exchange_group.items():
            for exchange_path in exchange_paths:
                exchange = exchange_group.exchange_instances[exchange_path]
                if not exchange.ready:
                    continue
                markets = exchange.markets.get_data()
                if markets is None:
                    continue

                for symbol in markets.keys():
                    if self._get_group_id(symbol) != group_id:
                        continue

                    scope_node = self._get_scope_node(exchange_class, symbol)
                    if scope_node is None:
                        continue

                    # 获取 ticker volume（可选，不 ready 时使用默认权重）
                    vol_ind = self._query_indicator_safe(self._ticker_volume_id, scope_node)
                    if vol_ind is not None:
                        vol_vars = vol_ind.get_vars()
                        volume = vol_vars.get("ticker_volume", 0)
                        if not volume or volume <= 0:
                            volume = 1.0  # fallback: 等权重
                    else:
                        volume = 1.0  # fallback: 等权重

                    is_spot = self._is_spot(exchange, symbol)

                    if is_spot:
                        # 获取 mid_price
                        ticker_ind = self._query_indicator_safe(self._ticker_id, scope_node)
                        if ticker_ind is None:
                            continue
                        ticker_vars = ticker_ind.get_vars()
                        mid_price = ticker_vars.get("mid_price")
                        if mid_price and mid_price > 0:
                            spot_data.append((mid_price, volume))
                    else:
                        # 获取 funding_rate 和 meta
                        fr_ind = self._query_indicator_safe(self._funding_rate_id, scope_node)
                        meta_ind = self._query_indicator_safe(self._funding_rate_meta_id, scope_node)
                        if fr_ind is None or meta_ind is None:
                            continue
                        fr_vars = fr_ind.get_vars()
                        meta_vars = meta_ind.get_vars()
                        funding_rate = fr_vars.get("funding_rate")
                        base_fr = meta_vars.get("funding_rate_base", 0)
                        interval_hours = meta_vars.get("funding_rate_meta")
                        if interval_hours is not None:
                            interval_hours = interval_hours.funding_interval_hours
                        if funding_rate is not None and interval_hours and interval_hours > 0:
                            daily_excess_fr = (funding_rate - base_fr) * 24 / interval_hours
                            futures_data.append((daily_excess_fr, volume, exchange_class))

        return spot_data, futures_data

    def get_vars(self) -> dict[str, Any]:
        """计算并返回 fair_price"""
        # 获取当前交易对信息
        exchange_class = self.scope.scope.get_var("exchange_class")
        symbol = self.scope.scope.get_var("symbol")
        if not exchange_class or not symbol:
            return {"fair_price": None}

        group_id = self._get_group_id(symbol)

        # 获取当前交易对的 exchange 实例来判断 spot/futures
        exchange_group = self.root.exchange_group
        exchange_paths = exchange_group.exchange_group.get(exchange_class, set())
        if not exchange_paths:
            return {"fair_price": None}
        exchange = exchange_group.exchange_instances[next(iter(exchange_paths))]
        is_spot = self._is_spot(exchange, symbol)

        # 收集组内数据
        spot_data, futures_data = self._collect_group_data(group_id)

        if is_spot:
            return self._calc_spot_fair_price(symbol, spot_data)
        else:
            return self._calc_futures_fair_price(
                exchange_class, symbol, spot_data, futures_data
            )

    def _calc_spot_fair_price(
        self, symbol: str, spot_data: list[tuple]
    ) -> dict[str, Any]:
        """计算现货 fair_price = mid_price / volume 加权现货均价"""
        if not spot_data:
            return {"fair_price": None}

        # volume 加权现货均价
        total_volume = sum(v for _, v in spot_data)
        if total_volume <= 0:
            return {"fair_price": None}
        weighted_avg_price = sum(p * v for p, v in spot_data) / total_volume

        # 获取当前交易对的 mid_price
        ticker_ind = self._query_indicator_safe(self._ticker_id, self.scope)
        if ticker_ind is None:
            return {"fair_price": None}
        ticker_vars = ticker_ind.get_vars()
        mid_price = ticker_vars.get("mid_price")
        if not mid_price or mid_price <= 0 or weighted_avg_price <= 0:
            return {"fair_price": None}

        fair_price = mid_price / weighted_avg_price
        return {"fair_price": fair_price}

    def _calc_futures_fair_price(
        self,
        exchange_class: str,
        symbol: str,
        spot_data: list[tuple],
        futures_data: list[tuple],
    ) -> dict[str, Any]:
        """
        计算合约 fair_price = 1 + (actual_spread - expected_spread × time_to_next)

        actual_spread = (index_price - mark_price) / mark_price
        expected_spread = predict_fair_spread(avg_excess_fr)
        time_to_next = seconds_until_next / (interval_hours × 3600)

        time_to_next 修正的意义：
        - 刚结完算 (time_to_next ≈ 1)：下一期费率完整待收，价差应反映完整 expected_spread
        - 即将结算 (time_to_next ≈ 0)：费率马上结清，价差中不应有费率支撑
        - 自然处理跨平台结算时间差异：各自独立修正，无需知道对方时间表
        """
        # 获取当前交易对的 index_price 和 mark_price
        index_ind = self._query_indicator_safe(self._index_price_id, self.scope)
        mark_ind = self._query_indicator_safe(self._mark_price_id, self.scope)
        if index_ind is None or mark_ind is None:
            return {"fair_price": None}

        index_vars = index_ind.get_vars()
        mark_vars = mark_ind.get_vars()
        index_price = index_vars.get("index_price")
        mark_price = mark_vars.get("mark_price")

        if not index_price or not mark_price or mark_price <= 0:
            return {"fair_price": None}

        actual_spread = (index_price - mark_price) / mark_price

        # 获取当前交易对的结算时间信息
        meta_ind = self._query_indicator_safe(self._funding_rate_meta_id, self.scope)
        time_to_next = 0.5  # 默认值：结算周期中点
        if meta_ind is not None:
            meta_vars = meta_ind.get_vars()
            meta = meta_vars.get("funding_rate_meta")
            if meta is not None:
                interval_seconds = meta.funding_interval_hours * 3600
                if interval_seconds > 0:
                    seconds_until = max(meta.next_funding_timestamp - time.time(), 0.0)
                    time_to_next = min(seconds_until / interval_seconds, 1.0)

        # volume 加权平均无偏资费率
        if not futures_data:
            return {"fair_price": 1.0 + actual_spread}

        total_volume = sum(v for _, v, _ in futures_data)
        if total_volume <= 0:
            return {"fair_price": 1.0 + actual_spread}

        avg_excess_fr = sum(fr * v for fr, v, _ in futures_data) / total_volume

        # 通过 FairFundingRateIndicator 预测期望 spread
        ec_scope_node = self._get_exchange_class_scope_node(exchange_class)
        if ec_scope_node is None:
            return {"fair_price": 1.0 + actual_spread}

        ffr_ind = self._query_indicator_safe(self._fair_funding_rate_id, ec_scope_node)
        if ffr_ind is None or not hasattr(ffr_ind, 'predict_fair_spread'):
            return {"fair_price": 1.0 + actual_spread}

        try:
            expected_spread = ffr_ind.predict_fair_spread(avg_excess_fr)
        except (ValueError, Exception):
            return {"fair_price": 1.0 + actual_spread}

        # 应用时间修正：expected_spread × time_to_next
        fair_price = 1.0 + (actual_spread - expected_spread * time_to_next)
        return {"fair_price": fair_price}

    async def on_tick(self):
        pass

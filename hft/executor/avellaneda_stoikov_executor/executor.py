"""
AvellanedaStoikovExecutor - Avellaneda-Stoikov 最优做市执行器

基于 Avellaneda-Stoikov 论文 "High-frequency trading in a limit order book" (2008)

核心公式：
1. 保留价格: r(s, q, t) = s - q * γ * σ² * (T - t)
2. 最优价差: δ(t) = γ * σ² * (T - t) + (2/γ) * ln(1 + γ/k)
3. 最优报价: bid = r - δ/2, ask = r + δ/2
"""
import math
import time
import numpy as np
from typing import TYPE_CHECKING, Optional

from ..base import BaseExecutor, ExecutionResult, OrderIntent
from ..intensity import TradeIntensityCalculator, IntensityResult

if TYPE_CHECKING:
    from ...datasource.group import DataSourceGroup
    from ...exchange.base import BaseExchange
    from .config import AvellanedaStoikovExecutorConfig


def calculate_weighted_mid_deviation(
    order_book: dict,
    levels: int = 10,
    decay: float = 0.9,
) -> float:
    """
    计算加权中间价偏离度

    Returns:
        偏离度 [-1, 1]，>0 买方强，<0 卖方强
    """
    bids = order_book.get('bids', [])
    asks = order_book.get('asks', [])

    if not bids or not asks:
        return 0.0

    n_bids = min(levels, len(bids))
    n_asks = min(levels, len(asks))

    if n_bids == 0 or n_asks == 0:
        return 0.0

    weights_bid = [decay ** i for i in range(n_bids)]
    weights_ask = [decay ** i for i in range(n_asks)]

    total_bid_vol = sum(bids[i][1] * weights_bid[i] for i in range(n_bids))
    total_ask_vol = sum(asks[i][1] * weights_ask[i] for i in range(n_asks))

    if total_bid_vol == 0 or total_ask_vol == 0:
        return 0.0

    bid_vwap = sum(bids[i][0] * bids[i][1] * weights_bid[i] for i in range(n_bids)) / total_bid_vol
    ask_vwap = sum(asks[i][0] * asks[i][1] * weights_ask[i] for i in range(n_asks)) / total_ask_vol

    mid = (bids[0][0] + asks[0][0]) / 2
    weighted_mid = (bid_vwap * total_ask_vol + ask_vwap * total_bid_vol) / (total_bid_vol + total_ask_vol)

    spread = asks[0][0] - bids[0][0]
    if spread <= 0:
        return 0.0

    deviation = (weighted_mid - mid) / spread
    return float(np.clip(deviation, -1, 1))


class AvellanedaStoikovExecutor(BaseExecutor):
    """
    Avellaneda-Stoikov 最优做市执行器

    特点：
    - 从成交数据动态估计 k 和 sigma
    - 根据库存水平动态调整报价
    - 订单生命周期由基类管理
    """

    config: "AvellanedaStoikovExecutorConfig"

    def __init__(self, config: "AvellanedaStoikovExecutorConfig"):
        super().__init__(config)
        self._period_start: float = time.time()
        self._calculators: dict[tuple[str, str], TradeIntensityCalculator] = {}

    @property
    def per_order_usd(self) -> float:
        if self.config.orders:
            return self.config.orders[0].per_order_usd
        return 100.0

    @property
    def datasource_group(self) -> "DataSourceGroup":
        return self.root.datasource_group

    def _get_calculator(self, exchange_class: str, symbol: str) -> TradeIntensityCalculator:
        key = (exchange_class, symbol)
        if key not in self._calculators:
            self._calculators[key] = TradeIntensityCalculator(
                sub_range_seconds=self.config.intensity_sub_range,
                total_range_seconds=self.config.intensity_total_range,
                precision=self.config.intensity_precision,
                precision_std_range=self.config.intensity_std_range,
                min_correlation=self.config.intensity_min_correlation,
                min_trades=self.config.intensity_min_trades,
            )
        return self._calculators[key]

    def _get_remaining_time(self) -> float:
        elapsed = time.time() - self._period_start
        remaining = self.config.T - (elapsed % self.config.T)
        return max(remaining, 1.0)

    def _get_k_and_sigma(
        self,
        intensity_result: Optional[IntensityResult],
        side: str,
    ) -> tuple[float, float]:
        if intensity_result is None or not intensity_result.is_valid:
            return self.config.k_fallback, self.config.sigma_fallback

        if side == "buy":
            k = intensity_result.buy_k if intensity_result.buy_k > 0 else self.config.k_fallback
        else:
            k = intensity_result.sell_k if intensity_result.sell_k > 0 else self.config.k_fallback

        sigma = intensity_result.average_std if intensity_result.average_std > 0 else self.config.sigma_fallback
        return k, sigma

    def _calculate_reservation_price(
        self,
        mid_price: float,
        inventory_usd: float,
        sigma: float,
        gamma: float,
        per_order_usd: float,
    ) -> float:
        """计算保留价格"""
        remaining_time = self._get_remaining_time()
        inventory = inventory_usd / per_order_usd
        offset = inventory * gamma * (sigma ** 2) * remaining_time
        return mid_price * (1 - offset)

    def _calculate_spread(
        self,
        k: float,
        sigma: float,
        side: str,
        inventory: float,
        gamma: float,
    ) -> float:
        """计算单边价差"""
        if side == "buy":
            inv_adj = 0.5 * (1 - inventory) * gamma * sigma
        else:
            inv_adj = 0.5 * (1 + inventory) * gamma * sigma

        if k > 0 and gamma > 0:
            arrival_adj = sigma * (1 / gamma) * math.log1p(gamma / max(k * sigma, 1e-9))
        else:
            arrival_adj = sigma

        spread = inv_adj + arrival_adj
        return max(self.config.min_spread / 2, min(spread, self.config.max_spread / 2))

    async def execute_delta(
        self,
        exchange: "BaseExchange",
        symbol: str,
        delta_usd: float,
        speed: float,
        current_price: float,
    ) -> ExecutionResult:
        """执行 AS 做市策略"""
        try:
            await exchange.medal_initialize_symbol(symbol)

            # 1. 获取成交数据，更新强度计算器
            from ...datasource.group import DataType
            trades_ds = self.datasource_group.query_single(
                DataType.TRADES, exchange.class_name, symbol
            )

            intensity_result = None
            if trades_ds:
                trades = trades_ds.get_all()
                calculator = self._get_calculator(exchange.class_name, symbol)
                intensity_result = calculator.update(trades)

            # 2. 获取订单簿
            orderbook_ds = self.datasource_group.query_single(
                DataType.ORDER_BOOK, exchange.class_name, symbol
            )
            order_book = None
            mid_deviation = 0.0
            if orderbook_ds:
                order_book = orderbook_ds.get_latest()
                if order_book:
                    mid_deviation = calculate_weighted_mid_deviation(
                        order_book,
                        levels=self.config.mid_levels,
                        decay=self.config.mid_decay,
                    )

            # 3. 获取库存
            positions = await exchange.medal_fetch_positions()
            inventory_amount = positions.get(symbol, 0.0)
            inventory_usd = inventory_amount * current_price

            # 4. 获取 k 和 sigma
            buy_k, buy_sigma = self._get_k_and_sigma(intensity_result, "buy")
            sell_k, sell_sigma = self._get_k_and_sigma(intensity_result, "sell")
            sigma = (buy_sigma + sell_sigma) / 2

            # 5. 调整中间价
            mid_price = current_price
            if order_book and order_book.get('bids') and order_book.get('asks'):
                spread = order_book['asks'][0][0] - order_book['bids'][0][0]
                mid_adjustment = mid_deviation * spread * self.config.mid_adjustment_factor
                mid_price = current_price + mid_adjustment

            # 6. 计算订单意图
            intents = self._calculate_intents(
                exchange, symbol, mid_price, inventory_usd,
                sigma, buy_k, buy_sigma, sell_k, sell_sigma
            )

            # 7. 调用基类管理订单
            created, cancelled, reused = await self.manage_limit_orders(
                exchange, symbol, intents, mid_price
            )

            return ExecutionResult(
                exchange_class=exchange.class_name,
                symbol=symbol,
                success=True,
                exchange_name=exchange.name,
                delta_usd=delta_usd,
            )

        except Exception as e:
            self.logger.exception("[%s] Error: %s", exchange.name, e)
            return ExecutionResult(
                exchange_class=exchange.class_name,
                symbol=symbol,
                success=False,
                exchange_name=exchange.name,
                delta_usd=delta_usd,
                error=str(e),
            )

    def _calculate_intents(
        self,
        exchange: "BaseExchange",
        symbol: str,
        mid_price: float,
        inventory_usd: float,
        sigma: float,
        buy_k: float,
        buy_sigma: float,
        sell_k: float,
        sell_sigma: float,
    ) -> list[OrderIntent]:
        """计算 AS 订单意图"""
        intents = []

        for idx, order_config in enumerate(self.config.orders):
            gamma = order_config.gamma
            side = "sell" if order_config.reverse else "buy"
            inventory_normalized = inventory_usd / order_config.per_order_usd

            # 保留价格
            reservation_price = self._calculate_reservation_price(
                mid_price, inventory_usd, sigma, gamma, order_config.per_order_usd
            )

            # 计算报价
            if side == "buy":
                if inventory_usd >= self.config.max_inventory:
                    continue  # 库存已满，跳过买单
                k, side_sigma = buy_k, buy_sigma
                order_spread = self._calculate_spread(k, side_sigma, side, inventory_normalized, gamma)
                target_price = reservation_price * (1 - order_spread)
            else:
                if inventory_usd <= -self.config.max_inventory:
                    continue  # 库存已满，跳过卖单
                k, side_sigma = sell_k, sell_sigma
                order_spread = self._calculate_spread(k, side_sigma, side, inventory_normalized, gamma)
                target_price = reservation_price * (1 + order_spread)

            amount = abs(self.usd_to_amount(
                exchange, symbol, order_config.per_order_usd, mid_price
            ))

            intents.append(OrderIntent(
                side=side,
                level=idx,
                price=target_price,
                amount=amount,
                timeout=order_config.timeout,
                refresh_tolerance=order_config.refresh_tolerance,
            ))

        return intents

    @property
    def log_state_dict(self) -> dict:
        calc_status = "no_data"
        for calc in self._calculators.values():
            if calc.is_ready:
                calc_status = "ready"
                break
            elif calc.result is not None:
                calc_status = "collecting"

        return {
            **super().log_state_dict,
            "remaining_time": f"{self._get_remaining_time():.1f}s",
            "intensity": calc_status,
        }

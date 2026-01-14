"""
套利策略主模块

ArbitrageStrategy 实现跨交易所套利：
1. 收集所有交易所的交易对
2. 生成所有可能的套利对组合 A(n, 2)
3. 计算每个套利对的评分
4. 根据阈值决定入场/退出
5. 生成目标仓位
"""
from typing import Optional, TYPE_CHECKING
from collections import defaultdict
from rich.console import Console
from rich.table import Table
from ..base import BaseStrategy, TargetPositions
from .config import ArbitrageConfig
from .types import (
    ArbitrageType,
    TradingPair,
    ArbitragePair,
    PairState,
)

if TYPE_CHECKING:
    from ...exchange.base import BaseExchange


class ArbitrageStrategy(BaseStrategy):
    """
    跨交易所套利策略

    工作流程：
    1. on_tick(): 刷新市场数据，更新评分，管理持仓状态
    2. get_target_positions_usd(): 根据活跃套利对生成目标仓位

    持仓管理（滞后控制）：
    - score > entry_threshold: 入场
    - score < exit_threshold: 退出
    - 介于两者之间: 保持不变
    """

    config: ArbitrageConfig

    def __init__(self, config: ArbitrageConfig):
        super().__init__(config)
        # 活跃的套利对状态
        self._active_pairs: dict[str, PairState] = {}
        # 缓存的交易对列表
        self._trading_pairs: list[TradingPair] = []
        # 缓存的套利对列表
        self._arbitrage_pairs: list[ArbitragePair] = []

    @property
    def exchange_group(self):
        """获取交易所组"""
        return self.root.exchange_group

    def _get_exchange(self, exchange_path: str) -> Optional["BaseExchange"]:
        """根据配置路径获取交易所实例"""
        for exchange in self.exchange_group.children.values():
            if exchange.config.path == exchange_path:
                return exchange
        return None

    # ========== 交易对收集 ==========

    def _collect_trading_pairs(self) -> list[TradingPair]:
        """
        收集所有交易所的交易对

        Returns:
            所有符合条件的交易对列表
        """
        pairs: list[TradingPair] = []

        for exchange_path in self.config.exchanges:
            exchange = self._get_exchange(exchange_path)
            if exchange is None or not exchange.ready:
                continue

            for symbol, info in exchange.market_trading_pairs.items():
                # 过滤计价币种
                if info.quote != self.config.quote_currency:
                    continue

                # 过滤基础币种
                if self.config.base_currencies and info.base not in self.config.base_currencies:
                    continue

                trade_type = info.trade_type.value  # "spot" or "swap"

                pairs.append(TradingPair(
                    exchange_path=exchange_path,
                    symbol=symbol,
                    trade_type=trade_type,
                    base=info.base,
                    quote=info.quote,
                ))

        return pairs

    # ========== 套利对生成 ==========

    def _generate_arbitrage_pairs(self, trading_pairs: list[TradingPair]) -> list[ArbitragePair]:
        """
        生成所有可能的套利对组合 A(n, 2)

        只对相同 base 币种的交易对进行配对。

        Args:
            trading_pairs: 所有交易对列表

        Returns:
            所有有效的套利对列表
        """
        # 按 base 币种分组
        by_base: dict[str, list[TradingPair]] = defaultdict(list)
        for tp in trading_pairs:
            by_base[tp.base].append(tp)

        pairs: list[ArbitragePair] = []

        for base, tps in by_base.items():
            if len(tps) < 2:
                continue

            # 分类
            swaps = [tp for tp in tps if tp.trade_type == "swap"]
            spots = [tp for tp in tps if tp.trade_type == "spot"]

            # Swap vs Swap（跨交易所合约套利）
            if self.config.enable_swap_swap:
                for i, tp1 in enumerate(swaps):
                    for tp2 in swaps[i + 1:]:
                        # 必须是不同交易所
                        if tp1.exchange_path != tp2.exchange_path:
                            pairs.append(ArbitragePair(
                                leg1=tp1,
                                leg2=tp2,
                                arb_type=ArbitrageType.SWAP_SWAP,
                            ))

            # Spot vs Swap（现货-合约套利）
            if self.config.enable_spot_swap:
                for spot in spots:
                    for swap in swaps:
                        # 可以是同交易所或跨交易所
                        pairs.append(ArbitragePair(
                            leg1=spot,
                            leg2=swap,
                            arb_type=ArbitrageType.SPOT_SWAP,
                        ))

            # Spot vs Spot（现货搬运套利）
            if self.config.enable_spot_spot:
                for i, tp1 in enumerate(spots):
                    for tp2 in spots[i + 1:]:
                        # 必须是不同交易所
                        if tp1.exchange_path != tp2.exchange_path:
                            pairs.append(ArbitragePair(
                                leg1=tp1,
                                leg2=tp2,
                                arb_type=ArbitrageType.SPOT_SPOT,
                            ))

        return pairs

    # ========== 市场数据获取 ==========

    async def _fetch_market_data(self, pairs: list[ArbitragePair]) -> None:
        """
        获取所有套利对的市场数据（价格、资金费率）

        Args:
            pairs: 套利对列表
        """
        # 收集需要获取数据的交易对
        trading_pairs_to_fetch: dict[str, TradingPair] = {}
        for pair in pairs:
            trading_pairs_to_fetch[pair.leg1.id] = pair.leg1
            trading_pairs_to_fetch[pair.leg2.id] = pair.leg2

        # 按交易所分组获取数据
        by_exchange: dict[str, list[TradingPair]] = defaultdict(list)
        for tp in trading_pairs_to_fetch.values():
            by_exchange[tp.exchange_path].append(tp)

        for exchange_path, tps in by_exchange.items():
            exchange = self._get_exchange(exchange_path)
            if exchange is None:
                continue

            for tp in tps:
                try:
                    # 获取价格
                    ticker = await exchange.fetch_ticker(tp.symbol)
                    tp.price = ticker.get("last", 0) or ticker.get("close", 0) or 0

                    # 获取资金费率（仅 swap）
                    if tp.trade_type == "swap":
                        funding_rate = exchange.get_funding_rate(tp.symbol)
                        if funding_rate:
                            tp.funding_rate = funding_rate.next_funding_rate
                            tp.funding_interval = funding_rate.funding_interval_hours
                            tp.next_funding_time = funding_rate.next_funding_timestamp

                except Exception as e:
                    self.logger.debug(
                        "Failed to fetch data for %s: %s", tp.id, e
                    )

    # ========== 评分计算 ==========

    def _score_pair(self, pair: ArbitragePair) -> float:
        """
        计算套利对的评分（预估年化收益率）

        Args:
            pair: 套利对

        Returns:
            预估年化收益率
        """
        if pair.leg1.price <= 0 or pair.leg2.price <= 0:
            return 0.0

        if pair.arb_type == ArbitrageType.SWAP_SWAP:
            return self._score_swap_swap(pair)

        elif pair.arb_type == ArbitrageType.SPOT_SWAP:
            return self._score_spot_swap(pair)

        elif pair.arb_type == ArbitrageType.SPOT_SPOT:
            return self._score_spot_spot(pair)

        return 0.0

    def _score_swap_swap(self, pair: ArbitragePair) -> float:
        """Swap vs Swap 评分"""
        f1 = pair.leg1.funding_rate or 0
        f2 = pair.leg2.funding_rate or 0
        funding_diff = abs(f1 - f2)

        # 年化
        interval = min(
            pair.leg1.funding_interval or 8,
            pair.leg2.funding_interval or 8,
            self.config.min_funding_interval
        )
        annual = funding_diff * (365 * 24 / interval)

        # 确定方向：做多低费率，做空高费率
        if f1 < f2:
            pair.direction = 1   # long leg1, short leg2
        else:
            pair.direction = -1  # short leg1, long leg2

        pair.estimated_annual_profit = annual
        return annual

    def _score_spot_swap(self, pair: ArbitragePair) -> float:
        """Spot vs Swap 评分"""
        # leg1 是 spot，leg2 是 swap
        funding_rate = pair.leg2.funding_rate or 0

        # 只有资金费率为正时才有利可图
        if funding_rate <= 0:
            pair.direction = 0
            pair.estimated_annual_profit = 0
            return 0.0

        # 年化
        interval = pair.leg2.funding_interval or self.config.min_funding_interval
        annual = funding_rate * (365 * 24 / interval)

        # 方向：做多 spot，做空 swap
        pair.direction = 1

        pair.estimated_annual_profit = annual
        return annual

    def _score_spot_spot(self, pair: ArbitragePair) -> float:
        """Spot vs Spot 评分"""
        price_diff = abs(pair.leg1.price - pair.leg2.price)
        avg_price = (pair.leg1.price + pair.leg2.price) / 2
        profit_rate = price_diff / avg_price

        # 扣除转账费
        estimated_fee_rate = 0.001
        net_profit = profit_rate - estimated_fee_rate

        if net_profit <= 0:
            pair.direction = 0
            pair.estimated_annual_profit = 0
            return 0.0

        # 方向：买便宜的，卖贵的
        if pair.leg1.price < pair.leg2.price:
            pair.direction = 1
        else:
            pair.direction = -1

        annual = net_profit * 365
        pair.estimated_annual_profit = annual
        return net_profit

    # ========== 状态管理 ==========

    def _update_active_pairs(self, scored_pairs: list[ArbitragePair]) -> None:
        """根据评分更新活跃套利对"""
        pair_map = {p.id: p for p in scored_pairs}

        # 检查现有持仓
        pairs_to_remove = []
        for pair_id, state in self._active_pairs.items():
            pair = pair_map.get(pair_id)
            if pair is None:
                pairs_to_remove.append(pair_id)
                self.logger.info("Exit (pair removed): %s", pair_id)
                continue

            state.pair.score = pair.score
            state.pair.direction = pair.direction

            if pair.score < self.config.exit_threshold:
                pairs_to_remove.append(pair_id)
                self.logger.info(
                    "Exit: %s, score=%.4f < threshold=%.4f",
                    pair_id, pair.score, self.config.exit_threshold
                )

        for pair_id in pairs_to_remove:
            del self._active_pairs[pair_id]

        # 检查新入场
        if len(self._active_pairs) < self.config.max_pairs:
            candidates = [
                p for p in scored_pairs
                if p.id not in self._active_pairs
                and p.score > self.config.entry_threshold
                and p.direction != 0
            ]
            candidates.sort(key=lambda x: x.score, reverse=True)

            for pair in candidates:
                if len(self._active_pairs) >= self.config.max_pairs:
                    break

                self._active_pairs[pair.id] = PairState(
                    pair=pair,
                    entry_score=pair.score,
                    entry_prices=(pair.leg1.price, pair.leg2.price),
                )
                self.logger.info(
                    "Entry: %s, score=%.4f, direction=%d",
                    pair.id, pair.score, pair.direction
                )

    # ========== 主循环 ==========

    async def on_tick(self) -> bool:
        """主循环"""
        self._trading_pairs = self._collect_trading_pairs()
        if len(self._trading_pairs) < 2:
            self.logger.debug("Not enough trading pairs: %d", len(self._trading_pairs))
            return False

        self._arbitrage_pairs = self._generate_arbitrage_pairs(self._trading_pairs)
        if not self._arbitrage_pairs:
            self.logger.debug("No arbitrage pairs generated")
            return False

        await self._fetch_market_data(self._arbitrage_pairs)

        for pair in self._arbitrage_pairs:
            pair.score = self._score_pair(pair)

        self._update_active_pairs(self._arbitrage_pairs)

        return False

    def get_target_positions_usd(self) -> TargetPositions:
        """根据活跃套利对生成目标仓位"""
        positions: dict[tuple[str, str], float] = defaultdict(float)

        for state in self._active_pairs.values():
            pair = state.pair
            usd = self.config.per_pair_usd

            if pair.direction == 0:
                continue

            leg1_key = pair.leg1.key
            if pair.direction == 1:
                positions[leg1_key] += usd
            else:
                positions[leg1_key] -= usd

            leg2_key = pair.leg2.key
            if pair.direction == 1:
                positions[leg2_key] -= usd
            else:
                positions[leg2_key] += usd

        return {k: (v, self.config.speed) for k, v in positions.items()}

    # ========== 状态输出 ==========

    def _format_funding_rate(self, rate: Optional[float]) -> str:
        """格式化资金费率"""
        if rate is None:
            return "[dim]--[/dim]"
        # 转换为百分比，保留4位小数
        pct = rate * 100
        if pct > 0:
            return f"[green]+{pct:.4f}%[/green]"
        elif pct < 0:
            return f"[red]{pct:.4f}%[/red]"
        return f"{pct:.4f}%"

    def _format_price(self, price: float) -> str:
        """格式化价格"""
        if price <= 0:
            return "[dim]--[/dim]"
        if price >= 1000:
            return f"{price:,.2f}"
        elif price >= 1:
            return f"{price:.4f}"
        else:
            return f"{price:.6f}"

    def _format_score(self, score: float, is_active: bool) -> str:
        """格式化评分（年化收益率）"""
        pct = score * 100
        if is_active:
            return f"[bold green]{pct:.2f}%[/bold green]"
        elif score > self.config.entry_threshold:
            return f"[green]{pct:.2f}%[/green]"
        elif score > self.config.exit_threshold:
            return f"[yellow]{pct:.2f}%[/yellow]"
        else:
            return f"[dim]{pct:.2f}%[/dim]"

    def _format_direction(self, direction: int, leg1_type: str, leg2_type: str) -> str:
        """格式化方向"""
        if direction == 0:
            return "[dim]--[/dim]"
        elif direction == 1:
            return f"[green]+{leg1_type}[/green]/[red]-{leg2_type}[/red]"
        else:
            return f"[red]-{leg1_type}[/red]/[green]+{leg2_type}[/green]"

    def _format_status(self, pair_id: str) -> str:
        """格式化状态"""
        if pair_id in self._active_pairs:
            state = self._active_pairs[pair_id]
            hours = state.hold_hours
            if hours < 1:
                return f"[bold green]ACTIVE[/bold green] ({hours*60:.0f}m)"
            else:
                return f"[bold green]ACTIVE[/bold green] ({hours:.1f}h)"
        return "[dim]--[/dim]"

    def _format_position(self, pair_id: str) -> str:
        """格式化仓位"""
        if pair_id in self._active_pairs:
            return f"${self.config.per_pair_usd:,.0f}"
        return "[dim]--[/dim]"

    def _get_exchange_short_name(self, exchange_path: str) -> str:
        """获取交易所简称"""
        # "okx/main" -> "OKX"
        parts = exchange_path.split("/")
        return parts[0].upper() if parts else exchange_path

    def _build_table(self) -> Table:
        """
        构建套利对排名表格

        Returns:
            Rich Table 对象
        """
        table = Table(
            title=f"Arbitrage Pairs (max={self.config.max_pairs})",
            show_header=True,
            header_style="bold cyan",
        )

        # 添加列
        table.add_column("#", style="dim", width=3)
        table.add_column("Base", width=5)
        table.add_column("Type", width=10)
        table.add_column("Leg1", width=12)
        table.add_column("F1", width=10, justify="right")
        table.add_column("Leg2", width=12)
        table.add_column("F2", width=10, justify="right")
        table.add_column("Score", width=8, justify="right")
        table.add_column("Dir", width=12)
        table.add_column("Status", width=14)
        table.add_column("Position", width=10, justify="right")

        # 排序套利对
        sorted_pairs = sorted(
            self._arbitrage_pairs,
            key=lambda x: x.score,
            reverse=True
        )

        # 只显示 top N（活跃的 + 候选的）
        shown_count = 0
        max_show = self.config.max_pairs * 2  # 显示两倍 max_pairs

        for rank, pair in enumerate(sorted_pairs, 1):
            if shown_count >= max_show:
                break

            # 跳过评分为 0 的
            if pair.score <= 0 and pair.id not in self._active_pairs:
                continue

            is_active = pair.id in self._active_pairs

            # 交易所简称
            ex1 = self._get_exchange_short_name(pair.leg1.exchange_path)
            ex2 = self._get_exchange_short_name(pair.leg2.exchange_path)

            # 类型简称
            type_map = {
                ArbitrageType.SWAP_SWAP: "Swap-Swap",
                ArbitrageType.SPOT_SWAP: "Spot-Swap",
                ArbitrageType.SPOT_SPOT: "Spot-Spot",
            }
            arb_type = type_map.get(pair.arb_type, str(pair.arb_type.value))

            # Leg 信息
            leg1_info = f"{ex1}:{pair.leg1.trade_type}"
            leg2_info = f"{ex2}:{pair.leg2.trade_type}"

            # 资金费率
            f1 = self._format_funding_rate(pair.leg1.funding_rate)
            f2 = self._format_funding_rate(pair.leg2.funding_rate)

            # 添加行
            table.add_row(
                str(rank),
                pair.base,
                arb_type,
                leg1_info,
                f1,
                leg2_info,
                f2,
                self._format_score(pair.score, is_active),
                self._format_direction(pair.direction, pair.leg1.trade_type, pair.leg2.trade_type),
                self._format_status(pair.id),
                self._format_position(pair.id),
            )

            shown_count += 1

        # 如果没有数据，添加提示行
        if shown_count == 0:
            table.add_row(
                "--", "--", "--", "--", "--", "--", "--", "--", "--", "--", "--"
            )

        return table

    @property
    def log_state_dict(self) -> dict:
        return {
            **super().log_state_dict,
            "trading_pairs": len(self._trading_pairs),
            "arbitrage_pairs": len(self._arbitrage_pairs),
            "active_pairs": len(self._active_pairs),
        }

    def log_state(self, console: Console, recursive: bool = True):
        """输出状态到控制台，包含套利对排名表格"""
        # 调用父类方法处理子节点
        super().log_state(console, recursive)

        # 输出表格
        if self._arbitrage_pairs:
            table = self._build_table()
            console.print(table)

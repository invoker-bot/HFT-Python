"""
简单策略实现

SimpleController: 根据目标仓位和当前仓位的差值控制执行器
SimpleStrategy: 整合 TradingPairTable, Controller, Executor 的简单策略
"""
import logging
from dataclasses import dataclass
from functools import cached_property
from typing import Optional, ClassVar, Type, TYPE_CHECKING
from pydantic import Field
from ..core.listener import Listener
from .pairs import TradingPairs, TradingPairsTable, MarketType
from .controller import InfiniteController
from .config import BaseStrategyConfig

if TYPE_CHECKING:
    from ..exchange.base import BaseExchange
    from ..executor.order_executor import BaseOrderExecutor

logger = logging.getLogger(__name__)


@dataclass
class PositionTarget:
    """仓位目标"""
    symbol: str
    target: float        # 目标仓位（正=多，负=空）
    current: float = 0.0 # 当前仓位

    @property
    def delta(self) -> float:
        """仓位差值"""
        return self.target - self.current


class SimpleController(InfiniteController):
    """
    简单 Controller

    根据配置的目标仓位和交易所的当前仓位，
    计算差值并传递给 Executor

    使用方式:
        controller = SimpleController(
            name="simple",
            exchange=exchange,
            targets={"BTC/USDT:USDT": 0.5, "ETH/USDT:USDT": 2.0}
        )
    """

    def __init__(
        self,
        name: str,
        exchange: "BaseExchange",
        targets: Optional[dict[str, float]] = None,
        executors: Optional[dict[str, "BaseOrderExecutor"]] = None,
        interval: float = 1.0,
    ):
        """
        Args:
            name: Controller 名称
            exchange: 交易所实例
            targets: 目标仓位字典 {symbol: target_amount}
            executors: 执行器字典 {symbol: executor}
            interval: 决策间隔（秒）
        """
        super().__init__(name=name, interval=interval)
        self._exchange = exchange
        self._targets: dict[str, float] = targets or {}
        self._executors: dict[str, "BaseOrderExecutor"] = executors or {}
        self._positions: dict[str, PositionTarget] = {}

    @property
    def exchange(self) -> "BaseExchange":
        return self._exchange

    @property
    def targets(self) -> dict[str, float]:
        return self._targets

    def set_target(self, symbol: str, target: float) -> None:
        """设置目标仓位"""
        self._targets[symbol] = target

    def set_targets(self, targets: dict[str, float]) -> None:
        """批量设置目标仓位"""
        self._targets.update(targets)

    def add_executor(self, symbol: str, executor: "BaseOrderExecutor") -> None:
        """添加执行器"""
        self._executors[symbol] = executor

    async def fetch_positions(self) -> dict[str, float]:
        """获取当前仓位"""
        positions = {}
        try:
            pos_list = await self._exchange.fetch_positions()
            for pos in pos_list:
                symbol = pos.get('symbol')
                contracts = pos.get('contracts', 0)
                side = pos.get('side', 'long')

                if contracts != 0:
                    # 将合约数转换为仓位方向
                    if side == 'short':
                        contracts = -abs(contracts)
                    else:
                        contracts = abs(contracts)
                    positions[symbol] = contracts

        except Exception as e:
            logger.warning(f"[{self.name}] Failed to fetch positions: {e}")

        return positions

    async def decide(self) -> None:
        """
        决策逻辑

        1. 获取当前仓位
        2. 计算目标仓位与当前仓位的差值
        3. 更新 Executor 的 target_delta
        """
        # 1. 获取当前仓位
        current_positions = await self.fetch_positions()

        # 2. 计算差值并更新 Executor
        for symbol, target in self._targets.items():
            current = current_positions.get(symbol, 0.0)
            delta = target - current

            # 更新仓位追踪
            self._positions[symbol] = PositionTarget(
                symbol=symbol,
                target=target,
                current=current,
            )

            # 更新 Executor
            if symbol in self._executors:
                executor = self._executors[symbol]
                executor.target_delta = delta

                logger.debug(
                    f"[{self.name}] {symbol}: "
                    f"target={target:.4f}, current={current:.4f}, delta={delta:.4f}"
                )


class SimpleTradingPairSelector:
    """
    简单交易对选择器

    从配置中直接选择交易对，不做复杂的评分
    """

    def __init__(
        self,
        exchange: str,
        symbols: Optional[list[str]] = None,
        bases: Optional[list[str]] = None,
        quote: str = "USDT",
        market_type: MarketType = MarketType.LINEAR,
    ):
        """
        Args:
            exchange: 交易所名称
            symbols: 直接指定的 symbol 列表（如 ["BTC/USDT:USDT", "ETH/USDT:USDT"]）
            bases: base 货币列表（如 ["BTC", "ETH"]，会自动构建 symbol）
            quote: 计价货币
            market_type: 市场类型
        """
        self._exchange = exchange
        self._symbols = symbols or []
        self._bases = bases or []
        self._quote = quote
        self._market_type = market_type
        self._table: Optional[TradingPairsTable] = None

    @property
    def table(self) -> TradingPairsTable:
        """获取 TradingPairsTable"""
        if self._table is None:
            self._build_table()
        return self._table

    def _build_table(self) -> None:
        """构建交易对表"""
        self._table = TradingPairsTable()

        # 从 symbols 添加
        for symbol in self._symbols:
            pair = self._parse_symbol(symbol)
            if pair:
                self._table.add_pair(pair)

        # 从 bases 添加
        for base in self._bases:
            pair = TradingPairs(
                base=base,
                quote=self._quote,
                exchange=self._exchange,
                market_type=self._market_type,
            )
            self._table.add_pair(pair)

    def _parse_symbol(self, symbol: str) -> Optional[TradingPairs]:
        """解析 symbol 字符串为 TradingPairs"""
        try:
            # 处理格式: BTC/USDT:USDT
            if ':' in symbol:
                base_quote, settle = symbol.split(':')
                base, quote = base_quote.split('/')
                return TradingPairs(
                    base=base,
                    quote=quote,
                    exchange=self._exchange,
                    market_type=MarketType.LINEAR,
                    settle=settle,
                )
            else:
                # 现货格式: BTC/USDT
                base, quote = symbol.split('/')
                return TradingPairs(
                    base=base,
                    quote=quote,
                    exchange=self._exchange,
                    market_type=MarketType.SPOT,
                )
        except Exception as e:
            logger.warning(f"Failed to parse symbol {symbol}: {e}")
            return None

    def get_pairs(self) -> list[TradingPairs]:
        """获取所有交易对"""
        pairs = []
        for row in self.table:
            pairs.extend(row.pairs)
        return pairs

    def get_symbols(self) -> list[str]:
        """获取所有 symbol"""
        return [p.symbol for p in self.get_pairs()]


class SimpleStrategyConfig(BaseStrategyConfig):
    """简单策略配置"""
    class_name: ClassVar[str] = "simple"

    # 执行器配置
    executor_type: str = Field("market", description="Executor type: market, limit, multi_limit")
    order_interval: float = Field(5.0, description="Order interval in seconds")
    per_order_usd: float = Field(100.0, description="Amount per order in USD")
    max_orders: int = Field(10, description="Maximum number of open orders")

    # 限价单配置
    spread_type: str = Field("fixed", description="Spread type: fixed, std, as")
    spread_pct: float = Field(0.001, description="Spread percentage")
    price_tolerance: float = Field(0.002, description="Price tolerance for order adjustment")

    # Controller 配置
    controller_interval: float = Field(1.0, description="Controller decision interval in seconds")

    @classmethod
    def get_class_type(cls) -> Type["SimpleStrategy"]:
        return SimpleStrategy

    @cached_property
    def instance(self) -> "SimpleStrategy":
        from ..exchange import BaseExchangeConfig
        exchange_config = BaseExchangeConfig.load(self.exchange_path)
        return SimpleStrategy(config=self, exchange=exchange_config.instance)


class SimpleStrategy(Listener):
    """
    简单策略

    整合:
    - SimpleTradingPairSelector: 交易对选择
    - SimpleController: 仓位控制
    - Executor: 订单执行
    """

    def __init__(
        self,
        config: SimpleStrategyConfig,
        exchange: "BaseExchange",
    ):
        super().__init__(name=config.name)
        self._config = config
        self._exchange = exchange

        # 初始化组件
        self._selector: Optional[SimpleTradingPairSelector] = None
        self._controller: Optional[SimpleController] = None
        self._executors: dict[str, "BaseOrderExecutor"] = {}

    @property
    def config(self) -> SimpleStrategyConfig:
        return self._config

    @property
    def exchange(self) -> "BaseExchange":
        return self._exchange

    @property
    def controller(self) -> Optional[SimpleController]:
        return self._controller

    async def on_start(self) -> None:
        """启动策略"""
        # 1. 初始化交易对选择器
        market_type = MarketType.LINEAR
        if self._config.market_type == "spot":
            market_type = MarketType.SPOT
        elif self._config.market_type == "inverse":
            market_type = MarketType.INVERSE

        self._selector = SimpleTradingPairSelector(
            exchange=self._exchange.exchange_id,
            symbols=self._config.symbols,
            bases=self._config.bases,
            quote=self._config.quote,
            market_type=market_type,
        )

        # 2. 初始化执行器
        await self._init_executors()

        # 3. 初始化 Controller
        self._controller = SimpleController(
            name=f"{self.name}_controller",
            exchange=self._exchange,
            targets=self._config.targets,
            executors=self._executors,
            interval=self._config.controller_interval,
        )

        # 4. 添加为子 Listener
        self.add_child(self._controller)
        for executor in self._executors.values():
            self.add_child(executor)

        logger.info(f"[{self.name}] Strategy started with {len(self._executors)} executors")

    async def _init_executors(self) -> None:
        """初始化执行器"""
        from ..executor.order_executor import (
            MarketOrderExecutor,
            LimitOrderExecutor,
            MultipleLimitOrderExecutor,
        )
        from ..executor.spread import FixedSpread, StdSpread, ASSpread

        # 创建 Spread
        spread = None
        if self._config.executor_type in ("limit", "multi_limit"):
            if self._config.spread_type == "fixed":
                spread = FixedSpread(spread_pct=self._config.spread_pct)
            elif self._config.spread_type == "std":
                spread = StdSpread(base_spread=self._config.spread_pct)
            elif self._config.spread_type == "as":
                spread = ASSpread(base_spread=self._config.spread_pct)

        # 为每个 symbol 创建执行器
        symbols = self._selector.get_symbols() if self._selector else []

        for symbol in symbols:
            executor_name = f"{self.name}_{symbol.replace('/', '_').replace(':', '_')}"

            if self._config.executor_type == "market":
                executor = MarketOrderExecutor(
                    name=executor_name,
                    exchange=self._exchange,
                    symbol=symbol,
                    order_interval=self._config.order_interval,
                    per_order_usd=self._config.per_order_usd,
                    max_orders=self._config.max_orders,
                )
            elif self._config.executor_type == "limit":
                executor = LimitOrderExecutor(
                    name=executor_name,
                    exchange=self._exchange,
                    symbol=symbol,
                    spread=spread,
                    order_interval=self._config.order_interval,
                    per_order_usd=self._config.per_order_usd,
                    max_orders=self._config.max_orders,
                    price_tolerance=self._config.price_tolerance,
                )
            elif self._config.executor_type == "multi_limit":
                executor = MultipleLimitOrderExecutor(
                    name=executor_name,
                    exchange=self._exchange,
                    symbol=symbol,
                    spread=spread,
                    order_interval=self._config.order_interval,
                    per_order_usd=self._config.per_order_usd,
                    max_orders=self._config.max_orders,
                )
            else:
                logger.warning(f"Unknown executor type: {self._config.executor_type}")
                continue

            self._executors[symbol] = executor

    def set_target(self, symbol: str, target: float) -> None:
        """设置目标仓位"""
        self._config.targets[symbol] = target
        if self._controller:
            self._controller.set_target(symbol, target)

    def set_targets(self, targets: dict[str, float]) -> None:
        """批量设置目标仓位"""
        for symbol, target in targets.items():
            self.set_target(symbol, target)

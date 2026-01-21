import asyncio
import os
from typing import Iterable

import pytest

from hft.executor.limit_executor.config import LimitExecutorConfig, LimitOrderLevel
from hft.executor.limit_executor.executor import LimitExecutor
from hft.strategy.market_neutral_positions import (
    MarketNeutralPositionsConfig,
    MarketNeutralPositionsStrategy,
)
from hft.strategy.group import StrategyGroup
from hft.strategy.static_positions import StaticPositionsStrategy, StaticPositionsStrategyConfig
from hft.strategy.config import ScopeConfig, TargetDefinition

from tests.demo.mock_exchange import MockAppRoot, MockExchange, MockExchangeGroup, MockNetwork


_DEFAULT_N_VALUES = [50, 200, 1000, 5000]
_DEFAULT_MAX_N = 1000
TRACKED_SYMBOLS = ["SYM0/USDT", "SYM1/USDT"]


def _get_max_n() -> int:
    raw = os.getenv("HFT_COMPLEXITY_MAX_N")
    if not raw:
        return _DEFAULT_MAX_N
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_MAX_N
    return max(value, 1)


_MAX_N = _get_max_n()
N_VALUES = [value for value in _DEFAULT_N_VALUES if value <= _MAX_N]
if not N_VALUES:
    N_VALUES = [_DEFAULT_N_VALUES[0]]


def _run_async(coro):
    return asyncio.run(coro)


def _build_markets(symbols: Iterable[str]) -> dict[str, dict]:
    return {symbol: {"symbol": symbol} for symbol in symbols}


def _build_symbol_list(count: int) -> list[str]:
    return [f"SYM{i}/USDT" for i in range(count)]


async def _wait_for_watch_count(network: MockNetwork, expected: int, timeout: float = 1.0) -> None:
    loop = asyncio.get_running_loop()
    start = loop.time()
    while network.active_watch_count("watch_ticker") != expected:
        if loop.time() - start > timeout:
            break
        await asyncio.sleep(0)


class CountingPerSymbolStrategy(MarketNeutralPositionsStrategy):
    """Test helper to count eval calls and resolve per-symbol indicators."""

    def __init__(self, config: MarketNeutralPositionsConfig) -> None:
        super().__init__(config)
        self.eval_calls = 0

    def _safe_eval(self, expr: str, context: dict[str, object]):
        self.eval_calls += 1
        return super()._safe_eval(expr, context)

    def _get_indicator(
        self,
        indicator_id: str,
        exchange_class: str | None,
        symbol: str | None,
        exchange_path: str | None = None,
    ):
        return super()._get_indicator(indicator_id, exchange_class, symbol, exchange_path=None)

    def _get_or_create_scope_for_target(self, exchange_path: str, symbol: str, link_index: int = 0):
        scope = super()._get_or_create_scope_for_target(exchange_path, symbol, link_index)
        current = scope
        while current and current.parent:
            current.parent.add_child(current)
            current = current.parent
        return scope


class CountingStaticPositionsStrategy(StaticPositionsStrategy):
    """Counts expression evaluations inside StaticPositionsStrategy."""

    def __init__(self, config: StaticPositionsStrategyConfig) -> None:
        super().__init__(config)
        self.eval_calls = 0

    def _safe_eval(self, expr: str, context: dict[str, object]):
        self.eval_calls += 1
        return super()._safe_eval(expr, context)


class CountingLimitExecutor(LimitExecutor):
    """Counts expression evaluations inside LimitExecutor."""

    def __init__(self, config: LimitExecutorConfig) -> None:
        super().__init__(config)
        self.eval_calls = 0

    def _safe_eval(self, expr: str, context: dict[str, object]):
        self.eval_calls += 1
        return super()._safe_eval(expr, context)


def _build_static_strategy_group(
    root: MockAppRoot,
    targets: list[TargetDefinition],
) -> tuple[StrategyGroup, CountingStaticPositionsStrategy]:
    strategy_config = StaticPositionsStrategyConfig(
        name="static-test",
        path="static_positions/test",
        targets=targets,
        condition="1 == 1",
    )
    strategy = CountingStaticPositionsStrategy(strategy_config)
    strategy_group = StrategyGroup()
    strategy_group.add_child(strategy)
    root.strategy_group = strategy_group
    root.add_child(strategy_group)
    return strategy_group, strategy


async def _run_market_neutral_case(market_count: int, include_all: bool) -> dict[str, int]:
    symbols = _build_symbol_list(market_count)
    for required in TRACKED_SYMBOLS:
        if required not in symbols:
            symbols.append(required)

    network = MockNetwork()
    exchange = MockExchange(
        name="mock/main",
        class_name="mock",
        markets=_build_markets(symbols),
        network=network,
    )
    exchange_group = MockExchangeGroup([exchange])
    root = MockAppRoot(exchange_group)

    include_symbols = ["*"] if include_all else TRACKED_SYMBOLS
    config = MarketNeutralPositionsConfig(
        name="test",
        path="market_neutral_positions/test",
        include_symbols=include_symbols,
        links=[["exchange", "trading_pair"]],
        scopes={
            "exchange": ScopeConfig(class_name="ExchangeScope"),
            "trading_pair": ScopeConfig(class_name="TradingPairScope"),
        },
        requires=["ticker"],
    )
    strategy = CountingPerSymbolStrategy(config)
    strategy.scope_manager = root.scope_manager
    root.add_child(strategy)

    try:
        strategy.get_target_positions_usd()
        expected = market_count if include_all else len(TRACKED_SYMBOLS)
        await _wait_for_watch_count(network, expected=expected)
        indicator_count = sum(
            len(pair._indicators) for pair in root.indicator_group._local_indicators.values()
        )
        return {
            "symbols": market_count,
            "include_all": int(include_all),
            "watch_ticker": network.request_counts["watch_ticker"],
            "fetch_ticker": network.request_counts["fetch_ticker"],
            "indicators": indicator_count,
            "strategy_eval_calls": strategy.eval_calls,
        }
    finally:
        await root.indicator_group.stop(True)


async def _run_limit_case(refresh_tolerance: float) -> dict[str, int]:
    network = MockNetwork()
    exchange = MockExchange(
        name="mock/main",
        class_name="mock",
        markets=_build_markets(TRACKED_SYMBOLS),
        network=network,
        ticker_price=100.0,
    )
    exchange_group = MockExchangeGroup([exchange])
    root = MockAppRoot(exchange_group)

    targets = [
        TargetDefinition(
            exchange="*",
            exchange_class="mock",
            symbol=TRACKED_SYMBOLS[0],
            position_usd="150 + 50",
            speed=0.5,
        ),
    ]
    strategy_group, strategy = _build_static_strategy_group(root, targets)

    config = LimitExecutorConfig(
        path="limit/test",
        orders=[
            LimitOrderLevel(
                spread="1.0",
                refresh_tolerance=str(refresh_tolerance),
                timeout="60",
                per_order_usd="100",
            )
        ],
    )
    executor = CountingLimitExecutor(config)
    root.add_child(executor)

    await executor.on_tick()
    exchange.ticker_price = 101.0
    await executor.on_tick()

    stats = executor.stats
    return {
        "refresh_tolerance": int(refresh_tolerance * 100),
        "fetch_ticker": network.request_counts_by_exchange[(exchange.name, "fetch_ticker")],
        "create_orders": network.request_counts_by_exchange[(exchange.name, "create_orders")],
        "cancel_orders": network.request_counts_by_exchange.get((exchange.name, "cancel_orders"), 0),
        "orders_created": stats["orders_created"],
        "orders_cancelled": stats["orders_cancelled"],
        "orders_reused": stats["orders_reused"],
        "strategy_eval_calls": strategy.eval_calls,
        "executor_eval_calls": executor.eval_calls,
    }


@pytest.mark.parametrize("market_count", N_VALUES)
def test_benchmark_market_neutral_watch_constant(benchmark, market_count: int) -> None:
    summary: dict[str, int] = {}

    def run():
        summary.update(_run_async(_run_market_neutral_case(market_count, include_all=False)))

    benchmark.pedantic(run, iterations=1, rounds=1)
    benchmark.extra_info["summary"] = summary


@pytest.mark.parametrize("market_count", N_VALUES)
def test_benchmark_market_neutral_watch_scaled(benchmark, market_count: int) -> None:
    summary: dict[str, int] = {}

    def run():
        summary.update(_run_async(_run_market_neutral_case(market_count, include_all=True)))

    benchmark.pedantic(run, iterations=1, rounds=1)
    benchmark.extra_info["summary"] = summary


@pytest.mark.parametrize("refresh_tolerance", [1.0, 0.0])
def test_benchmark_limit_executor_refresh_tolerance(benchmark, refresh_tolerance: float) -> None:
    summary: dict[str, int] = {}

    def run():
        summary.update(_run_async(_run_limit_case(refresh_tolerance)))

    benchmark.pedantic(run, iterations=1, rounds=1)
    benchmark.extra_info["summary"] = summary

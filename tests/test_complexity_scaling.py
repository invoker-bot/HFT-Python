import asyncio
import os
import warnings
from typing import Iterable

import pytest

from hft.executor.base import BaseExecutor, ExecutionResult
from hft.executor.config import BaseExecutorConfig
from hft.executor.limit_executor.config import LimitExecutorConfig, LimitOrderLevel
from hft.executor.limit_executor.executor import LimitExecutor
from hft.strategy.group import StrategyGroup
from hft.strategy.market_neutral_positions import (
    MarketNeutralPositionsConfig,
    MarketNeutralPositionsStrategy,
)
from hft.strategy.static_positions import StaticPositionsStrategy, StaticPositionsStrategyConfig
from hft.strategy.config import ScopeConfig, TargetDefinition

from tests.demo.mock_exchange import MockAppRoot, MockExchange, MockExchangeGroup, MockNetwork


_DEFAULT_N_VALUES = [50, 200, 1000, 5000]
_DEFAULT_MAX_N = 1000


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

TRACKED_SYMBOLS = ["SYM0/USDT", "SYM1/USDT"]
_WARNED_PER_SYMBOL = False


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


class CountingExecutorConfig(BaseExecutorConfig):
    class_name = "counting_executor"
    path: str = "counting/test"
    per_order_usd: float = 1.0

    @classmethod
    def get_class_type(cls):
        return CountingExecutor


class CountingExecutor(BaseExecutor):
    def __init__(self, config: CountingExecutorConfig | None = None) -> None:
        super().__init__(config or CountingExecutorConfig())

    @property
    def per_order_usd(self) -> float:
        return self.config.per_order_usd

    async def execute_delta(self, exchange, symbol, delta_usd, speed, current_price):
        return ExecutionResult(
            exchange_class=exchange.class_name,
            symbol=symbol,
            success=True,
            exchange_name=exchange.name,
            target_usd=delta_usd,
        )


def _warn_once_for_per_symbol_requires() -> bool:
    global _WARNED_PER_SYMBOL
    if _WARNED_PER_SYMBOL:
        return False
    _WARNED_PER_SYMBOL = True
    warnings.warn(
        "Per-symbol data sources scale with symbol count; request complexity grows with N.",
        UserWarning,
        stacklevel=2,
    )
    return True


class PerSymbolIndicatorStrategy(MarketNeutralPositionsStrategy):
    """Test helper to resolve per-symbol indicators without exchange_path override."""

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


def _build_static_strategy_group(root: MockAppRoot, targets: list[TargetDefinition]) -> StrategyGroup:
    strategy_config = StaticPositionsStrategyConfig(
        name="static-test",
        path="static_positions/test",
        targets=targets,
    )
    strategy = StaticPositionsStrategy(strategy_config)
    strategy_group = StrategyGroup()
    strategy_group.add_child(strategy)
    root.strategy_group = strategy_group
    root.add_child(strategy_group)
    return strategy_group


async def _run_executor_targets(executor: BaseExecutor, strategy_group: StrategyGroup):
    targets = strategy_group.get_aggregated_targets()
    return await executor._process_targets(targets)


@pytest.mark.parametrize("market_count", N_VALUES)
def test_market_neutral_watch_count_constant_with_limited_symbols(market_count: int) -> None:
    _run_async(_test_market_neutral_watch_count_constant_with_limited_symbols(market_count))


async def _test_market_neutral_watch_count_constant_with_limited_symbols(market_count: int) -> None:
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

    config = MarketNeutralPositionsConfig(
        name="test",
        path="market_neutral_positions/test",
        include_symbols=TRACKED_SYMBOLS,
        links=[["exchange", "trading_pair"]],
        scopes={
            "exchange": ScopeConfig(class_name="ExchangeScope"),
            "trading_pair": ScopeConfig(class_name="TradingPairScope"),
        },
        requires=["ticker"],
    )
    strategy = PerSymbolIndicatorStrategy(config)
    strategy.scope_manager = root.scope_manager
    root.add_child(strategy)

    try:
        strategy.get_target_positions_usd()
        await _wait_for_watch_count(network, expected=len(TRACKED_SYMBOLS))
        assert network.active_watch_count("watch_ticker") == len(TRACKED_SYMBOLS)
        assert network.request_counts["watch_ticker"] == len(TRACKED_SYMBOLS)
    finally:
        await root.indicator_group.stop(True)


def test_market_neutral_watch_count_scales_with_all_symbols_warn_once() -> None:
    _run_async(_test_market_neutral_watch_count_scales_with_all_symbols_warn_once())


async def _test_market_neutral_watch_count_scales_with_all_symbols_warn_once() -> None:
    global _WARNED_PER_SYMBOL
    _WARNED_PER_SYMBOL = False

    for market_count in N_VALUES:
        symbols = _build_symbol_list(market_count)
        network = MockNetwork()
        exchange = MockExchange(
            name="mock/main",
            class_name="mock",
            markets=_build_markets(symbols),
            network=network,
        )
        exchange_group = MockExchangeGroup([exchange])
        root = MockAppRoot(exchange_group)

        config = MarketNeutralPositionsConfig(
            name="test",
            path="market_neutral_positions/test",
            include_symbols=["*"],
            links=[["exchange", "trading_pair"]],
            scopes={
                "exchange": ScopeConfig(class_name="ExchangeScope"),
                "trading_pair": ScopeConfig(class_name="TradingPairScope"),
            },
            requires=["ticker"],
        )
        strategy = PerSymbolIndicatorStrategy(config)
        strategy.scope_manager = root.scope_manager
        root.add_child(strategy)

        try:
            strategy.get_target_positions_usd()

            indicator_count = sum(
                len(pair._indicators) for pair in root.indicator_group._local_indicators.values()
            )
            assert indicator_count == market_count

            if _WARNED_PER_SYMBOL:
                with warnings.catch_warnings(record=True) as records:
                    _warn_once_for_per_symbol_requires()
                assert not records
            else:
                with pytest.warns(UserWarning, match="Per-symbol data sources"):
                    _warn_once_for_per_symbol_requires()
        finally:
            await root.indicator_group.stop(True)


def test_watch_release_on_stop() -> None:
    _run_async(_test_watch_release_on_stop())


async def _test_watch_release_on_stop() -> None:
    network = MockNetwork()
    exchange = MockExchange(
        name="mock/main",
        class_name="mock",
        markets=_build_markets(TRACKED_SYMBOLS),
        network=network,
    )
    exchange_group = MockExchangeGroup([exchange])
    root = MockAppRoot(exchange_group)

    indicator = root.indicator_group.get_indicator("ticker", "mock", TRACKED_SYMBOLS[0])
    assert indicator is not None

    try:
        await _wait_for_watch_count(network, expected=1)
        assert network.active_watch_count("watch_ticker") == 1

        await indicator.stop()
        await _wait_for_watch_count(network, expected=0)
        assert network.active_watch_count("watch_ticker") == 0
    finally:
        await root.indicator_group.stop(True)


def test_watch_routes_to_single_exchange_per_class() -> None:
    _run_async(_test_watch_routes_to_single_exchange_per_class())


async def _test_watch_routes_to_single_exchange_per_class() -> None:
    network = MockNetwork()
    first = MockExchange(
        name="mock/primary",
        class_name="mock",
        markets=_build_markets(TRACKED_SYMBOLS),
        network=network,
    )
    second = MockExchange(
        name="mock/secondary",
        class_name="mock",
        markets=_build_markets(TRACKED_SYMBOLS),
        network=network,
    )
    exchange_group = MockExchangeGroup([first, second])
    root = MockAppRoot(exchange_group)

    indicator_a = root.indicator_group.get_indicator("ticker", "mock", TRACKED_SYMBOLS[0])
    indicator_b = root.indicator_group.get_indicator("ticker", "mock", TRACKED_SYMBOLS[1])
    assert indicator_a is not None
    assert indicator_b is not None

    await _wait_for_watch_count(network, expected=2)

    assert network.request_counts_by_exchange[(first.name, "watch_ticker")] == 2
    assert network.request_counts_by_exchange.get((second.name, "watch_ticker"), 0) == 0

    await root.indicator_group.stop(True)


def test_watch_routes_to_each_exchange_class() -> None:
    _run_async(_test_watch_routes_to_each_exchange_class())


async def _test_watch_routes_to_each_exchange_class() -> None:
    network = MockNetwork()
    first = MockExchange(
        name="mock/one",
        class_name="mock_a",
        markets=_build_markets(TRACKED_SYMBOLS),
        network=network,
    )
    second = MockExchange(
        name="mock/two",
        class_name="mock_b",
        markets=_build_markets(TRACKED_SYMBOLS),
        network=network,
    )
    exchange_group = MockExchangeGroup([first, second])
    root = MockAppRoot(exchange_group)

    indicator_a = root.indicator_group.get_indicator("ticker", "mock_a", TRACKED_SYMBOLS[0])
    indicator_b = root.indicator_group.get_indicator("ticker", "mock_b", TRACKED_SYMBOLS[1])
    assert indicator_a is not None
    assert indicator_b is not None

    await _wait_for_watch_count(network, expected=2)

    assert network.request_counts_by_exchange[(first.name, "watch_ticker")] == 1
    assert network.request_counts_by_exchange[(second.name, "watch_ticker")] == 1

    await root.indicator_group.stop(True)


@pytest.mark.parametrize("market_count", N_VALUES)
def test_executor_requests_constant_with_static_positions(market_count: int) -> None:
    _run_async(_test_executor_requests_constant_with_static_positions(market_count))


async def _test_executor_requests_constant_with_static_positions(market_count: int) -> None:
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

    targets = [
        TargetDefinition(
            exchange="*",
            exchange_class="mock",
            symbol=TRACKED_SYMBOLS[0],
            position_usd="100.0",
            speed=0.5,
        ),
        TargetDefinition(
            exchange="*",
            exchange_class="mock",
            symbol=TRACKED_SYMBOLS[1],
            position_usd="200.0",
            speed=0.5,
        ),
    ]
    strategy_group = _build_static_strategy_group(root, targets)

    executor = CountingExecutor()
    root.add_child(executor)

    results = await _run_executor_targets(executor, strategy_group)

    assert network.request_counts_by_exchange[(exchange.name, "fetch_ticker")] == len(targets)


def test_limit_executor_refresh_tolerance_reduces_calls() -> None:
    _run_async(_test_limit_executor_refresh_tolerance_reduces_calls())


async def _test_limit_executor_refresh_tolerance_reduces_calls() -> None:
    async def run_with_tolerance(refresh_tolerance: float) -> dict[str, int]:
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
                position_usd="200.0",
                speed=0.5,
            ),
        ]
        strategy_group = _build_static_strategy_group(root, targets)

        config = LimitExecutorConfig(
            path="limit/test",
            orders=[
                LimitOrderLevel(
                    spread=1.0,
                    refresh_tolerance=refresh_tolerance,
                    timeout=60.0,
                    per_order_usd=100.0,
                )
            ],
        )
        executor = LimitExecutor(config)
        root.add_child(executor)

        results_first = await _run_executor_targets(executor, strategy_group)
        exchange.ticker_price = 101.0
        results_second = await _run_executor_targets(executor, strategy_group)

        summary = {
            "results": len(results_first) + len(results_second),
            "fetch_ticker": network.request_counts_by_exchange[(exchange.name, "fetch_ticker")],
            "create_orders": network.request_counts_by_exchange[(exchange.name, "create_orders")],
            "cancel_orders": network.request_counts_by_exchange.get((exchange.name, "cancel_orders"), 0),
            "orders_created": executor.stats["orders_created"],
            "orders_cancelled": executor.stats["orders_cancelled"],
            "orders_reused": executor.stats["orders_reused"],
        }
        return summary

    high = await run_with_tolerance(refresh_tolerance=1.0)
    low = await run_with_tolerance(refresh_tolerance=0.0)

    assert high["create_orders"] <= low["create_orders"]
    assert high["cancel_orders"] <= low["cancel_orders"]

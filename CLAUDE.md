# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Test Commands

```bash
# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Run unit tests (excludes integration tests by default)
pytest

# Run a single test file
pytest tests/test_executor.py -v

# Run a specific test
pytest tests/test_executor.py::TestClassName::test_method -v

# Run all tests including integration tests
pytest -m ""

# Run only integration tests
pytest -m integration

# Lint
flake8 hft/
pylint hft/
```

- **Test framework**: pytest with `asyncio_mode=auto` (no need for `@pytest.mark.asyncio`)
- **Time mocking**: Uses `freezegun` for time-dependent tests
- **Test markers**: `integration`, `integration_test` (needs `INTEGRATION_TEST_ALLOW_LISTS` env), `slow_integration_test`

## Architecture Overview

### Listener Tree

All components inherit from `Listener` (hft/core/listener.py), forming a tree with unified lifecycle:

```
AppCore (root)
├── ExchangeGroup
│   └── BaseExchange
│       ├── ExchangeBalanceListener
│       ├── ExchangePositionListener
│       └── ExchangeOrderBillListener
├── IndicatorGroup
│   └── TradingPairIndicators
│       ├── TickerDataSource
│       ├── OrderBookDataSource
│       └── ComputedIndicators...
├── BaseStrategy (StaticPositionsStrategy)
└── BaseExecutor (DefaultExecutor)
```

States: `STOPPED → STARTING → RUNNING → STOPPING → STOPPED`

Persistence: Listeners serialize via pickle. Fields in `__pickle_exclude__` are excluded and rebuilt on restore via `initialize()`.

### Data Flow

```
Exchange → DataSource.on_tick() → HealthyDataArray
                                       ↓
                              Indicator.get_vars() → Context Variables
                                       ↓
                              Strategy (flow execution) → targets
                                       ↓
                              Executor (expression evaluation) → Orders
```

### Scope & Flow System

Replaced the old "links" system. Configured via `flow:` in strategy/executor YAML configs.

- **Scopes** (hft/core/scope/): GlobalScope → ExchangeClassScope → ExchangeScope → TradingPairClassScope → TradingPairScope
- **FlowScopeNode** (hft/core/scope/vm.py): Temporary computation nodes with ChainMap variable inheritance
- **VirtualMachine**: Evaluates expressions using `simpleeval`, with a safe function whitelist (`abs`, `min`, `max`, `clip`, `log`, etc.)

### Indicator System

Two types, both inherit `BaseIndicator`:
- **DataSource** (hft/indicator/datasource/): Fetches data from exchange via watch/fetch, stores in `HealthyDataArray`. Method: `get_vars() → dict`
- **Computed** (hft/indicator/computed/): Derives values from other indicators. Method: `calculate_vars(direction) → dict`

### Executor System

Only `DefaultExecutor` exists currently. `BaseExecutor` (hft/executor/base.py) uses:
- `requires:` to declare indicator dependencies
- `vars:` for expression-based variable computation
- `orders:` / `order:` + `order_levels:` for order definitions
- Price calculation: buy → `bid_price - spread`, sell → `ask_price + spread`

### Configuration System

- `BaseConfig` (Pydantic models) loaded from YAML files under `conf/`
- `BaseConfigPath` provides lazy loading with caching
- Config hierarchy: `conf/app/` → `conf/strategy/` → `conf/executor/` → `conf/exchange/`
- Variable definitions support three formats: standard `{name, value}`, dict `{name: value}`, string `"name=value"`

### HealthyData (hft/core/healthy_data.py)

Two variants:
- `HealthyData[T]`: Single-value cache with max_age TTL
- `HealthyDataArray[T]`: Time-windowed array stored as `(value, timestamp)` tuples, with health checks (point count, CV, range coverage)

### Plugin System

Uses `pluggy` (hft/plugin/base.py). Key hooks: `on_order_creating`, `on_order_error`, `on_balance_update(exchange, account, balance)`, `on_position_update(exchange, account, positions)`.

## Key Patterns

- **Cache decorators** (hft/core/cache_decorator.py): Time-based forced refresh caching
  - `@cache(ttl)`: Auto-detect sync/async functions
  - `@cache_sync(ttl)`: Sync function cache with forced refresh (not lazy like TTLCache)
  - `@cache_async(ttl)`: Async function cache using AsyncTTL
  - `@instance_cache(ttl)`: Auto-detect sync/async instance methods
  - `@instance_cache_sync(ttl)`: Sync instance method cache
  - `@instance_cache_async(ttl)`: Async instance method cache, uses `id(self)` to avoid pickle issues
- **GroupListener**: Dynamically syncs children based on `sync_children_params()` return value
- **Duration parsing**: `parse_duration()` accepts `"30s"`, `"5m"`, `"1h"`, `"7d"` or raw seconds
- **Event system**: `pyee.AsyncIOEventEmitter` for order events (`order:creating`, `order:created`, `order:canceling`, `order:canceled`, `order:updated`)

## Language

This is a Chinese-language project. Comments, docs, and commit messages are primarily in Chinese.

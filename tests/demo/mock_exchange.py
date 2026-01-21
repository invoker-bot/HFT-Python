from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, DefaultDict, Optional

from hft.core.listener import Listener
from hft.core.scope.manager import ScopeManager
from hft.indicator.group import IndicatorGroup


@dataclass
class MockExchangeConfig:
    path: str


class MockNetwork:
    """Collects request and watch subscription counts."""

    def __init__(self) -> None:
        self.request_counts: DefaultDict[str, int] = defaultdict(int)
        self.request_counts_by_exchange: DefaultDict[tuple[str, str], int] = defaultdict(int)
        self.request_counts_by_key: DefaultDict[tuple[str, str], int] = defaultdict(int)
        self._active_watches: set[tuple[str, str, str]] = set()

    def record_request(self, exchange_name: str, kind: str, key: Optional[str] = None) -> None:
        self.request_counts[kind] += 1
        self.request_counts_by_exchange[(exchange_name, kind)] += 1
        if key is not None:
            self.request_counts_by_key[(kind, key)] += 1

    def register_watch(self, exchange_name: str, kind: str, key: str) -> None:
        token = (exchange_name, kind, key)
        if token not in self._active_watches:
            self._active_watches.add(token)
            self.record_request(exchange_name, kind, key)

    def unregister_watch(self, exchange_name: str, kind: str, key: str) -> None:
        self._active_watches.discard((exchange_name, kind, key))

    def active_watch_count(self, kind: Optional[str] = None) -> int:
        if kind is None:
            return len(self._active_watches)
        return sum(1 for _, watch_kind, _ in self._active_watches if watch_kind == kind)


class MockExchange:
    """Minimal exchange stub with request counters."""

    def __init__(
        self,
        name: str,
        class_name: str,
        markets: dict[str, Any],
        network: MockNetwork,
        ticker_price: float = 100.0,
    ) -> None:
        self.name = name
        self.class_name = class_name
        self.config = MockExchangeConfig(path=name)
        self.markets = markets
        self.network = network
        self.ticker_price = ticker_price
        self.positions: dict[str, float] = {}
        self.balance: dict[str, float] = {}
        self._watch_events: dict[str, asyncio.Event] = {}
        self._order_id_counter = 0
        self._active_orders: dict[str, dict[str, Any]] = {}
        self._initialized_symbols: set[str] = set()

    def _watch_key(self, symbol: str) -> str:
        return symbol

    def _get_watch_event(self, symbol: str) -> asyncio.Event:
        if symbol not in self._watch_events:
            self._watch_events[symbol] = asyncio.Event()
        return self._watch_events[symbol]

    async def load_markets(self) -> dict[str, Any]:
        self.network.record_request(self.name, "load_markets")
        return self.markets

    async def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        self.network.record_request(self.name, "fetch_ticker", symbol)
        price = float(self.ticker_price)
        return {
            "symbol": symbol,
            "timestamp": int(time.time() * 1000),
            "last": price,
            "bid": price - 0.5,
            "ask": price + 0.5,
        }

    async def watch_ticker(self, symbol: str) -> dict[str, Any]:
        key = self._watch_key(symbol)
        self.network.register_watch(self.name, "watch_ticker", key)
        event = self._get_watch_event(symbol)
        try:
            await event.wait()
        except asyncio.CancelledError:
            self.network.unregister_watch(self.name, "watch_ticker", key)
            raise
        return await self.fetch_ticker(symbol)

    async def un_watch_ticker(self, symbol: str) -> None:
        key = self._watch_key(symbol)
        self.network.record_request(self.name, "un_watch_ticker", key)
        self.network.unregister_watch(self.name, "watch_ticker", key)

    async def fetch_order_book(self, symbol: str, limit: Optional[int] = None) -> dict[str, Any]:
        self.network.record_request(self.name, "fetch_order_book", symbol)
        return {"symbol": symbol, "bids": [], "asks": [], "timestamp": int(time.time() * 1000)}

    async def watch_order_book(self, symbol: str, limit: Optional[int] = None) -> dict[str, Any]:
        key = self._watch_key(symbol)
        self.network.register_watch(self.name, "watch_order_book", key)
        event = self._get_watch_event(symbol)
        try:
            await event.wait()
        except asyncio.CancelledError:
            self.network.unregister_watch(self.name, "watch_order_book", key)
            raise
        return await self.fetch_order_book(symbol, limit=limit)

    async def un_watch_order_book(self, symbol: str) -> None:
        key = self._watch_key(symbol)
        self.network.record_request(self.name, "un_watch_order_book", key)
        self.network.unregister_watch(self.name, "watch_order_book", key)

    async def fetch_trades(self, symbol: str, since: Optional[int] = None, limit: Optional[int] = None) -> list[dict[str, Any]]:
        self.network.record_request(self.name, "fetch_trades", symbol)
        return []

    async def watch_trades(self, symbol: str) -> list[dict[str, Any]]:
        key = self._watch_key(symbol)
        self.network.register_watch(self.name, "watch_trades", key)
        event = self._get_watch_event(symbol)
        try:
            await event.wait()
        except asyncio.CancelledError:
            self.network.unregister_watch(self.name, "watch_trades", key)
            raise
        return await self.fetch_trades(symbol)

    async def un_watch_trades(self, symbol: str) -> None:
        key = self._watch_key(symbol)
        self.network.record_request(self.name, "un_watch_trades", key)
        self.network.unregister_watch(self.name, "watch_trades", key)

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        since: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[list[float]]:
        self.network.record_request(self.name, "fetch_ohlcv", symbol)
        return []

    async def watch_ohlcv(self, symbol: str, timeframe: str = "1m") -> list[list[float]]:
        key = f"{symbol}:{timeframe}"
        self.network.register_watch(self.name, "watch_ohlcv", key)
        event = self._get_watch_event(symbol)
        try:
            await event.wait()
        except asyncio.CancelledError:
            self.network.unregister_watch(self.name, "watch_ohlcv", key)
            raise
        return await self.fetch_ohlcv(symbol, timeframe=timeframe)

    async def un_watch_ohlcv(self, symbol: str, timeframe: str = "1m") -> None:
        key = f"{symbol}:{timeframe}"
        self.network.record_request(self.name, "un_watch_ohlcv", key)
        self.network.unregister_watch(self.name, "watch_ohlcv", key)

    async def medal_fetch_positions(self) -> dict[str, float]:
        self.network.record_request(self.name, "fetch_positions")
        return self.positions.copy()

    async def medal_initialize_symbol(self, symbol: str) -> None:
        self.network.record_request(self.name, "initialize_symbol", symbol)
        self._initialized_symbols.add(symbol)

    def get_contract_size(self, symbol: str) -> float:
        return 1.0

    async def create_orders(self, requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.network.record_request(self.name, "create_orders")
        results = []
        for request in requests:
            self._order_id_counter += 1
            order_id = f"order-{self._order_id_counter}"
            self._active_orders[order_id] = {
                "id": order_id,
                "symbol": request.get("symbol"),
                "side": request.get("side"),
                "amount": request.get("amount"),
                "price": request.get("price"),
            }
            results.append({"id": order_id})
        return results

    async def cancel_orders(self, order_ids: list[str], symbol: str) -> None:
        self.network.record_request(self.name, "cancel_orders", symbol)
        for order_id in order_ids:
            self._active_orders.pop(order_id, None)


class MockExchangeGroup:
    """Lightweight exchange group for tests."""

    def __init__(self, exchanges: list[MockExchange]) -> None:
        self.children = {exchange.name: exchange for exchange in exchanges}
        self.exchanges = self.children

    def get_exchange_by_class(self, class_name: str) -> Optional[MockExchange]:
        for exchange in self.children.values():
            if exchange.class_name == class_name:
                return exchange
        return None

    def get_exchanges_by_class(self, class_name: str) -> list[MockExchange]:
        return [exchange for exchange in self.children.values() if exchange.class_name == class_name]


class MockAppRoot(Listener):
    """Minimal root listener to host exchange/indicator/scope components."""

    def __init__(self, exchange_group: MockExchangeGroup) -> None:
        super().__init__(name="MockAppRoot", interval=None)
        self.exchange_group = exchange_group
        self.indicator_group = IndicatorGroup()
        self.scope_manager = ScopeManager()
        self.add_child(self.indicator_group)

    async def on_tick(self) -> bool:
        return False

"""
Unit tests for Feature 0007: FundingRate Indicators

Tests cover:
- GlobalFundingRateIndicator: 全局资金费率获取和分发
- FundingRateIndicator: 交易对级资金费率订阅
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from dataclasses import dataclass

from hft.indicator.datasource.funding_rate_datasource import (
    GlobalFundingRateIndicator,
    FundingRateIndicator,
)


@dataclass
class MockFundingRate:
    """Mock FundingRate for testing."""
    symbol: str
    base_funding_rate: float
    daily_funding_rate: float
    index_price: float
    mark_price: float
    timestamp: float = 0.0


class MockExchange:
    """Mock exchange for testing."""
    def __init__(self, class_name: str = "okx"):
        self.class_name = class_name
        self.ready = True
        self._funding_rates = {}

    async def medal_fetch_funding_rates(self):
        return self._funding_rates


class MockExchangeGroup:
    """Mock exchange group."""
    def __init__(self, exchange: MockExchange):
        self._exchange = exchange

    def get_exchange_by_class(self, class_name: str):
        if class_name == self._exchange.class_name:
            return self._exchange
        return None


# ============================================================
# GlobalFundingRateIndicator Tests
# ============================================================

class TestGlobalFundingRateIndicator:
    """Tests for GlobalFundingRateIndicator."""

    def test_init(self):
        """Should initialize with correct attributes."""
        indicator = GlobalFundingRateIndicator(
            exchange_class="okx",
            interval=5.0,
            window=600.0,
        )

        assert indicator.exchange_class == "okx"
        assert indicator.name == "global_funding_rate:okx"
        assert indicator._window == 600.0

    def test_calculate_vars_empty(self):
        """Should return empty dict when no data."""
        indicator = GlobalFundingRateIndicator(exchange_class="okx")

        result = indicator.calculate_vars(direction=1)

        assert result == {"funding_rates": {}}

    def test_calculate_vars_with_data(self):
        """Should return funding rates dict."""
        indicator = GlobalFundingRateIndicator(exchange_class="okx")

        mock_rates = {
            "BTC/USDT:USDT": MockFundingRate(
                symbol="BTC/USDT:USDT",
                base_funding_rate=0.0001,
                daily_funding_rate=0.0003,
                index_price=50000.0,
                mark_price=50010.0,
            )
        }
        indicator._data.append(1000.0, mock_rates)

        result = indicator.calculate_vars(direction=1)

        assert "funding_rates" in result
        assert "BTC/USDT:USDT" in result["funding_rates"]

    @pytest.mark.asyncio
    async def test_on_tick_no_exchange(self):
        """Should handle missing exchange gracefully."""
        indicator = GlobalFundingRateIndicator(exchange_class="okx")
        indicator._root = MagicMock()
        indicator._root.exchange_group = None

        result = await indicator.on_tick()

        assert result is False

    @pytest.mark.asyncio
    async def test_on_tick_exchange_not_ready(self):
        """Should skip when exchange not ready."""
        indicator = GlobalFundingRateIndicator(exchange_class="okx")

        mock_exchange = MockExchange("okx")
        mock_exchange.ready = False

        indicator._root = MagicMock()
        indicator._root.exchange_group = MockExchangeGroup(mock_exchange)

        result = await indicator.on_tick()

        assert result is False


# ============================================================
# FundingRateIndicator Tests
# ============================================================

class TestFundingRateIndicator:
    """Tests for FundingRateIndicator."""

    def test_init(self):
        """Should initialize with correct attributes."""
        indicator = FundingRateIndicator(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            window=300.0,
        )

        assert indicator.exchange_class == "okx"
        assert indicator.symbol == "BTC/USDT:USDT"
        assert indicator.name == "funding_rate:okx:BTC/USDT:USDT"
        assert indicator._subscribed is False

    def test_calculate_vars_empty(self):
        """Should return default values when no data."""
        indicator = FundingRateIndicator(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
        )

        result = indicator.calculate_vars(direction=1)

        assert result["funding_rate"] is None
        assert result["daily_funding_rate"] == 0.0
        assert result["base_funding_rate"] == 0.0

    def test_calculate_vars_with_data(self):
        """Should return funding rate values."""
        indicator = FundingRateIndicator(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
        )

        mock_fr = MockFundingRate(
            symbol="BTC/USDT:USDT",
            base_funding_rate=0.0001,
            daily_funding_rate=0.0003,
            index_price=50000.0,
            mark_price=50010.0,
        )
        indicator._data.append(1000.0, mock_fr)

        result = indicator.calculate_vars(direction=1)

        assert result["funding_rate"] == mock_fr
        assert result["daily_funding_rate"] == 0.0003
        assert result["base_funding_rate"] == 0.0001
        assert result["index_price"] == 50000.0
        assert result["mark_price"] == 50010.0

    def test_on_global_update_matching_symbol(self):
        """Should update data when symbol matches."""
        indicator = FundingRateIndicator(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
        )

        mock_fr = MockFundingRate(
            symbol="BTC/USDT:USDT",
            base_funding_rate=0.0001,
            daily_funding_rate=0.0003,
            index_price=50000.0,
            mark_price=50010.0,
        )
        funding_rates = {"BTC/USDT:USDT": mock_fr}

        indicator._on_global_update(1000.0, funding_rates)

        assert len(indicator._data) == 1
        assert indicator._data.latest == mock_fr

    def test_on_global_update_non_matching_symbol(self):
        """Should ignore updates for other symbols."""
        indicator = FundingRateIndicator(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
        )

        mock_fr = MockFundingRate(
            symbol="ETH/USDT:USDT",
            base_funding_rate=0.0002,
            daily_funding_rate=0.0006,
            index_price=3000.0,
            mark_price=3001.0,
        )
        funding_rates = {"ETH/USDT:USDT": mock_fr}

        indicator._on_global_update(1000.0, funding_rates)

        assert len(indicator._data) == 0

    def test_log_state_dict(self):
        """Should include relevant state info."""
        indicator = FundingRateIndicator(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
        )

        state = indicator.log_state_dict

        assert state["exchange_class"] == "okx"
        assert state["symbol"] == "BTC/USDT:USDT"
        assert state["subscribed"] is False

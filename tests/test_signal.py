"""
Unit tests for TradeSignal.

Tests cover:
- Basic signal creation and field validation
- Value and speed clamping to valid ranges
- Side derivation (long/short/flat)
- Urgency and close signal detection
- Timestamp and metadata handling
"""
import pytest
import time

from hft.strategy.signal_strategy import TradeSignal, SignalSide


class TestTradeSignalCreation:
    """Tests for TradeSignal creation and basic fields."""

    def test_create_basic_signal(self):
        """Should create a signal with required fields."""
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5
        )

        assert signal.exchange_class == "okx"
        assert signal.symbol == "BTC/USDT:USDT"
        assert signal.value == 0.5
        assert signal.speed == 0.5  # default

    def test_create_signal_with_all_fields(self):
        """Should create a signal with all fields."""
        signal = TradeSignal(
            exchange_class="binance",
            symbol="ETH/USDT:USDT",
            value=-0.3,
            speed=0.9,
            source="test_strategy",
            reason="Test signal",
            metadata={"key": "value"}
        )

        assert signal.exchange_class == "binance"
        assert signal.symbol == "ETH/USDT:USDT"
        assert signal.value == -0.3
        assert signal.speed == 0.9
        assert signal.source == "test_strategy"
        assert signal.reason == "Test signal"
        assert signal.metadata == {"key": "value"}

    def test_timestamp_is_auto_generated(self):
        """Timestamp should be auto-generated if not provided."""
        before = time.time()
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5
        )
        after = time.time()

        assert before <= signal.timestamp <= after

    def test_timestamp_can_be_custom(self):
        """Timestamp can be set to a custom value."""
        custom_time = 1000.0
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5,
            timestamp=custom_time
        )

        assert signal.timestamp == custom_time

    def test_metadata_defaults_to_empty_dict(self):
        """Metadata should default to empty dict."""
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5
        )

        assert signal.metadata == {}

    def test_metadata_is_independent_per_instance(self):
        """Each signal should have independent metadata dict."""
        signal1 = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5
        )
        signal2 = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5
        )

        signal1.metadata["key"] = "value"

        assert "key" not in signal2.metadata


class TestTradeSignalValueClamping:
    """Tests for value clamping to [-1.0, 1.0]."""

    def test_value_in_range_unchanged(self):
        """Values within range should be unchanged."""
        for value in [-1.0, -0.5, 0.0, 0.5, 1.0]:
            signal = TradeSignal(
                exchange_class="okx",
                symbol="BTC/USDT:USDT",
                value=value
            )
            assert signal.value == value

    def test_value_above_max_clamped_to_one(self):
        """Values > 1.0 should be clamped to 1.0."""
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=2.5
        )

        assert signal.value == 1.0

    def test_value_below_min_clamped_to_negative_one(self):
        """Values < -1.0 should be clamped to -1.0."""
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=-3.0
        )

        assert signal.value == -1.0

    def test_extreme_values_clamped(self):
        """Extreme values should be clamped."""
        signal_high = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=float('inf')
        )
        # Note: min(1.0, inf) = 1.0
        assert signal_high.value == 1.0

        signal_low = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=float('-inf')
        )
        assert signal_low.value == -1.0


class TestTradeSignalSpeedClamping:
    """Tests for speed clamping to [0.0, 1.0]."""

    def test_speed_in_range_unchanged(self):
        """Speed within range should be unchanged."""
        for speed in [0.0, 0.25, 0.5, 0.75, 1.0]:
            signal = TradeSignal(
                exchange_class="okx",
                symbol="BTC/USDT:USDT",
                value=0.5,
                speed=speed
            )
            assert signal.speed == speed

    def test_speed_above_max_clamped_to_one(self):
        """Speed > 1.0 should be clamped to 1.0."""
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5,
            speed=1.5
        )

        assert signal.speed == 1.0

    def test_speed_below_min_clamped_to_zero(self):
        """Speed < 0.0 should be clamped to 0.0."""
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5,
            speed=-0.5
        )

        assert signal.speed == 0.0


class TestTradeSignalSide:
    """Tests for side property derivation."""

    def test_positive_value_is_long(self):
        """Positive value should result in LONG side."""
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5
        )

        assert signal.side == SignalSide.LONG

    def test_negative_value_is_short(self):
        """Negative value should result in SHORT side."""
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=-0.5
        )

        assert signal.side == SignalSide.SHORT

    def test_zero_value_is_flat(self):
        """Zero value should result in FLAT side."""
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.0
        )

        assert signal.side == SignalSide.FLAT

    def test_small_positive_value_is_long(self):
        """Small positive value should still be LONG."""
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.001
        )

        assert signal.side == SignalSide.LONG

    def test_small_negative_value_is_short(self):
        """Small negative value should still be SHORT."""
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=-0.001
        )

        assert signal.side == SignalSide.SHORT


class TestTradeSignalIsUrgent:
    """Tests for is_urgent property."""

    def test_speed_above_threshold_is_urgent(self):
        """Speed > 0.8 should be urgent."""
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5,
            speed=0.9
        )

        assert signal.is_urgent is True

    def test_speed_at_threshold_is_not_urgent(self):
        """Speed == 0.8 should NOT be urgent."""
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5,
            speed=0.8
        )

        assert signal.is_urgent is False

    def test_speed_below_threshold_is_not_urgent(self):
        """Speed < 0.8 should NOT be urgent."""
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5,
            speed=0.5
        )

        assert signal.is_urgent is False

    def test_max_speed_is_urgent(self):
        """Speed == 1.0 should be urgent."""
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5,
            speed=1.0
        )

        assert signal.is_urgent is True


class TestTradeSignalIsClose:
    """Tests for is_close property."""

    def test_zero_value_is_close(self):
        """value == 0 should be a close signal."""
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.0
        )

        assert signal.is_close is True

    def test_small_positive_value_is_close(self):
        """Small positive value < 0.01 should be close."""
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.005
        )

        assert signal.is_close is True

    def test_small_negative_value_is_close(self):
        """Small negative value > -0.01 should be close."""
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=-0.005
        )

        assert signal.is_close is True

    def test_significant_value_is_not_close(self):
        """Significant value should NOT be close."""
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.1
        )

        assert signal.is_close is False

    def test_threshold_value_is_not_close(self):
        """Value == 0.01 should NOT be close."""
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.01
        )

        assert signal.is_close is False


class TestTradeSignalRepr:
    """Tests for __repr__ method."""

    def test_repr_contains_key_info(self):
        """__repr__ should contain key information."""
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5,
            speed=0.8,
            source="test_strategy"
        )

        repr_str = repr(signal)

        assert "okx" in repr_str
        assert "BTC/USDT:USDT" in repr_str
        assert "0.50" in repr_str  # value formatted
        assert "0.80" in repr_str  # speed formatted
        assert "test_strategy" in repr_str

    def test_repr_is_readable(self):
        """__repr__ should be human readable."""
        signal = TradeSignal(
            exchange_class="binance",
            symbol="ETH/USDT:USDT",
            value=-0.3,
            speed=0.9,
            source="momentum"
        )

        repr_str = repr(signal)

        assert repr_str.startswith("TradeSignal(")
        assert repr_str.endswith(")")


class TestSignalSideEnum:
    """Tests for SignalSide enum."""

    def test_enum_values(self):
        """SignalSide should have correct values."""
        assert SignalSide.LONG.value == "long"
        assert SignalSide.SHORT.value == "short"
        assert SignalSide.FLAT.value == "flat"

    def test_enum_comparison(self):
        """SignalSide enums should be comparable."""
        assert SignalSide.LONG == SignalSide.LONG
        assert SignalSide.LONG != SignalSide.SHORT


class TestTradeSignalEdgeCases:
    """Tests for edge cases."""

    def test_very_small_positive_value(self):
        """Very small positive value should work."""
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=1e-10
        )

        assert signal.value == 1e-10
        assert signal.side == SignalSide.LONG
        assert signal.is_close is True  # < 0.01

    def test_unicode_in_fields(self):
        """Unicode characters should work in string fields."""
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5,
            source="策略1",
            reason="价格突破"
        )

        assert signal.source == "策略1"
        assert signal.reason == "价格突破"

    def test_empty_strings_in_fields(self):
        """Empty strings should work in optional fields."""
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5,
            source="",
            reason=""
        )

        assert signal.source == ""
        assert signal.reason == ""

    def test_complex_metadata(self):
        """Complex nested metadata should work."""
        metadata = {
            "indicators": {"rsi": 30, "macd": -0.5},
            "prices": [100.0, 101.0, 99.0],
            "nested": {"a": {"b": {"c": 1}}}
        }
        signal = TradeSignal(
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            value=0.5,
            metadata=metadata
        )

        assert signal.metadata == metadata
        assert signal.metadata["indicators"]["rsi"] == 30

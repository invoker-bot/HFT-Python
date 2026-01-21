"""
测试 Indicator debug 参数功能

验证 debug 模式下 calculate_vars 的日志记录
"""
import logging
from unittest.mock import Mock, patch
import pytest
from hft.indicator.datasource.ticker_datasource import TickerDataSource
from hft.indicator.factory import IndicatorFactory


class TestIndicatorDebugParameter:
    """测试 Indicator debug 参数"""

    def test_indicator_accepts_debug_parameter(self):
        """测试 Indicator 接受 debug 参数"""
        ticker = TickerDataSource(
            exchange_class="okx",
            symbol="BTC/USDT",
            debug=True
        )

        assert hasattr(ticker, '_debug')
        assert ticker._debug is True

    def test_indicator_debug_defaults_to_false(self):
        """测试 debug 参数默认为 False"""
        ticker = TickerDataSource(
            exchange_class="okx",
            symbol="BTC/USDT"
        )

        assert hasattr(ticker, '_debug')
        assert ticker._debug is False

    def test_factory_passes_debug_parameter(self):
        """测试 IndicatorFactory 传递 debug 参数"""
        factory = IndicatorFactory(
            "TickerDataSource",
            {"debug": True}  # TickerDataSource 不需要 window 参数
        )

        indicator = factory("okx", "BTC/USDT")
        assert indicator is not None
        assert indicator._debug is True

    def test_factory_debug_false(self):
        """测试 IndicatorFactory debug=False"""
        factory = IndicatorFactory(
            "TickerDataSource",
            {"debug": False}  # TickerDataSource 不需要 window 参数
        )

        indicator = factory("okx", "BTC/USDT")
        assert indicator is not None
        assert indicator._debug is False

    def test_indicator_accepts_debug_log_interval(self):
        """测试 Indicator 接受 debug_log_interval 参数"""
        ticker = TickerDataSource(
            exchange_class="okx",
            symbol="BTC/USDT",
            debug=True,
            debug_log_interval=60.0
        )

        assert hasattr(ticker, '_debug_log_interval')
        assert ticker._debug_log_interval == 60.0

    def test_indicator_debug_log_interval_defaults_to_none(self):
        """测试 debug_log_interval 参数默认为 None"""
        ticker = TickerDataSource(
            exchange_class="okx",
            symbol="BTC/USDT",
            debug=True
        )

        assert hasattr(ticker, '_debug_log_interval')
        assert ticker._debug_log_interval is None

    def test_factory_passes_debug_log_interval(self):
        """测试 IndicatorFactory 传递 debug_log_interval 参数"""
        factory = IndicatorFactory(
            "TickerDataSource",
            {"debug": True, "debug_log_interval": 30.0}
        )

        indicator = factory("okx", "BTC/USDT")
        assert indicator is not None
        assert indicator._debug is True
        assert indicator._debug_log_interval == 30.0

    def test_factory_debug_log_interval_with_duration_string(self):
        """测试 IndicatorFactory 支持 duration 字符串格式的 debug_log_interval"""
        factory = IndicatorFactory(
            "TickerDataSource",
            {"debug": True, "debug_log_interval": "1m"}
        )

        indicator = factory("okx", "BTC/USDT")
        assert indicator is not None
        assert indicator._debug is True
        assert indicator._debug_log_interval == 60.0  # 1m = 60s

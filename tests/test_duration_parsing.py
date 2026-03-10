"""
测试 duration 字符串解析功能

Issue 0015: window 支持 duration 字符串（如 60s/1m/5m）
"""
import pytest
from humanfriendly import InvalidTimespan
from hft.core.duration import parse_duration


class TestParseDuration:
    """测试 parse_duration 函数"""

    def test_none_returns_zero(self):
        """None 应返回 0.0"""
        assert parse_duration(None) == 0.0

    def test_int_returns_float(self):
        """int 应转换为 float"""
        assert parse_duration(60) == 60.0
        assert parse_duration(0) == 0.0
        assert parse_duration(300) == 300.0

    def test_float_returns_same(self):
        """float 应直接返回"""
        assert parse_duration(60.5) == 60.5
        assert parse_duration(0.0) == 0.0
        assert parse_duration(300.123) == 300.123

    def test_seconds_duration(self):
        """秒单位 duration"""
        assert parse_duration("60s") == 60.0
        assert parse_duration("1s") == 1.0
        assert parse_duration("0s") == 0.0
        assert parse_duration("3.5s") == 3.5

    def test_minutes_duration(self):
        """分钟单位 duration"""
        assert parse_duration("1m") == 60.0
        assert parse_duration("5m") == 300.0
        assert parse_duration("0m") == 0.0
        assert parse_duration("1.5m") == 90.0

    def test_hours_duration(self):
        """小时单位 duration"""
        assert parse_duration("1h") == 3600.0
        assert parse_duration("2h") == 7200.0
        assert parse_duration("0h") == 0.0
        assert parse_duration("0.5h") == 1800.0

    def test_days_duration(self):
        """天单位 duration"""
        assert parse_duration("1d") == 86400.0
        assert parse_duration("2d") == 172800.0
        assert parse_duration("0d") == 0.0
        assert parse_duration("0.5d") == 43200.0

    def test_milliseconds_duration(self):
        """毫秒单位 duration"""
        assert parse_duration("1000ms") == 1.0
        assert parse_duration("500ms") == 0.5
        assert parse_duration("0ms") == 0.0
        assert parse_duration("1500ms") == 1.5

    def test_strip_whitespace(self):
        """应忽略前后空白"""
        assert parse_duration(" 60s ") == 60.0
        assert parse_duration("  1m  ") == 60.0

    def test_bare_number_string(self):
        """纯数字字符串应被解析为秒"""
        assert parse_duration("60") == 60.0
        assert parse_duration("60 s") == 60.0

    def test_invalid_format(self):
        """非法格式应抛出异常"""
        with pytest.raises(InvalidTimespan):
            parse_duration("s60")  # 单位在前

        with pytest.raises(InvalidTimespan):
            parse_duration("abc")  # 非数字

        with pytest.raises(InvalidTimespan):
            parse_duration("")  # 空字符串

    def test_unsupported_unit(self):
        """不支持的单位应抛出异常"""
        with pytest.raises(InvalidTimespan):
            parse_duration("60x")

    def test_unsupported_type(self):
        """不支持的类型应抛出 TypeError"""
        with pytest.raises(TypeError, match="Unsupported duration type"):
            parse_duration([60])  # list

        with pytest.raises(TypeError, match="Unsupported duration type"):
            parse_duration({"value": 60})  # dict

    def test_common_use_cases(self):
        """常见用例"""
        # 1分钟窗口
        assert parse_duration("1m") == parse_duration(60) == parse_duration(60.0)

        # 5分钟窗口
        assert parse_duration("5m") == parse_duration(300) == parse_duration(300.0)

        # 1小时窗口
        assert parse_duration("1h") == parse_duration(3600) == parse_duration(3600.0)

        # 无窗口
        assert parse_duration(None) == parse_duration(0) == parse_duration("0s")

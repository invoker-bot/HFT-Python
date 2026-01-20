"""
Issue 0010: Indicator window null 归一化测试

测试 window=None 与 window=0 的等价性
"""
import time

import pytest

# pylint: disable=protected-access

from hft.indicator.base import BaseIndicator


class DummyIndicator(BaseIndicator[float]):
    """测试用的简单 Indicator"""

    def calculate_vars(self, direction: int = 1) -> dict:
        return {"value": self.data.latest}


class TestWindowNullNormalization:
    """测试 window=None 的归一化行为"""

    def test_window_none_normalized_to_zero(self):
        """测试 window=None 被归一化为 0.0"""
        indicator = DummyIndicator(name="test", window=None)
        assert indicator._window == 0.0

    def test_window_zero_stays_zero(self):
        """测试 window=0 保持为 0.0"""
        indicator = DummyIndicator(name="test", window=0)
        assert indicator._window == 0.0

    def test_window_none_and_zero_equivalent_data_storage(self):
        """测试 window=None 和 window=0 的数据存储行为等价"""
        indicator_none = DummyIndicator(name="test_none", window=None)
        indicator_zero = DummyIndicator(name="test_zero", window=0)

        # 两者的 _window 应该相同
        assert indicator_none._window == indicator_zero._window
        assert indicator_none._window == 0.0

    def test_window_none_is_ready_no_error(self):
        """测试 window=None 时 is_ready() 不会抛出 TypeError"""
        indicator = DummyIndicator(name="test", window=None)

        # 添加一些数据
        indicator._data.append(time.time(), 1.0)

        # is_ready() 应该不抛出异常
        try:
            result = indicator.is_ready()
            # window=0 时，没有 ready_condition 默认为 True
            assert result is True
        except TypeError as e:
            pytest.fail(f"is_ready() raised TypeError with window=None: {e}")

    def test_window_none_and_zero_equivalent_is_ready(self):
        """测试 window=None 和 window=0 的 is_ready() 行为等价"""
        indicator_none = DummyIndicator(name="test_none", window=None)
        indicator_zero = DummyIndicator(name="test_zero", window=0)

        # 添加相同的数据
        ts = time.time()
        indicator_none._data.append(ts, 1.0)
        indicator_zero._data.append(ts, 1.0)

        # 两者的 is_ready() 结果应该相同
        assert indicator_none.is_ready() == indicator_zero.is_ready()

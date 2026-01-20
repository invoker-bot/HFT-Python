"""
IndicatorGroup 单元测试

Feature 0006: Indicator 与 DataSource 统一架构
"""
# pylint: disable=protected-access
import time
from typing import Any

import pytest

from hft.indicator.base import BaseIndicator, GlobalIndicator
from hft.indicator.group import (
    IndicatorGroup,
    TradingPairIndicators,
    GlobalIndicators,
)


class SimpleTestIndicator(BaseIndicator[float]):
    """测试用的简单指标"""

    def calculate_vars(self, direction: int) -> dict[str, Any]:
        if not self._data:
            return {"value": 0.0}
        return {"value": self._data.latest}


class SimpleGlobalIndicator(GlobalIndicator[float]):
    """测试用的全局指标"""

    def calculate_vars(self, direction: int) -> dict[str, Any]:
        return {"global_value": 42.0}


class TestTradingPairIndicators:
    """TradingPairIndicators 测试"""

    def test_init(self):
        """测试初始化"""
        pair = TradingPairIndicators("okx", "BTC/USDT:USDT")

        assert pair.exchange_class == "okx"
        assert pair.symbol == "BTC/USDT:USDT"
        assert pair.name == "okx:BTC/USDT:USDT"

    def test_register_indicator(self):
        """测试注册指标"""
        pair = TradingPairIndicators("okx", "BTC/USDT:USDT")
        indicator = SimpleTestIndicator(name="test_ind")

        pair.register_indicator("test_ind", indicator)

        assert pair.has_indicator("test_ind")
        assert pair.get_indicator("test_ind") is indicator

    def test_get_nonexistent_indicator(self):
        """测试获取不存在的指标"""
        pair = TradingPairIndicators("okx", "BTC/USDT:USDT")

        assert pair.get_indicator("nonexistent") is None
        assert pair.has_indicator("nonexistent") is False


class TestGlobalIndicators:
    """GlobalIndicators 测试"""

    def test_init(self):
        """测试初始化"""
        global_inds = GlobalIndicators()
        assert global_inds.name == "GlobalIndicators"

    def test_register_indicator(self):
        """测试注册全局指标"""
        global_inds = GlobalIndicators()
        indicator = SimpleGlobalIndicator(name="global_test")

        global_inds.register_indicator("global_test", indicator)

        assert global_inds.has_indicator("global_test")
        assert global_inds.get_indicator("global_test") is indicator


class TestIndicatorGroup:
    """IndicatorGroup 测试"""

    def test_init(self):
        """测试初始化"""
        group = IndicatorGroup()
        assert group.name == "IndicatorGroup"

    def test_register_factory(self):
        """测试注册工厂"""
        group = IndicatorGroup()

        def factory(exchange_class, symbol):
            return SimpleTestIndicator(name=f"test_{exchange_class}_{symbol}")

        group.register_factory("test_indicator", factory)

        assert "test_indicator" in group._indicator_factories

    def test_get_indicator_creates_pair_indicators(self):
        """测试 get_indicator 自动创建 TradingPairIndicators"""
        group = IndicatorGroup()

        def factory(exchange_class, symbol):
            ind = SimpleTestIndicator(name=f"test_{exchange_class}_{symbol}")
            ind._data.append(time.time(), 100.0)  # 添加数据使其 ready
            return ind

        group.register_factory("test_ind", factory)

        # 获取指标
        indicator = group.get_indicator("test_ind", "okx", "BTC/USDT:USDT")

        assert indicator is not None
        assert ("okx", "BTC/USDT:USDT") in group._local_indicators

    def test_get_indicator_global(self):
        """测试获取全局指标"""
        group = IndicatorGroup()

        def factory(exchange_class, symbol):
            return SimpleGlobalIndicator(name="global_test")

        group.register_factory("global_test", factory)

        # 获取全局指标
        indicator = group.get_indicator("global_test", None, None)

        assert indicator is not None
        assert group._global_indicators.has_indicator("global_test")

    def test_query_indicator_returns_none_when_not_ready(self):
        """测试 query_indicator 在未 ready 时返回 None"""
        group = IndicatorGroup()

        def factory(exchange_class, symbol):
            # 不添加数据，所以不 ready
            return SimpleTestIndicator(name="not_ready")

        group.register_factory("not_ready", factory)

        # query 返回 None（未 ready）
        result = group.query_indicator("not_ready", "okx", "BTC/USDT:USDT")
        assert result is None

        # get 仍然返回实例
        indicator = group.get_indicator("not_ready", "okx", "BTC/USDT:USDT")
        assert indicator is not None

    def test_query_indicator_returns_indicator_when_ready(self):
        """测试 query_indicator 在 ready 时返回指标"""
        group = IndicatorGroup()

        def factory(exchange_class, symbol):
            ind = SimpleTestIndicator(name="ready_ind")
            ind._data.append(time.time(), 100.0)  # 添加数据使其 ready
            return ind

        group.register_factory("ready_ind", factory)

        # query 返回指标（已 ready）
        result = group.query_indicator("ready_ind", "okx", "BTC/USDT:USDT")
        assert result is not None
        assert result.is_ready()

    def test_get_indicator_touches(self):
        """测试 get_indicator 会 touch 指标"""
        group = IndicatorGroup()

        def factory(exchange_class, symbol):
            ind = SimpleTestIndicator(name="touch_test", expire_seconds=1.0)
            return ind

        group.register_factory("touch_test", factory)

        # 第一次获取
        indicator = group.get_indicator("touch_test", "okx", "BTC/USDT:USDT")
        first_touch = indicator._last_touch

        time.sleep(0.01)

        # 第二次获取应该更新 touch 时间
        indicator2 = group.get_indicator("touch_test", "okx", "BTC/USDT:USDT")
        assert indicator2 is indicator
        assert indicator._last_touch > first_touch

    def test_no_factory_returns_none(self):
        """测试无工厂时返回 None"""
        group = IndicatorGroup()

        result = group.get_indicator("nonexistent", "okx", "BTC/USDT:USDT")
        assert result is None

    def test_get_stats(self):
        """测试统计信息"""
        group = IndicatorGroup()

        def factory(exchange_class, symbol):
            ind = SimpleTestIndicator(name=f"test_{symbol}")
            return ind

        group.register_factory("test", factory)

        # 创建几个指标
        group.get_indicator("test", "okx", "BTC/USDT:USDT")
        group.get_indicator("test", "okx", "ETH/USDT:USDT")

        stats = group.get_stats()

        assert stats["local_indicators"]["trading_pairs"] == 2
        assert "okx" in stats["local_indicators"]["by_exchange"]


# ============================================================
# 回归测试（Issue 0003）
# ============================================================

class TestRegressionIssue0003:
    """Issue 0003 回归测试：防止 Feature 0006 缺陷复现"""

    @pytest.mark.asyncio
    async def test_global_indicators_not_removed_after_tick(self):
        """
        回归测试：IndicatorGroup.tick 后 GlobalIndicators 不会被移除

        Issue 0003 P0: sync_children_params() 必须包含 GlobalIndicators，
        否则 GroupListener._sync_children() 会将其移除。
        """
        group = IndicatorGroup()

        # 注册一个全局指标工厂
        def factory(exchange_class, symbol):
            return SimpleGlobalIndicator(name="global_test")

        group.register_factory("global_test", factory)

        # 获取全局指标（触发创建）
        indicator = group.get_indicator("global_test", None, None)
        assert indicator is not None
        assert group._global_indicators.has_indicator("global_test")

        # 模拟 tick（会调用 _sync_children）
        await group._sync_children()

        # 验证 GlobalIndicators 仍然存在且是子节点
        assert "GlobalIndicators" in group.children
        assert group.children["GlobalIndicators"] is group._global_indicators

        # 验证全局指标仍然存在
        assert group._global_indicators.has_indicator("global_test")

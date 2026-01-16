"""
BaseIndicator 单元测试

Feature 0006: Indicator 与 DataSource 统一架构
"""
import time
import pytest
import asyncio
from typing import Any, Optional

from hft.indicator.base import (
    BaseIndicator,
    GlobalIndicator,
    BaseDataSource,
    DEFAULT_EXPIRE_SECONDS,
    GLOBAL_EXPIRE_SECONDS,
)


class SimpleTestIndicator(BaseIndicator[float]):
    """测试用的简单指标"""

    def calculate_vars(self, direction: int) -> dict[str, Any]:
        if not self._data:
            return {"value": 0.0}
        return {
            "value": self._data.latest,
            "direction": direction,
        }


class TestBaseIndicatorInit:
    """BaseIndicator 初始化测试"""

    def test_default_init(self):
        """测试默认初始化"""
        indicator = SimpleTestIndicator(name="test")

        assert indicator.name == "test"
        assert indicator.window == 300.0
        assert indicator._ready_condition is None
        assert indicator._expire_seconds == DEFAULT_EXPIRE_SECONDS
        assert indicator.interval is None  # 事件驱动

    def test_custom_init(self):
        """测试自定义参数初始化"""
        indicator = SimpleTestIndicator(
            name="custom",
            window=600.0,
            ready_condition="timeout < 30",
            expire_seconds=100.0,
            interval=1.0,
        )

        assert indicator.name == "custom"
        assert indicator.window == 600.0
        assert indicator._ready_condition == "timeout < 30"
        assert indicator._expire_seconds == 100.0
        assert indicator.interval == 1.0


class TestBaseIndicatorData:
    """BaseIndicator 数据操作测试"""

    def test_data_append(self):
        """测试数据添加"""
        indicator = SimpleTestIndicator(name="test")
        now = time.time()

        indicator._data.append(now, 100.0)
        indicator._data.append(now + 1, 200.0)

        assert len(indicator._data) == 2
        assert indicator._data.latest == 200.0
        assert indicator.cache_size == 2

    def test_calculate_vars(self):
        """测试 calculate_vars"""
        indicator = SimpleTestIndicator(name="test")
        now = time.time()

        indicator._data.append(now, 42.0)

        vars_buy = indicator.calculate_vars(direction=1)
        assert vars_buy["value"] == 42.0
        assert vars_buy["direction"] == 1

        vars_sell = indicator.calculate_vars(direction=-1)
        assert vars_sell["direction"] == -1


class TestBaseIndicatorReady:
    """BaseIndicator ready 判断测试"""

    def test_is_ready_no_condition(self):
        """测试无条件时的 ready 判断"""
        indicator = SimpleTestIndicator(name="test")

        # 无数据时不 ready
        assert indicator.is_ready() is False

        # 有数据时 ready
        indicator._data.append(time.time(), 100.0)
        assert indicator.is_ready() is True

    def test_is_ready_with_timeout_condition(self):
        """测试 timeout 条件"""
        indicator = SimpleTestIndicator(
            name="test",
            ready_condition="timeout < 60",
        )

        # 无数据时 timeout=inf，不满足条件
        assert indicator.is_ready() is False

        # 新鲜数据满足条件
        indicator._data.append(time.time(), 100.0)
        assert indicator.is_ready() is True

        # 旧数据不满足条件
        indicator._data.clear()
        indicator._data.append(time.time() - 120, 100.0)
        assert indicator.is_ready() is False

    def test_is_ready_no_window(self):
        """测试无 window 时 cv=0, range=1"""
        indicator = SimpleTestIndicator(
            name="test",
            window=0,  # 无 window
            ready_condition="cv < 0.5 and range > 0.5",
        )

        # 无 window 时 cv=0, range=1，条件满足
        indicator._data.append(time.time(), 100.0)
        assert indicator.is_ready() is True


class TestBaseIndicatorExpire:
    """BaseIndicator 过期机制测试"""

    def test_touch(self):
        """测试 touch 更新"""
        indicator = SimpleTestIndicator(name="test", expire_seconds=10.0)

        old_touch = indicator._last_touch
        time.sleep(0.01)
        indicator.touch()

        assert indicator._last_touch > old_touch

    def test_is_expired(self):
        """测试过期判断"""
        indicator = SimpleTestIndicator(name="test", expire_seconds=0.1)

        # 刚创建不过期
        assert indicator.is_expired() is False

        # 等待过期
        time.sleep(0.15)
        assert indicator.is_expired() is True

        # touch 后不过期
        indicator.touch()
        assert indicator.is_expired() is False


class TestBaseIndicatorEvents:
    """BaseIndicator 事件机制测试"""

    def test_on_emit(self):
        """测试事件注册和发射"""
        indicator = SimpleTestIndicator(name="test")
        received = []

        indicator.on("update", lambda ts, val: received.append((ts, val)))

        now = time.time()
        indicator.emit("update", now, 100.0)

        assert len(received) == 1
        assert received[0] == (now, 100.0)

    def test_emit_update_triggers_ready(self):
        """测试 _emit_update 触发 ready 事件"""
        indicator = SimpleTestIndicator(name="test")
        ready_triggered = []

        indicator.on("ready", lambda: ready_triggered.append(True))

        # 第一次 update，从 not ready 变为 ready
        now = time.time()
        indicator._data.append(now, 100.0)
        indicator._emit_update(now, 100.0)

        assert len(ready_triggered) == 1

        # 第二次 update，已经 ready，不再触发
        indicator._data.append(now + 1, 200.0)
        indicator._emit_update(now + 1, 200.0)

        assert len(ready_triggered) == 1  # 仍然是 1


class TestGlobalIndicator:
    """GlobalIndicator 测试"""

    def test_default_expire_seconds(self):
        """测试默认过期时间"""

        class TestGlobal(GlobalIndicator[float]):
            def calculate_vars(self, direction: int) -> dict[str, Any]:
                return {}

        indicator = TestGlobal(name="global_test")
        assert indicator._expire_seconds == GLOBAL_EXPIRE_SECONDS


class TestBaseDataSource:
    """BaseDataSource 测试"""

    def test_mode_property(self):
        """测试 mode 属性"""

        class TestDataSource(BaseDataSource[float]):
            async def _watch(self) -> None:
                pass

            async def _fetch(self) -> None:
                pass

            def calculate_vars(self, direction: int) -> dict[str, Any]:
                return {}

        ds_watch = TestDataSource(
            name="watch_ds",
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            mode="watch",
        )
        assert ds_watch.mode == "watch"
        assert ds_watch.exchange_class == "okx"
        assert ds_watch.symbol == "BTC/USDT:USDT"

        ds_fetch = TestDataSource(
            name="fetch_ds",
            exchange_class="binance",
            symbol="ETH/USDT:USDT",
            mode="fetch",
        )
        assert ds_fetch.mode == "fetch"
        assert ds_fetch.exchange_class == "binance"
        assert ds_fetch.symbol == "ETH/USDT:USDT"


# ============================================================
# 回归测试（Issue 0003）
# ============================================================

class TestRegressionIssue0003:
    """Issue 0003 回归测试"""

    @pytest.mark.asyncio
    async def test_on_stop_handles_watch_task_exception(self):
        """
        回归测试：BaseDataSource.on_stop 在 watch_task 异常退出时不抛异常

        Issue 0003 P1: on_stop 必须捕获所有异常，确保 stop 链路干净。
        """
        class FailingDataSource(BaseDataSource[float]):
            async def _watch(self) -> None:
                raise RuntimeError("Simulated watch failure")

            async def _fetch(self) -> None:
                pass

            def calculate_vars(self, direction: int) -> dict[str, Any]:
                return {}

        ds = FailingDataSource(
            name="failing_ds",
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            mode="watch",
        )

        # 启动（会创建 watch task）
        await ds.on_start()

        # 等待 watch task 失败
        await asyncio.sleep(0.1)

        # on_stop 不应抛出异常
        await ds.on_stop()

        # 验证 watch_task 已清理
        assert ds._watch_task is None

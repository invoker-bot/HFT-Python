"""
测试 HealthyData 和 HealthyDataArray 模块

覆盖：
- HealthyData 基本健康/不健康状态
- HealthyData 超时过期
- HealthyData get / get_or_raise / mark_dirty / update
- HealthyData pickle 序列化/反序列化
- HealthyData get_or_update_by_func 缓存与更新逻辑
- HealthyDataArray append / get / len / 索引 / 迭代
- HealthyDataArray 重复时间戳去重策略
- HealthyDataArray shrink 清理旧数据
- HealthyDataArray assign / clear
- HealthyDataArray 健康状态判断
- 并发安全：coalesce fetch、无死锁
"""
import asyncio
import pickle
import time

import pytest

from hft.core.healthy_data import (
    HealthyData,
    HealthyDataArray,
    UnhealthyDataError,
    always_duplicate,
    never_duplicate,
)


# ---------------------------------------------------------------------------
# HealthyData 基础测试
# ---------------------------------------------------------------------------

class TestHealthyDataBasic:
    """HealthyData 基本行为"""

    def test_初始状态不健康(self):
        """新建实例没有数据，应为不健康"""
        hd = HealthyData(max_age=10.0)
        assert hd.is_healthy is False
        assert bool(hd) is False

    async def test_更新后变健康(self):
        hd = HealthyData(max_age=10.0)
        await hd.update(42)
        assert hd.is_healthy is True
        assert bool(hd) is True

    async def test_超过max_age后不健康(self):
        """数据年龄超过 max_age 后应变为不健康"""
        hd = HealthyData(max_age=0.01)
        await hd.update("hello")
        assert hd.is_healthy is True
        await asyncio.sleep(0.02)
        assert hd.is_healthy is False

    async def test_get_返回数据和时间戳(self):
        hd = HealthyData(max_age=10.0)
        t_before = time.time()
        await hd.update({"price": 100})
        data, ts = hd.get()
        assert data == {"price": 100}
        assert ts >= t_before

    async def test_get_or_raise_健康时返回数据(self):
        hd = HealthyData(max_age=10.0)
        await hd.update(99)
        data, ts = await hd.get_or_raise()
        assert data == 99
        assert ts > 0

    async def test_get_or_raise_不健康时抛异常(self):
        hd = HealthyData(max_age=10.0)
        with pytest.raises(UnhealthyDataError):
            await hd.get_or_raise()

    async def test_mark_dirty_使数据不健康(self):
        hd = HealthyData(max_age=10.0)
        await hd.update("fresh")
        assert hd.is_healthy is True
        await hd.mark_dirty()
        assert hd.is_healthy is False

    async def test_update_后清除dirty标记(self):
        hd = HealthyData(max_age=10.0)
        await hd.update("fresh")
        await hd.mark_dirty()
        assert hd.is_healthy is False
        await hd.update("refreshed")
        assert hd.is_healthy is True

    async def test_is_stale_为is_healthy的反值(self):
        hd = HealthyData(max_age=10.0)
        assert hd.is_stale() is True  # 不健康 => stale
        await hd.update(1)
        assert hd.is_stale() is False  # 健康 => not stale

    def test_get_初始时返回None和零时间戳(self):
        hd = HealthyData(max_age=10.0)
        data, ts = hd.get()
        assert data is None
        assert ts == 0.0


# ---------------------------------------------------------------------------
# HealthyData pickle 测试
# ---------------------------------------------------------------------------

class TestHealthyDataPickle:
    """HealthyData pickle 序列化/反序列化"""

    async def test_pickle_保留数据(self):
        hd = HealthyData(max_age=10.0)
        await hd.update({"key": "value"})
        serialized = pickle.dumps(hd)
        restored: HealthyData = pickle.loads(serialized)
        data, _ = restored.get()
        assert data == {"key": "value"}

    async def test_pickle_重建锁对象(self):
        """反序列化后 _data_lock 应是新的 asyncio.Lock"""
        hd = HealthyData(max_age=10.0)
        await hd.update(123)
        serialized = pickle.dumps(hd)
        restored: HealthyData = pickle.loads(serialized)
        assert isinstance(restored._data_lock, asyncio.Lock)
        assert isinstance(restored._update_data_lock, asyncio.Lock)

    async def test_pickle_后仍可正常使用(self):
        hd = HealthyData(max_age=10.0)
        await hd.update(7)
        restored: HealthyData = pickle.loads(pickle.dumps(hd))
        # 数据应仍健康（max_age 足够大）
        assert restored.is_healthy is True
        data, _ = await restored.get_or_raise()
        assert data == 7


# ---------------------------------------------------------------------------
# HealthyData get_or_update_by_func 测试
# ---------------------------------------------------------------------------

class TestGetOrUpdateByFunc:
    """get_or_update_by_func 缓存与回退逻辑"""

    async def test_健康时不调用func(self):
        hd = HealthyData(max_age=10.0)
        await hd.update(100)

        call_count = 0

        async def fetch():
            nonlocal call_count
            call_count += 1
            return 200, time.time()

        result, _ = await hd.get_or_update_by_func(fetch)
        assert result == 100  # 返回缓存值
        assert call_count == 0  # func 未被调用

    async def test_不健康时调用func(self):
        hd = HealthyData(max_age=10.0)

        call_count = 0

        async def fetch():
            nonlocal call_count
            call_count += 1
            return 999, time.time()

        result, _ = await hd.get_or_update_by_func(fetch)
        assert result == 999
        assert call_count == 1

    async def test_func失败时抛UnhealthyDataError(self):
        hd = HealthyData(max_age=10.0)

        async def failing_fetch():
            raise RuntimeError("网络异常")

        with pytest.raises(UnhealthyDataError):
            await hd.get_or_update_by_func(failing_fetch)


# ---------------------------------------------------------------------------
# HealthyDataArray 基础测试
# ---------------------------------------------------------------------------

class TestHealthyDataArrayBasic:
    """HealthyDataArray 基本操作"""

    async def test_append_并get返回最新(self):
        hda = HealthyDataArray(max_age=10, window=60)
        t = time.time()
        await hda.append("a", t)
        await hda.append("b", t + 1)
        data, ts = hda.get()
        assert data == "b"
        assert ts == t + 1

    async def test_len(self):
        hda = HealthyDataArray(max_age=10, window=60)
        t = time.time()
        for i in range(5):
            await hda.append(i, t + i)
        assert len(hda) == 5

    async def test_索引访问返回值(self):
        hda = HealthyDataArray(max_age=10, window=60)
        t = time.time()
        await hda.append(10, t)
        await hda.append(20, t + 1)
        assert hda[0] == 10
        assert hda[1] == 20

    async def test_迭代返回值序列(self):
        hda = HealthyDataArray(max_age=10, window=60)
        t = time.time()
        for i in range(3):
            await hda.append(i * 10, t + i)
        values = list(hda)
        assert values == [0, 10, 20]

    def test_空数组get返回None和零(self):
        hda = HealthyDataArray(max_age=10, window=60)
        data, ts = hda.get()
        assert data is None
        assert ts == 0.0


# ---------------------------------------------------------------------------
# HealthyDataArray 去重策略测试
# ---------------------------------------------------------------------------

class TestHealthyDataArrayDuplicate:
    """时间戳去重行为"""

    async def test_always_duplicate_覆盖相同时间戳(self):
        """默认策略：同时间戳（容差内）用新值覆盖旧值"""
        hda = HealthyDataArray(max_age=10, window=60, duplicate_timestamp_delta=0.1)
        t = time.time()
        await hda.append("old", t, duplicate_value_fn=always_duplicate)
        await hda.append("new", t, duplicate_value_fn=always_duplicate)
        assert len(hda) == 1
        assert hda[0] == "new"

    async def test_never_duplicate_保留相同时间戳的两条(self):
        """never_duplicate：即使时间戳相同也不覆盖"""
        hda = HealthyDataArray(max_age=10, window=60, duplicate_timestamp_delta=0.1)
        t = time.time()
        await hda.append("first", t, duplicate_value_fn=never_duplicate)
        await hda.append("second", t, duplicate_value_fn=never_duplicate)
        assert len(hda) == 2


# ---------------------------------------------------------------------------
# HealthyDataArray shrink 测试
# ---------------------------------------------------------------------------

class TestHealthyDataArrayShrink:
    """shrink 清理过期数据"""

    async def test_shrink_清理过旧数据(self):
        """
        当时间跨度超过 (2 + _random_rate) * window 时触发 shrink。
        设置 _random_rate = 0 使 shrink 阈值 = 2 * window = 2s。

        为使 shrink 真正删除数据，旧点的时间戳必须早于 time.time() - window，
        因此以 "过去" 时刻为基准插入 5 个点（跨度 4s，超过阈值 2s），
        最终 bisect_left 应找到 cut > 0 并截断。
        """
        hda = HealthyDataArray(max_age=10, window=1)
        hda._random_rate = 0  # 固定随机率，使 shrink 阈值为 2 * window

        # 以 10s 前为起点，插入 5 个点，时间跨度 4s > 2 * 1 = 2s，触发 shrink
        # 最旧点在 now-10，最新点在 now-6；cut = bisect_left(now - 1) => 全部 5 点都旧
        base = time.time() - 10
        for i in range(5):
            await hda.append(f"v{i}", base + i)

        # shrink 后旧数据应被截断，保留点数 < 5
        assert len(hda) < 5


# ---------------------------------------------------------------------------
# HealthyDataArray assign / clear 测试
# ---------------------------------------------------------------------------

class TestHealthyDataArrayAssignClear:

    async def test_assign_替换全部数据(self):
        hda = HealthyDataArray(max_age=10, window=60)
        t = time.time()
        await hda.append("old", t)
        new_points = [("x", t + 1), ("y", t + 2)]
        await hda.assign(new_points)
        assert len(hda) == 2
        assert hda[0] == "x"
        assert hda[1] == "y"

    async def test_clear_清空数组(self):
        hda = HealthyDataArray(max_age=10, window=60)
        t = time.time()
        for i in range(3):
            await hda.append(i, t + i)
        hda.clear()
        assert len(hda) == 0
        data, _ = hda.get()
        assert data is None


# ---------------------------------------------------------------------------
# HealthyDataArray 健康状态测试
# ---------------------------------------------------------------------------

class TestHealthyDataArrayHealth:
    """is_healthy 综合判断"""

    def test_空数组不健康(self):
        hda = HealthyDataArray(max_age=10, window=60)
        assert hda.is_healthy is False

    async def test_覆盖范围不足时不健康(self):
        """
        healthy_range=0.25 表示实际覆盖 > 0.25 * healthy_window 才健康。
        插入 3 个点但时间跨度极小（远小于 healthy_window），is_range_healthy 应为 False。
        """
        hda = HealthyDataArray(
            max_age=10,
            window=60,
            healthy_window=60,
            healthy_points=3,
            healthy_range=0.25,
        )
        t = time.time()
        # 3 个点，时间跨度 0.001s << 0.25 * 60 = 15s
        for i in range(3):
            await hda.append(i, t + i * 0.001)
        assert hda.is_healthy is False

    async def test_足够数据点和覆盖范围时健康(self):
        """数据点足够、时间覆盖足够、数据新鲜 => is_healthy True"""
        hda = HealthyDataArray(
            max_age=10,
            window=60,
            healthy_window=5,
            healthy_points=3,
            healthy_range=0.25,
        )
        t = time.time()
        # 3 个点，跨度 4s > 0.25 * 5 = 1.25s
        for i in range(3):
            await hda.append(i, t - 4 + i * 2)
        # 最新点在 t，满足 max_age=10
        assert hda.is_healthy is True

    async def test_脏标记使数组不健康(self):
        hda = HealthyDataArray(
            max_age=10,
            window=60,
            healthy_window=5,
            healthy_points=3,
            healthy_range=0.25,
        )
        t = time.time()
        for i in range(3):
            await hda.append(i, t - 4 + i * 2)
        assert hda.is_healthy is True
        await hda.mark_dirty()
        assert hda.is_healthy is False

    async def test_超过max_age后不健康(self):
        hda = HealthyDataArray(
            max_age=0.01,
            window=60,
            healthy_window=5,
            healthy_points=3,
            healthy_range=0.25,
        )
        t = time.time()
        for i in range(3):
            await hda.append(i, t - 4 + i * 2)
        assert hda.is_healthy is True
        await asyncio.sleep(0.02)
        assert hda.is_healthy is False


# ---------------------------------------------------------------------------
# 并发安全测试
# ---------------------------------------------------------------------------

class TestConcurrency:
    """并发场景下的安全性"""

    async def test_并发get_or_update_by_func合并fetch调用(self):
        """
        多个协程同时发现数据不健康并发起 get_or_update_by_func，
        双重检查锁应确保实际 fetch 调用次数 <= 2（第一次及竞争）。
        """
        hd = HealthyData(max_age=10.0)
        call_count = 0

        async def slow_fetch():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)
            return "result", time.time()

        # 10 个协程同时请求
        tasks = [hd.get_or_update_by_func(slow_fetch) for _ in range(10)]
        results = await asyncio.gather(*tasks)

        # 所有请求应成功返回相同结果
        assert all(r[0] == "result" for r in results)
        # 合并效果：实际 fetch 调用应远少于 10（上界为 2：第一组竞争最多两次）
        assert call_count <= 2

    async def test_并发update_和get_or_raise不死锁(self):
        """update 与 get_or_raise 并发执行不应死锁"""
        hd = HealthyData(max_age=10.0)
        await hd.update(0)

        async def updater():
            for i in range(20):
                await hd.update(i)
                await asyncio.sleep(0)

        async def reader():
            for _ in range(20):
                try:
                    await hd.get_or_raise()
                except UnhealthyDataError:
                    pass
                await asyncio.sleep(0)

        # 5s 超时内必须完成，否则视为死锁
        await asyncio.wait_for(
            asyncio.gather(updater(), reader()),
            timeout=5.0,
        )

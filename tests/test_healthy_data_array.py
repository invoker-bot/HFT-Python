"""
HealthyDataArray 单元测试
"""
import time
import pytest
from hft.core.healthy_data import HealthyDataArray


class TestHealthyDataArrayBasic:
    """基本功能测试"""

    def test_init(self):
        """测试初始化"""
        arr = HealthyDataArray[float](max_seconds=300)
        assert len(arr) == 0
        assert arr.latest is None
        assert arr.latest_timestamp == 0.0

    def test_append_single(self):
        """测试单个添加"""
        arr = HealthyDataArray[float](max_seconds=300)
        now = time.time()
        arr.append(now, 100.0)

        assert len(arr) == 1
        assert arr.latest == 100.0
        assert arr.latest_timestamp == now

    def test_append_order(self):
        """测试添加顺序（保持时间戳升序）"""
        arr = HealthyDataArray[float](max_seconds=300)
        now = time.time()

        # 乱序添加
        arr.append(now, 2.0)
        arr.append(now - 10, 1.0)
        arr.append(now + 10, 3.0)

        # 应该按时间戳排序
        values = list(arr)
        assert values == [1.0, 2.0, 3.0]

    def test_index_access(self):
        """测试索引访问"""
        arr = HealthyDataArray[float](max_seconds=300)
        now = time.time()

        arr.append(now, 1.0)
        arr.append(now + 1, 2.0)
        arr.append(now + 2, 3.0)

        assert arr[0] == 1.0
        assert arr[1] == 2.0
        assert arr[2] == 3.0
        assert arr[-1] == 3.0


class TestHealthyDataArrayDuplicate:
    """去重功能测试"""

    def test_duplicate_same_timestamp(self):
        """测试相同时间戳去重（覆盖旧值）"""
        arr = HealthyDataArray[float](max_seconds=300)
        now = time.time()

        arr.append(now, 1.0)
        arr.append(now, 2.0)  # 应该覆盖旧值

        assert len(arr) == 1
        assert arr.latest == 2.0  # 新值覆盖旧值

    def test_duplicate_within_tolerance(self):
        """测试容差范围内去重（覆盖旧值）"""
        arr = HealthyDataArray[float](max_seconds=300, duplicate_tolerance=0.1)
        now = time.time()

        arr.append(now, 1.0)
        arr.append(now + 0.05, 2.0)  # 在容差内，应该覆盖旧值

        assert len(arr) == 1
        assert arr.latest == 2.0  # 新值覆盖旧值

    def test_duplicate_outside_tolerance(self):
        """测试容差范围外不去重"""
        arr = HealthyDataArray[float](max_seconds=300, duplicate_tolerance=0.01)
        now = time.time()

        arr.append(now, 1.0)
        arr.append(now + 0.1, 2.0)  # 超出容差，应该保留

        assert len(arr) == 2

    def test_custom_duplicate_fn(self):
        """测试自定义去重函数"""
        # 只有值相同才视为重复
        arr = HealthyDataArray[float](
            max_seconds=300,
            duplicate_tolerance=0.1,
            is_duplicate_fn=lambda x, y: x == y,
        )
        now = time.time()

        arr.append(now, 1.0)
        arr.append(now + 0.05, 1.0)  # 值相同，去重
        arr.append(now + 0.05, 2.0)  # 值不同，保留

        assert len(arr) == 2


class TestHealthyDataArrayShrink:
    """时间窗口清理测试"""

    def test_shrink_old_data(self):
        """测试清理旧数据"""
        arr = HealthyDataArray[float](max_seconds=100)
        now = time.time()

        # 添加旧数据
        arr.append(now - 200, 1.0)
        arr.append(now - 150, 2.0)
        # 添加新数据，触发清理
        arr.append(now, 3.0)

        # 旧数据应该被清理
        assert len(arr) == 1
        assert arr.latest == 3.0

    def test_shrink_keeps_recent(self):
        """测试保留近期数据"""
        arr = HealthyDataArray[float](max_seconds=100)
        now = time.time()

        arr.append(now - 50, 1.0)
        arr.append(now - 25, 2.0)
        arr.append(now, 3.0)

        # 都在窗口内，应该全部保留
        assert len(arr) == 3


class TestHealthyDataArrayMetrics:
    """健康指标测试"""

    def test_timeout(self):
        """测试 timeout 计算"""
        arr = HealthyDataArray[float](max_seconds=300)
        now = time.time()

        arr.append(now - 10, 1.0)
        timeout = arr.timeout

        # timeout 应该约等于 10 秒
        assert 9.9 < timeout < 10.5

    def test_timeout_empty(self):
        """测试空数组的 timeout"""
        arr = HealthyDataArray[float](max_seconds=300)
        assert arr.timeout == float('inf')

    def test_get_cv_uniform(self):
        """测试均匀采样的 CV"""
        arr = HealthyDataArray[float](max_seconds=300)
        now = time.time()

        # 均匀间隔添加
        for i in range(10):
            arr.append(now + i * 10, float(i))

        cv = arr.get_cv(now, now + 100)
        # 均匀采样，CV 应该接近 0
        assert cv < 0.1

    def test_get_cv_non_uniform(self):
        """测试不均匀采样的 CV"""
        arr = HealthyDataArray[float](max_seconds=300)
        now = time.time()

        # 不均匀间隔
        arr.append(now, 1.0)
        arr.append(now + 1, 2.0)
        arr.append(now + 2, 3.0)
        arr.append(now + 50, 4.0)  # 大间隔
        arr.append(now + 51, 5.0)

        cv = arr.get_cv(now, now + 60)
        # 不均匀采样，CV 应该较大
        assert cv > 0.5

    def test_get_cv_insufficient_data(self):
        """测试数据不足时的 CV"""
        arr = HealthyDataArray[float](max_seconds=300)
        now = time.time()

        arr.append(now, 1.0)
        arr.append(now + 1, 2.0)

        # 数据点不足，返回极端值 100.0
        cv = arr.get_cv(now, now + 10, min_points=5)
        assert cv == 100.0

    def test_get_range_full(self):
        """测试完整覆盖的 range"""
        arr = HealthyDataArray[float](max_seconds=300)
        now = time.time()

        arr.append(now, 1.0)
        arr.append(now + 25, 2.0)
        arr.append(now + 50, 3.0)
        arr.append(now + 75, 4.0)
        arr.append(now + 100, 5.0)

        range_val = arr.get_range(now, now + 100)
        # 完整覆盖，range 应该接近 1.0
        assert 0.9 < range_val <= 1.0

    def test_get_range_partial(self):
        """测试部分覆盖的 range"""
        arr = HealthyDataArray[float](max_seconds=300)
        now = time.time()

        # 添加更多数据点以满足 min_points 要求
        for i in range(10):
            arr.append(now + 25 + i * 8, float(i))

        # 查询 0-100，但数据只覆盖 25-97
        range_val = arr.get_range(now, now + 100)
        assert 0.7 < range_val < 0.8

    def test_get_range_insufficient_data(self):
        """测试数据不足时的 range"""
        arr = HealthyDataArray[float](max_seconds=300)
        now = time.time()

        arr.append(now, 1.0)
        arr.append(now + 1, 2.0)

        # 数据点不足，返回极端值 0.0
        range_val = arr.get_range(now, now + 100, min_points=5)
        assert range_val == 0.0


class TestHealthyDataArrayIsHealthy:
    """健康判断测试"""

    def test_is_healthy_true(self):
        """测试健康状态"""
        arr = HealthyDataArray[float](max_seconds=300)
        now = time.time()

        # 均匀添加足够数据
        for i in range(20):
            arr.append(now - 100 + i * 5, float(i))

        is_healthy = arr.is_healthy(
            start_timestamp=now - 100,
            end_timestamp=now,
            timeout_threshold=60,
            cv_threshold=0.8,
            range_threshold=0.6,
        )
        assert is_healthy is True

    def test_is_healthy_timeout_fail(self):
        """测试超时导致不健康"""
        arr = HealthyDataArray[float](max_seconds=300)
        now = time.time()

        # 数据太旧
        for i in range(10):
            arr.append(now - 200 + i * 5, float(i))

        is_healthy = arr.is_healthy(
            start_timestamp=now - 200,
            end_timestamp=now - 100,
            timeout_threshold=60,  # 超时阈值 60s，但数据已经 150s 前
        )
        assert is_healthy is False

    def test_is_healthy_cv_fail(self):
        """测试 CV 过高导致不健康"""
        arr = HealthyDataArray[float](max_seconds=300)
        now = time.time()

        # 极不均匀的采样
        arr.append(now - 100, 1.0)
        arr.append(now - 99, 2.0)
        arr.append(now - 98, 3.0)
        arr.append(now - 97, 4.0)
        arr.append(now - 10, 5.0)  # 大间隔
        arr.append(now, 6.0)

        is_healthy = arr.is_healthy(
            start_timestamp=now - 100,
            end_timestamp=now,
            cv_threshold=0.3,  # 严格的 CV 阈值
        )
        assert is_healthy is False

    def test_is_healthy_range_fail(self):
        """测试覆盖不足导致不健康"""
        arr = HealthyDataArray[float](max_seconds=300)
        now = time.time()

        # 只覆盖一小部分
        for i in range(5):
            arr.append(now - 10 + i, float(i))

        is_healthy = arr.is_healthy(
            start_timestamp=now - 100,
            end_timestamp=now,
            range_threshold=0.5,  # 需要 50% 覆盖
        )
        assert is_healthy is False


class TestHealthyDataArrayIteration:
    """迭代功能测试"""

    def test_iter(self):
        """测试迭代"""
        arr = HealthyDataArray[float](max_seconds=300)
        now = time.time()

        arr.append(now, 1.0)
        arr.append(now + 1, 2.0)
        arr.append(now + 2, 3.0)

        values = list(arr)
        assert values == [1.0, 2.0, 3.0]

    def test_items(self):
        """测试 items 迭代"""
        arr = HealthyDataArray[float](max_seconds=300)
        now = time.time()

        arr.append(now, 1.0)
        arr.append(now + 1, 2.0)

        items = list(arr.items())
        assert len(items) == 2
        assert items[0] == (now, 1.0)
        assert items[1] == (now + 1, 2.0)

    def test_bool(self):
        """测试布尔转换"""
        arr = HealthyDataArray[float](max_seconds=300)
        assert not arr

        arr.append(time.time(), 1.0)
        assert arr

    def test_clear(self):
        """测试清空"""
        arr = HealthyDataArray[float](max_seconds=300)
        arr.append(time.time(), 1.0)
        arr.append(time.time(), 2.0)

        arr.clear()
        assert len(arr) == 0
        assert not arr


# ============================================================
# 回归测试（Issue 0003）
# ============================================================

class TestRegressionIssue0003:
    """Issue 0003 回归测试"""

    def test_timeout_non_negative_when_future_timestamp(self):
        """
        回归测试：HealthyDataArray.timeout 在 latest_timestamp 超前本机时间时不为负

        Issue 0003 P2: 交易所时间可能超前本机时间，导致 timeout 为负值。
        修复后应使用 max(0.0, ...) 确保非负。
        """
        arr = HealthyDataArray[float](max_seconds=300)

        # 模拟交易所时间超前本机 10 秒
        future_timestamp = time.time() + 10
        arr.append(future_timestamp, 100.0)

        timeout = arr.timeout

        # timeout 应该为 0，而非负值
        assert timeout >= 0.0
        assert timeout == 0.0  # 超前时应该返回 0


class TestHealthyDataArrayAssign:
    """assign() 方法测试"""

    def test_assign_empty(self):
        """测试 assign 空列表"""
        arr = HealthyDataArray[float](max_seconds=300)
        arr.append(100.0, 1.0)
        arr.assign([])
        assert len(arr) == 0

    def test_assign_basic(self):
        """测试基本 assign 功能"""
        arr = HealthyDataArray[float](max_seconds=300)
        now = time.time()
        points = [(now - 10, 1.0), (now - 5, 2.0), (now, 3.0)]
        arr.assign(points)
        assert len(arr) == 3
        assert arr.latest == 3.0

    def test_assign_unsorted(self):
        """测试 assign 自动排序"""
        arr = HealthyDataArray[float](max_seconds=300)
        now = time.time()
        # 乱序输入
        points = [(now, 3.0), (now - 10, 1.0), (now - 5, 2.0)]
        arr.assign(points)
        values = list(arr)
        assert values == [1.0, 2.0, 3.0]

    def test_assign_dedup(self):
        """测试 assign 去重"""
        arr = HealthyDataArray[float](max_seconds=300)
        now = time.time()
        # 同时间戳的数据，后者覆盖前者
        points = [(now, 1.0), (now, 2.0)]
        arr.assign(points)
        assert len(arr) == 1
        assert arr.latest == 2.0

    def test_assign_shrink(self):
        """测试 assign 自动清理超窗数据"""
        arr = HealthyDataArray[float](max_seconds=100)
        now = time.time()
        # 包含超窗数据
        points = [(now - 200, 1.0), (now - 50, 2.0), (now, 3.0)]
        arr.assign(points)
        # 超窗数据应被清理
        assert len(arr) == 2
        values = list(arr)
        assert values == [2.0, 3.0]

    def test_assign_replaces_existing(self):
        """测试 assign 替换现有数据"""
        arr = HealthyDataArray[float](max_seconds=300)
        now = time.time()
        arr.append(now - 10, 100.0)
        arr.append(now - 5, 200.0)
        assert len(arr) == 2

        # assign 完全替换
        new_points = [(now - 3, 1.0), (now, 2.0)]
        arr.assign(new_points)
        assert len(arr) == 2
        values = list(arr)
        assert values == [1.0, 2.0]

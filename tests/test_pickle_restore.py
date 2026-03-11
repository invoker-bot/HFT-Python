"""
Pickle 序列化与恢复测试

覆盖：
- Listener pickle round-trip：排除字段、锁重建、状态保持
- HealthyData pickle round-trip：数据保留、锁重建
- HealthyDataArray pickle round-trip：数据列表保留、锁重建
- ActiveOrdersTracker pickle 行为
- Listener 恢复后的状态转换
- 复杂对象图（父子树）的 pickle 行为
"""
# pylint: disable=protected-access
import asyncio
import pickle
import time

import pytest

from hft.core.listener import Listener, ListenerState
from hft.core.healthy_data import HealthyData, HealthyDataArray
from hft.executor.base import ActiveOrdersTracker, ActiveOrder


# ============================================================
# 辅助类（模块级别，可被 pickle）
# ============================================================

class StatefulListener(Listener):
    """带自定义状态的 Listener 子类，用于 pickle 测试"""

    def __init__(self, name="StatefulListener", interval=1.0, **kwargs):
        self.counter = 0
        self.data = {"key": "value"}
        self.items = [1, 2, 3]
        super().__init__(name=name, interval=interval, **kwargs)

    async def on_tick(self) -> bool:
        self.counter += 1
        return False


class ChildListener(Listener):
    """子监听器，用于测试父子树 pickle"""

    def __init__(self, name="ChildListener", interval=1.0, **kwargs):
        self.child_data = "child_value"
        super().__init__(name=name, interval=interval, **kwargs)

    async def on_tick(self) -> bool:
        return False


class CustomSaveReloadListener(Listener):
    """测试 on_save/on_reload 钩子的 Listener（必须在模块级别定义才能 pickle）"""

    def __init__(self):
        self.reload_called = False
        self.extra = None
        super().__init__(name="custom")

    def on_save(self):
        return {"extra_save_data": 42}

    def on_reload(self, state):
        self.reload_called = True
        self.extra = state.get("extra_save_data")

    async def on_tick(self) -> bool:
        return False


# ============================================================
# Listener pickle round-trip
# ============================================================

class TestListenerPickleRoundTrip:
    """Listener pickle 序列化与反序列化"""

    def test_getstate_排除不可序列化字段(self):
        """__getstate__ 应排除 __pickle_exclude__ 中的所有字段"""
        listener = StatefulListener()
        state = listener.__getstate__()

        excluded_keys = Listener.__pickle_exclude__
        for key in excluded_keys:
            assert key not in state, f"排除字段 {key} 不应出现在序列化状态中"

    def test_getstate_保留业务状态(self):
        """__getstate__ 应保留不在排除列表中的业务字段"""
        listener = StatefulListener()
        listener.counter = 42
        listener.data = {"test": True}

        state = listener.__getstate__()

        assert state["counter"] == 42
        assert state["data"] == {"test": True}
        assert state["items"] == [1, 2, 3]

    def test_pickle_dumps_loads_基本验证(self):
        """Listener 应能成功 pickle/unpickle"""
        listener = StatefulListener()
        listener.counter = 10

        data = pickle.dumps(listener)
        restored = pickle.loads(data)

        assert isinstance(restored, StatefulListener)
        assert restored.counter == 10
        assert restored.data == {"key": "value"}
        assert restored.items == [1, 2, 3]

    def test_pickle_恢复后锁被重建(self):
        """unpickle 后 _alock 和 _ulock 应被重建为新的 asyncio.Lock"""
        listener = StatefulListener()
        data = pickle.dumps(listener)
        restored = pickle.loads(data)

        assert isinstance(restored._alock, asyncio.Lock)
        assert isinstance(restored._ulock, asyncio.Lock)
        # 确保是新的锁对象（不是同一个实例）
        assert restored._alock is not listener._alock

    def test_pickle_恢复后background_task为None(self):
        """unpickle 后 _background_task 应为 None"""
        listener = StatefulListener()
        data = pickle.dumps(listener)
        restored = pickle.loads(data)

        assert restored._background_task is None

    def test_pickle_恢复后state为STOPPED(self):
        """unpickle 后 _state 应为 STOPPED（因为 _state 在排除列表中，
        initialize() 会将其重置为 STOPPED）"""
        listener = StatefulListener()
        # 手动设置为非 STOPPED 状态模拟运行中
        listener._state = ListenerState.RUNNING

        data = pickle.dumps(listener)
        restored = pickle.loads(data)

        # _state 在 __pickle_exclude__ 中，恢复后被 initialize() 重置为 STOPPED
        assert restored._state == ListenerState.STOPPED
        assert restored.state == ListenerState.STOPPED

    def test_pickle_enabled状态被保留(self):
        """_enabled 不在排除列表中，应被保留"""
        listener = StatefulListener()
        listener._enabled = False

        data = pickle.dumps(listener)
        restored = pickle.loads(data)

        assert restored._enabled is False
        assert restored.enabled is False

    def test_pickle_healthy状态被保留(self):
        """_healthy 不在排除列表中，应被保留"""
        listener = StatefulListener()
        listener._healthy = True

        data = pickle.dumps(listener)
        restored = pickle.loads(data)

        assert restored._healthy is True

    def test_pickle_children不被保留(self):
        """_children 在排除列表中，恢复后应为空字典"""
        parent = StatefulListener(name="parent")
        child = ChildListener(name="child", parent=parent)
        assert len(parent.children) == 1

        data = pickle.dumps(parent)
        restored = pickle.loads(data)

        assert len(restored.children) == 0
        assert restored._children == {}

    def test_pickle_parent不被保留(self):
        """_parent 在排除列表中（弱引用不可序列化），恢复后应为 None"""
        parent = StatefulListener(name="parent")
        child = ChildListener(name="child", parent=parent)
        assert child.parent is parent

        data = pickle.dumps(child)
        restored = pickle.loads(data)

        assert restored.parent is None
        assert restored._parent is None

    def test_pickle_name被保留(self):
        """name 不在排除列表中，应被保留"""
        listener = StatefulListener(name="my_listener")

        data = pickle.dumps(listener)
        restored = pickle.loads(data)

        assert restored.name == "my_listener"

    def test_pickle_interval被保留(self):
        """_interval 不在排除列表中，应被保留"""
        listener = StatefulListener(interval=5.0)

        data = pickle.dumps(listener)
        restored = pickle.loads(data)

        assert restored._interval == 5.0

    def test_pickle_cache_time被保留(self):
        """cache_time 应出现在序列化状态中（__getstate__ 显式添加）"""
        listener = StatefulListener()
        state = listener.__getstate__()
        assert "cache_time" in state


# ============================================================
# HealthyData pickle round-trip（补充 test_healthy_data.py 中的基础测试）
# ============================================================

class TestHealthyDataPickleRestore:
    """HealthyData pickle 更详细的恢复验证"""

    async def test_pickle_dict类型数据保留(self):
        """HealthyData[dict] 数据应在 pickle round-trip 后保留"""
        hd = HealthyData[dict](max_age=60.0)
        await hd.update({"price": 100.5, "volume": 1000})

        restored = pickle.loads(pickle.dumps(hd))

        data, ts = restored.get()
        assert data == {"price": 100.5, "volume": 1000}
        assert ts > 0

    async def test_pickle_max_age被保留(self):
        """max_age 应在 pickle round-trip 后保留"""
        hd = HealthyData(max_age=42.0)
        await hd.update("test")

        restored = pickle.loads(pickle.dumps(hd))
        assert restored.max_age == 42.0

    async def test_pickle_dirty标记被保留(self):
        """_dirty 标记应在 pickle round-trip 后保留"""
        hd = HealthyData(max_age=60.0)
        await hd.update("test")
        await hd.mark_dirty()

        restored = pickle.loads(pickle.dumps(hd))
        assert restored._dirty is True
        assert restored.is_healthy is False

    async def test_pickle_恢复后锁可正常使用(self):
        """反序列化后的锁应可正常用于 async 操作"""
        hd = HealthyData(max_age=60.0)
        await hd.update({"val": 1})

        restored = pickle.loads(pickle.dumps(hd))

        # 验证锁可以正常获取和释放
        async with restored._data_lock:
            pass
        async with restored._update_data_lock:
            pass

        # 验证可以继续更新
        await restored.update({"val": 2})
        data, _ = restored.get()
        assert data == {"val": 2}

    async def test_pickle_空数据round_trip(self):
        """空的 HealthyData 也应能正常 pickle/unpickle"""
        hd = HealthyData(max_age=10.0)

        restored = pickle.loads(pickle.dumps(hd))

        assert restored.is_healthy is False
        data, ts = restored.get()
        assert data is None
        assert ts == 0.0


# ============================================================
# HealthyDataArray pickle round-trip
# ============================================================

class TestHealthyDataArrayPickleRestore:
    """HealthyDataArray pickle 序列化与反序列化"""

    async def test_pickle_多数据点保留(self):
        """HealthyDataArray 的 _data_list 应在 pickle 后保留"""
        now = time.time()
        hda = HealthyDataArray(max_age=60.0, window=300)
        await hda.append(10.0, now - 2)
        await hda.append(20.0, now - 1)
        await hda.append(30.0, now)

        restored = pickle.loads(pickle.dumps(hda))

        assert len(restored) == 3
        assert restored[0] == 10.0
        assert restored[1] == 20.0
        assert restored[2] == 30.0

    async def test_pickle_data_list时间戳保留(self):
        """data_list 中的时间戳应在 pickle 后精确保留"""
        now = time.time()
        ts1, ts2 = now - 10, now - 5
        hda = HealthyDataArray(max_age=60.0, window=300)
        await hda.append("a", ts1)
        await hda.append("b", ts2)

        restored = pickle.loads(pickle.dumps(hda))

        assert len(restored.data_list) == 2
        assert restored.data_list[0] == ("a", ts1)
        assert restored.data_list[1] == ("b", ts2)

    async def test_pickle_恢复后锁被重建(self):
        """反序列化后 _data_lock 和 _update_data_lock 应是新的 asyncio.Lock"""
        hda = HealthyDataArray(max_age=60.0, window=300)
        await hda.append(1.0)

        restored = pickle.loads(pickle.dumps(hda))

        assert isinstance(restored._data_lock, asyncio.Lock)
        assert isinstance(restored._update_data_lock, asyncio.Lock)

    async def test_pickle_恢复后可继续追加(self):
        """反序列化后应能继续 append 数据"""
        hda = HealthyDataArray(max_age=60.0, window=300)
        await hda.append(1.0, time.time() - 1)

        restored = pickle.loads(pickle.dumps(hda))
        await restored.append(2.0, time.time())

        assert len(restored) == 2
        assert restored[1] == 2.0

    async def test_pickle_配置参数保留(self):
        """window、healthy_points 等配置参数应在 pickle 后保留"""
        hda = HealthyDataArray(
            max_age=30.0,
            window=120,
            healthy_points=5,
            healthy_cv=0.8,
            healthy_range=0.3,
        )

        restored = pickle.loads(pickle.dumps(hda))

        assert restored.max_age == 30.0
        assert restored.window == 120
        assert restored._healthy_points == 5
        assert restored._healthy_cv == 0.8
        assert restored._healthy_range == 0.3

    async def test_pickle_空数组round_trip(self):
        """空的 HealthyDataArray 也应能正常 pickle/unpickle"""
        hda = HealthyDataArray(max_age=10.0, window=60)

        restored = pickle.loads(pickle.dumps(hda))

        assert len(restored) == 0
        data, ts = restored.get()
        assert data is None
        assert ts == 0.0


# ============================================================
# ActiveOrdersTracker pickle round-trip
# ============================================================

class TestActiveOrdersTrackerPickle:
    """ActiveOrdersTracker pickle 行为测试

    ActiveOrdersTracker 包含 asyncio.Lock 和 defaultdict。
    在某些 Python 版本中 asyncio.Lock 可以被 pickle，
    因此这里测试手动排除 _lock 后 orders 数据能否保留。
    """

    def test_tracker手动排除lock后可以pickle(self):
        """手动排除 _lock 后，orders 数据应能保留"""
        tracker = ActiveOrdersTracker()
        tracker.add_active_orders("exchange1", "BTC/USDT", [
            ActiveOrder(
                order_id="order1",
                exchange_path="exchange1",
                symbol="BTC/USDT",
                price=50000.0,
                amount=1.0,
                created_at=time.time(),
                timeout_refresh_tolerance=30.0,
            )
        ])
        # 模拟手动序列化（排除锁）
        state = {k: v for k, v in tracker.__dict__.items() if k != '_lock'}
        data = pickle.dumps(state)
        restored_state = pickle.loads(data)

        assert "order1" in restored_state["orders"]["exchange1"]["BTC/USDT"]

    def test_tracker含多个订单的数据保留(self):
        """多个交易对的订单数据在序列化后均应保留"""
        tracker = ActiveOrdersTracker()
        now = time.time()
        tracker.add_active_orders("ex1", "BTC/USDT", [
            ActiveOrder("o1", "ex1", "BTC/USDT", 50000.0, 1.0, now, 30.0),
            ActiveOrder("o2", "ex1", "BTC/USDT", 49000.0, -0.5, now, 30.0),
        ])
        tracker.add_active_orders("ex2", "ETH/USDT", [
            ActiveOrder("o3", "ex2", "ETH/USDT", 3000.0, 2.0, now, 30.0),
        ])

        state = {k: v for k, v in tracker.__dict__.items() if k != '_lock'}
        restored_state = pickle.loads(pickle.dumps(state))

        assert "o1" in restored_state["orders"]["ex1"]["BTC/USDT"]
        assert "o2" in restored_state["orders"]["ex1"]["BTC/USDT"]
        assert "o3" in restored_state["orders"]["ex2"]["ETH/USDT"]


# ============================================================
# Listener 恢复后的状态转换
# ============================================================

class TestListenerStateTransitionsAfterRestore:
    """Listener 恢复后的状态转换"""

    def test_运行中listener_pickle后状态重置为STOPPED(self):
        """RUNNING 状态的 Listener pickle 后应恢复为 STOPPED"""
        listener = StatefulListener()
        listener._state = ListenerState.RUNNING

        restored = pickle.loads(pickle.dumps(listener))
        assert restored.state == ListenerState.STOPPED

    async def test_恢复后可正常启动(self):
        """恢复后的 Listener 应能正常 start"""
        listener = StatefulListener()
        listener._state = ListenerState.RUNNING

        restored = pickle.loads(pickle.dumps(listener))
        assert restored.state == ListenerState.STOPPED

        await restored.start(recursive=False)
        assert restored.state == ListenerState.STARTING

    async def test_恢复后可正常tick(self):
        """恢复后的 Listener 应能正常执行 tick (STARTING -> RUNNING)"""
        listener = StatefulListener()

        restored = pickle.loads(pickle.dumps(listener))

        await restored.start(recursive=False)
        assert restored.state == ListenerState.STARTING

        await restored.tick()
        assert restored.state == ListenerState.RUNNING

    async def test_恢复后可正常停止(self):
        """恢复后的 Listener 应能正常 start -> tick -> stop 完整生命周期"""
        listener = StatefulListener()

        restored = pickle.loads(pickle.dumps(listener))

        await restored.start(recursive=False)
        await restored.tick()
        assert restored.state == ListenerState.RUNNING

        await restored.stop(recursive=False)
        assert restored.state == ListenerState.STOPPED

    def test_finished标志被保留(self):
        """finished 标志不在 __pickle_exclude__ 中，应被保留"""
        listener = StatefulListener()
        listener.finished = True

        restored = pickle.loads(pickle.dumps(listener))
        assert restored.finished is True

    async def test_恢复后finished的listener不会重启(self):
        """finished=True 的 Listener 恢复后 start 不应进入 STARTING"""
        listener = StatefulListener()
        listener.finished = True

        restored = pickle.loads(pickle.dumps(listener))
        assert restored.finished is True

        await restored.start(recursive=False)
        # finished 为 True 时，start 内部不会改变 state
        assert restored.state == ListenerState.STOPPED

    async def test_恢复后tick更新counter(self):
        """恢复后 tick 应能正常执行业务逻辑（counter 递增）"""
        listener = StatefulListener()
        listener.counter = 5

        restored = pickle.loads(pickle.dumps(listener))
        assert restored.counter == 5

        await restored.start(recursive=False)
        await restored.tick()  # STARTING -> RUNNING (calls on_start, not on_tick)
        await restored.tick()  # RUNNING -> on_tick -> counter += 1
        assert restored.counter == 6


# ============================================================
# 复杂对象图（父子树）
# ============================================================

class TestComplexObjectGraphPickle:
    """测试 Listener 树结构的 pickle 行为"""

    def test_父节点pickle不包含子节点(self):
        """pickle 父节点时不应包含子节点（_children 在排除列表中）"""
        parent = StatefulListener(name="parent")
        child1 = ChildListener(name="child1", parent=parent)
        child2 = ChildListener(name="child2", parent=parent)

        assert len(parent.children) == 2

        restored_parent = pickle.loads(pickle.dumps(parent))

        # 子节点不应被保留
        assert len(restored_parent.children) == 0
        # 父节点自身数据应保留
        assert restored_parent.name == "parent"
        assert restored_parent.counter == 0

    def test_子节点pickle不包含父引用(self):
        """pickle 子节点时不应包含对父节点的引用"""
        parent = StatefulListener(name="parent")
        child = ChildListener(name="child", parent=parent)

        restored_child = pickle.loads(pickle.dumps(child))

        assert restored_child.parent is None
        assert restored_child.child_data == "child_value"

    def test_恢复后可重建树(self):
        """恢复后应能重新建立父子关系"""
        parent = StatefulListener(name="parent")
        child = ChildListener(name="child", parent=parent)

        # 分别 pickle
        restored_parent = pickle.loads(pickle.dumps(parent))
        restored_child = pickle.loads(pickle.dumps(child))

        # 重建父子关系
        restored_parent.add_child(restored_child)

        assert len(restored_parent.children) == 1
        assert restored_child.parent is restored_parent
        assert restored_parent.children["child"] is restored_child

    def test_深层树pickle后各层独立(self):
        """三层树结构中，pickle 任一节点都不会包含其他层"""
        root = StatefulListener(name="root")
        mid = ChildListener(name="mid", parent=root)
        leaf = ChildListener(name="leaf", parent=mid)

        assert len(root.children) == 1
        assert len(mid.children) == 1

        restored_root = pickle.loads(pickle.dumps(root))
        restored_mid = pickle.loads(pickle.dumps(mid))
        restored_leaf = pickle.loads(pickle.dumps(leaf))

        assert len(restored_root.children) == 0
        assert len(restored_mid.children) == 0
        assert restored_root.parent is None
        assert restored_mid.parent is None
        assert restored_leaf.parent is None

    def test_多次pickle_unpickle稳定(self):
        """多次序列化/反序列化不应丢失数据或引入异常"""
        listener = StatefulListener(name="stable")
        listener.counter = 99
        listener.data = {"nested": {"deep": True}}

        for _ in range(5):
            listener = pickle.loads(pickle.dumps(listener))

        assert listener.name == "stable"
        assert listener.counter == 99
        assert listener.data == {"nested": {"deep": True}}
        assert listener.state == ListenerState.STOPPED
        assert isinstance(listener._alock, asyncio.Lock)

    def test_on_save和on_reload钩子(self):
        """on_save 返回的数据应被包含在序列化状态中，on_reload 应被调用"""
        listener = CustomSaveReloadListener()
        state = listener.__getstate__()
        assert state["extra_save_data"] == 42

        restored = pickle.loads(pickle.dumps(listener))
        assert restored.reload_called is True
        assert restored.extra == 42

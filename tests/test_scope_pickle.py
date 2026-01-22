"""
Scope 系统 Pickle 序列化测试

测试内容：
1. BaseScope 的 pickle 序列化/反序列化
2. 条件变量的状态保存和恢复
3. functions 和普通 vars 通过 initialize() 重建
4. 各种 Scope 类型的 pickle 支持
"""
import pickle
import time
from hft.core.scope import BaseScope, VirtualMachine
from hft.core.scope.scopes import (
    GlobalScope,
    ExchangeClassScope,
    ExchangeScope,
    TradingPairClassScope,
    TradingPairScope,
    TradingPairClassGroupScope,
)


class TestBaseScopePickle:
    """BaseScope pickle 测试"""

    def test_pickle_basic_scope(self):
        """测试基本 Scope 的 pickle"""
        scope = BaseScope("test", "test_instance")
        scope.set_var("x", 10)
        scope.set_var("y", 20)

        # 序列化
        pickled = pickle.dumps(scope)

        # 反序列化
        restored = pickle.loads(pickled)

        # 验证基本属性
        assert restored.scope_class_id == "test"
        assert restored.scope_instance_id == "test_instance"
        assert restored.get_var("instance_id") == "test_instance"
        assert restored.get_var("class_id") == "test"

    def test_pickle_conditional_vars(self):
        """测试条件变量的 pickle"""
        vm = VirtualMachine()
        scope = BaseScope("test", "test_instance")

        scope.set_var("position", 0)
        scope.set_var("mid_price", 100.5)

        # 执行条件变量赋值
        vm.execute([
            {
                "name": "entry_price",
                "value": "mid_price",
                "on": "position == 0",
                "initial_value": None
            }
        ], scope)

        # 验证条件变量已设置
        assert scope.get_var("entry_price") == 100.5
        assert scope.get_var("__entry_price_last_update_time") is not None

        # 序列化
        pickled = pickle.dumps(scope)

        # 反序列化
        restored = pickle.loads(pickled)

        # 验证条件变量被保存
        assert restored.get_var("entry_price") == 100.5
        assert restored.get_var("__entry_price_last_update_time") is not None

    def test_pickle_does_not_save_normal_vars(self):
        """测试普通变量不被 pickle 保存"""
        scope = BaseScope("test", "test_instance")
        scope.set_var("normal_var", 123)

        # 序列化
        pickled = pickle.dumps(scope)

        # 反序列化
        restored = pickle.loads(pickled)

        # 普通变量不应该被保存（除了 instance_id, class_id, app_core）
        assert restored.get_var("normal_var") is None


class TestGlobalScopePickle:
    """GlobalScope pickle 测试"""

    def test_pickle_global_scope(self):
        """测试 GlobalScope 的 pickle"""
        scope = GlobalScope("global", "global")

        # 验证 functions 存在
        assert scope.get_function("min") is not None
        assert scope.get_function("clip") is not None

        # 序列化
        pickled = pickle.dumps(scope)

        # 反序列化
        restored = pickle.loads(pickled)

        # 验证 functions 通过 initialize() 重建
        assert restored.get_function("min") is not None
        assert restored.get_function("max") is not None
        assert restored.get_function("clip") is not None
        assert restored.get_function("avg") is not None

        # 测试 functions 功能正常
        clip_func = restored.get_function("clip")
        assert clip_func(5, 0, 10) == 5
        assert clip_func(-5, 0, 10) == 0

    def test_pickle_global_scope_with_conditional_vars(self):
        """测试 GlobalScope 带条件变量的 pickle"""
        vm = VirtualMachine()
        scope = GlobalScope("global", "global")

        scope.set_var("rsi", 25)
        scope.set_var("mid_price", 100.0)

        # 执行条件变量赋值
        vm.execute([
            {
                "name": "center_price",
                "value": "mid_price",
                "on": "rsi < 30 or rsi > 70",
                "initial_value": 100.0
            }
        ], scope)

        # 序列化
        pickled = pickle.dumps(scope)

        # 反序列化
        restored = pickle.loads(pickled)

        # 验证条件变量被保存
        assert restored.get_var("center_price") == 100.0

        # 验证 functions 被重建
        assert restored.get_function("clip") is not None


class TestExchangeScopePickle:
    """ExchangeScope pickle 测试"""

    def test_pickle_exchange_class_scope(self):
        """测试 ExchangeClassScope 的 pickle"""
        scope = ExchangeClassScope("exchange_class", "okx")

        # 验证变量存在
        assert scope.get_var("exchange_class") == "okx"

        # 序列化
        pickled = pickle.dumps(scope)

        # 反序列化
        restored = pickle.loads(pickled)

        # 验证变量通过 initialize() 重建
        assert restored.get_var("exchange_class") == "okx"

    def test_pickle_exchange_scope(self):
        """测试 ExchangeScope 的 pickle"""
        scope = ExchangeScope("exchange", "okx/main")

        # 验证变量存在
        assert scope.get_var("exchange_id") == "okx/main"
        assert scope.get_var("exchange_path") == "okx/main"

        # 序列化
        pickled = pickle.dumps(scope)

        # 反序列化
        restored = pickle.loads(pickled)

        # 验证变量通过 initialize() 重建
        assert restored.get_var("exchange_id") == "okx/main"
        assert restored.get_var("exchange_path") == "okx/main"


class TestTradingPairScopePickle:
    """TradingPairScope pickle 测试"""

    def test_pickle_trading_pair_class_scope(self):
        """测试 TradingPairClassScope 的 pickle"""
        scope = TradingPairClassScope("trading_pair_class", "okx-ETH/USDT")

        # 验证变量存在
        assert scope.get_var("symbol") == "ETH/USDT"

        # 序列化
        pickled = pickle.dumps(scope)

        # 反序列化
        restored = pickle.loads(pickled)

        # 验证变量通过 initialize() 重建
        assert restored.get_var("symbol") == "ETH/USDT"

    def test_pickle_trading_pair_scope(self):
        """测试 TradingPairScope 的 pickle"""
        scope = TradingPairScope("trading_pair", "okx/main-ETH/USDT")

        # 验证变量存在
        assert scope.get_var("exchange_id") == "okx/main"
        assert scope.get_var("symbol") == "ETH/USDT"

        # 序列化
        pickled = pickle.dumps(scope)

        # 反序列化
        restored = pickle.loads(pickled)

        # 验证变量通过 initialize() 重建
        assert restored.get_var("exchange_id") == "okx/main"
        assert restored.get_var("exchange_path") == "okx/main"
        assert restored.get_var("symbol") == "ETH/USDT"

    def test_pickle_trading_pair_class_group_scope(self):
        """测试 TradingPairClassGroupScope 的 pickle"""
        scope = TradingPairClassGroupScope("group", "ETH")

        # 验证变量存在
        assert scope.get_var("group_id") == "ETH"

        # 序列化
        pickled = pickle.dumps(scope)

        # 反序列化
        restored = pickle.loads(pickled)

        # 验证变量通过 initialize() 重建
        assert restored.get_var("group_id") == "ETH"


class TestPickleWithConditionalVars:
    """条件变量 pickle 综合测试"""

    def test_pickle_preserves_conditional_state(self):
        """测试 pickle 保存条件变量状态"""
        vm = VirtualMachine()
        scope = GlobalScope("global", "global")

        scope.set_var("position", 0)
        scope.set_var("mid_price", 100.0)

        # 第一次执行：条件满足
        vm.execute([
            {
                "name": "entry_price",
                "value": "mid_price",
                "on": "position == 0",
                "initial_value": None
            }
        ], scope)

        entry_price_before = scope.get_var("entry_price")
        timestamp_before = scope.get_var("__entry_price_last_update_time")

        # 序列化
        pickled = pickle.dumps(scope)

        # 等待一小段时间
        time.sleep(0.1)

        # 反序列化
        restored = pickle.loads(pickled)

        # 验证条件变量值和时间戳被保存
        assert restored.get_var("entry_price") == entry_price_before
        assert restored.get_var("__entry_price_last_update_time") == timestamp_before

    def test_pickle_duration_calculation_after_restore(self):
        """测试反序列化后 duration 计算正确"""
        vm = VirtualMachine()
        scope = GlobalScope("global", "global")

        scope.set_var("rsi", 25)
        scope.set_var("mid_price", 100.0)

        # 执行条件变量赋值
        vm.execute([
            {
                "name": "center_price",
                "value": "mid_price",
                "on": "rsi < 30 or rsi > 70 or duration > 0.5",
                "initial_value": 100.0
            }
        ], scope)

        # 序列化
        pickled = pickle.dumps(scope)

        # 等待一段时间
        time.sleep(0.6)

        # 反序列化
        restored = pickle.loads(pickled)

        # 修改条件使其不满足（但 duration > 0.5 应该满足）
        restored.set_var("rsi", 50)
        restored.set_var("mid_price", 105.0)

        # 再次执行
        vm.execute([
            {
                "name": "center_price",
                "value": "mid_price",
                "on": "rsi < 30 or rsi > 70 or duration > 0.5",
                "initial_value": 100.0
            }
        ], restored)

        # duration > 0.5，条件满足，应该更新
        assert restored.get_var("center_price") == 105.0

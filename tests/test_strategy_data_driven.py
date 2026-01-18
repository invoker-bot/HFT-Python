"""
Feature 0008: Strategy 数据驱动增强 - 单元测试

测试内容：
1. TargetDefinition 配置类
2. VarDefinition / ConditionalVarDefinition 配置类
3. StaticPositionsStrategy 新格式支持
4. 表达式求值
5. 多 Exchange 目标匹配
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from hft.strategy.config import (
    VarDefinition,
    ConditionalVarDefinition,
    TargetDefinition,
)
from hft.strategy.static_positions import (
    StaticPositionsStrategy,
    StaticPositionsStrategyConfig,
)
from hft.strategy.base import TargetPositions, StrategyOutput


class TestVarDefinition:
    """VarDefinition 配置类测试"""

    def test_basic_creation(self):
        """测试基本创建"""
        var = VarDefinition(name="test_var", value="mid_price * 0.5")
        assert var.name == "test_var"
        assert var.value == "mid_price * 0.5"

    def test_required_fields(self):
        """测试必填字段"""
        with pytest.raises(Exception):
            VarDefinition(name="test")  # 缺少 value

        with pytest.raises(Exception):
            VarDefinition(value="expr")  # 缺少 name


class TestConditionalVarDefinition:
    """ConditionalVarDefinition 配置类测试"""

    def test_basic_creation(self):
        """测试基本创建"""
        var = ConditionalVarDefinition(
            value="mid_price",
            on="rsi < 30",
            default=100.0,
        )
        assert var.value == "mid_price"
        assert var.on == "rsi < 30"
        assert var.default == 100.0

    def test_default_value_none(self):
        """测试默认值为 None"""
        var = ConditionalVarDefinition(
            value="mid_price",
            on="rsi < 30",
        )
        assert var.default is None


class TestTargetDefinition:
    """TargetDefinition 配置类测试"""

    def test_basic_creation(self):
        """测试基本创建"""
        target = TargetDefinition(
            symbol="BTC/USDT:USDT",
            position_usd="1000",
        )
        assert target.symbol == "BTC/USDT:USDT"
        assert target.position_usd == "1000"
        assert target.exchange == "*"  # 默认值
        assert target.exchange_class == "*"  # 默认值
        assert target.speed == 0.5  # 默认值

    def test_with_exchange_filter(self):
        """测试 exchange 过滤"""
        target = TargetDefinition(
            exchange="okx/*",
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
            position_usd="1000",
        )
        assert target.exchange == "okx/*"
        assert target.exchange_class == "okx"

    def test_expression_fields(self):
        """测试表达式字段"""
        target = TargetDefinition(
            symbol="BTC/USDT:USDT",
            position_usd="0.6 * equation_usd",
            position_amount="base_amount + delta",
            max_position_usd="0.8 * equation_usd",
        )
        assert target.position_usd == "0.6 * equation_usd"
        assert target.position_amount == "base_amount + delta"
        assert target.max_position_usd == "0.8 * equation_usd"

    def test_extra_fields(self):
        """测试额外字段（通过 model_extra）"""
        target = TargetDefinition(
            symbol="BTC/USDT:USDT",
            position_usd="1000",
            custom_field="custom_value",
        )
        assert target.symbol == "BTC/USDT:USDT"
        assert target.model_extra.get("custom_field") == "custom_value"


class TestStaticPositionsStrategyConfig:
    """StaticPositionsStrategyConfig 测试"""

    def test_legacy_format(self):
        """测试旧格式配置"""
        config = StaticPositionsStrategyConfig(
            name="test",
            exchange_path="okx/main",
            positions_usd={"BTC/USDT:USDT": 1000},
            speed=0.8,
        )
        assert config.exchange_path == "okx/main"
        assert config.positions_usd == {"BTC/USDT:USDT": 1000}
        assert config.speed == 0.8
        assert config.targets == []  # 默认空列表

    def test_new_format(self):
        """测试新格式配置"""
        config = StaticPositionsStrategyConfig(
            name="test",
            targets=[
                TargetDefinition(
                    symbol="BTC/USDT:USDT",
                    position_usd="1000",
                ),
            ],
        )
        assert len(config.targets) == 1
        assert config.targets[0].symbol == "BTC/USDT:USDT"

    def test_with_requires(self):
        """测试 requires 配置"""
        config = StaticPositionsStrategyConfig(
            name="test",
            requires=["equation", "rsi"],
            targets=[],
        )
        assert config.requires == ["equation", "rsi"]

    def test_with_vars(self):
        """测试 vars 配置"""
        config = StaticPositionsStrategyConfig(
            name="test",
            vars=[
                VarDefinition(name="ratio", value="0.6"),
            ],
            targets=[],
        )
        assert len(config.vars) == 1
        assert config.vars[0].name == "ratio"

    def test_with_vars(self):
        """测试 vars 配置"""
        config = StaticPositionsStrategyConfig(
            name="test",
            vars={
                "center_price": ConditionalVarDefinition(
                    value="mid_price",
                    on="rsi < 30",
                ),
            },
            targets=[],
        )
        assert "center_price" in config.vars
        assert config.vars["center_price"].value == "mid_price"


class TestStaticPositionsStrategyTargetMatching:
    """StaticPositionsStrategy 目标匹配测试"""

    @pytest.fixture
    def mock_strategy(self):
        """创建 mock strategy"""
        config = StaticPositionsStrategyConfig(
            name="test",
            targets=[
                TargetDefinition(
                    exchange="*",
                    exchange_class="okx",
                    symbol="BTC/USDT:USDT",
                    position_usd="1000",
                ),
            ],
        )
        strategy = StaticPositionsStrategy(config)
        return strategy

    @pytest.fixture
    def mock_exchange_okx(self):
        """创建 mock OKX exchange"""
        exchange = MagicMock()
        exchange.config.path = "okx/main"
        exchange.class_name = "okx"
        exchange.ready = True
        return exchange

    @pytest.fixture
    def mock_exchange_binance(self):
        """创建 mock Binance exchange"""
        exchange = MagicMock()
        exchange.config.path = "binance/main"
        exchange.class_name = "binance"
        exchange.ready = True
        return exchange

    def test_match_wildcard_exchange(self, mock_strategy, mock_exchange_okx, mock_exchange_binance):
        """测试通配符 exchange 匹配"""
        # Setup
        mock_strategy._get_all_exchanges = MagicMock(
            return_value=[mock_exchange_okx, mock_exchange_binance]
        )

        target = TargetDefinition(
            exchange="*",
            exchange_class="*",
            symbol="BTC/USDT:USDT",
        )

        matches = mock_strategy._match_target_to_exchanges(target)

        # 应该匹配所有 exchange
        assert len(matches) == 2
        assert ("okx/main", "BTC/USDT:USDT") in matches
        assert ("binance/main", "BTC/USDT:USDT") in matches

    def test_match_specific_exchange_class(self, mock_strategy, mock_exchange_okx, mock_exchange_binance):
        """测试指定 exchange_class 匹配"""
        mock_strategy._get_all_exchanges = MagicMock(
            return_value=[mock_exchange_okx, mock_exchange_binance]
        )

        target = TargetDefinition(
            exchange="*",
            exchange_class="okx",
            symbol="BTC/USDT:USDT",
        )

        matches = mock_strategy._match_target_to_exchanges(target)

        # 只应该匹配 okx
        assert len(matches) == 1
        assert ("okx/main", "BTC/USDT:USDT") in matches

    def test_match_specific_exchange_path(self, mock_strategy, mock_exchange_okx, mock_exchange_binance):
        """测试指定 exchange path 匹配"""
        mock_strategy._get_all_exchanges = MagicMock(
            return_value=[mock_exchange_okx, mock_exchange_binance]
        )

        target = TargetDefinition(
            exchange="binance/main",
            exchange_class="*",
            symbol="ETH/USDT:USDT",
        )

        matches = mock_strategy._match_target_to_exchanges(target)

        # 只应该匹配 binance/main
        assert len(matches) == 1
        assert ("binance/main", "ETH/USDT:USDT") in matches

    def test_match_no_matches(self, mock_strategy, mock_exchange_okx):
        """测试无匹配情况"""
        mock_strategy._get_all_exchanges = MagicMock(
            return_value=[mock_exchange_okx]
        )

        target = TargetDefinition(
            exchange="*",
            exchange_class="binance",  # 不存在
            symbol="BTC/USDT:USDT",
        )

        matches = mock_strategy._match_target_to_exchanges(target)

        assert len(matches) == 0


class TestStaticPositionsStrategyExpressionEval:
    """StaticPositionsStrategy 表达式求值测试"""

    @pytest.fixture
    def strategy(self):
        """创建 strategy"""
        config = StaticPositionsStrategyConfig(
            name="test",
            targets=[],
        )
        return StaticPositionsStrategy(config)

    def test_safe_eval_arithmetic(self, strategy):
        """测试算术表达式"""
        result = strategy._safe_eval("1 + 2 * 3", {})
        assert result == 7

    def test_safe_eval_with_context(self, strategy):
        """测试带上下文的表达式"""
        context = {"mid_price": 100.0, "ratio": 0.5}
        result = strategy._safe_eval("mid_price * ratio", context)
        assert result == 50.0

    def test_safe_eval_functions(self, strategy):
        """测试内置函数"""
        context = {"values": [1, 2, 3, 4, 5]}

        # sum
        result = strategy._safe_eval("sum(values)", context)
        assert result == 15

        # avg
        result = strategy._safe_eval("avg(values)", context)
        assert result == 3.0

        # min/max
        result = strategy._safe_eval("min(values)", context)
        assert result == 1
        result = strategy._safe_eval("max(values)", context)
        assert result == 5

        # clip
        result = strategy._safe_eval("clip(10, 0, 5)", context)
        assert result == 5

    def test_safe_eval_conditional(self, strategy):
        """测试条件表达式"""
        context = {"rsi": 25}
        result = strategy._safe_eval("1 if rsi < 30 else -1", context)
        assert result == 1

        context = {"rsi": 75}
        result = strategy._safe_eval("1 if rsi < 30 else -1", context)
        assert result == -1

    def test_safe_eval_invalid_returns_none(self, strategy):
        """测试无效表达式返回 None"""
        result = strategy._safe_eval("undefined_var", {})
        assert result is None


class TestStaticPositionsStrategyOutput:
    """StaticPositionsStrategy 输出测试"""

    def test_legacy_format_output(self):
        """测试旧格式输出"""
        config = StaticPositionsStrategyConfig(
            name="test",
            exchange_path="okx/main",
            positions_usd={"BTC/USDT:USDT": 1000},
            speed=0.8,
        )
        strategy = StaticPositionsStrategy(config)

        output = strategy.get_target_positions_usd()

        # 应该是旧格式 (tuple)
        assert isinstance(output, dict)
        key = ("okx/main", "BTC/USDT:USDT")
        assert key in output
        assert output[key] == (1000, 0.8)

    def test_new_format_output(self):
        """测试新格式输出"""
        config = StaticPositionsStrategyConfig(
            name="test",
            targets=[
                TargetDefinition(
                    symbol="BTC/USDT:USDT",
                    position_usd="1000",  # 字符串表达式
                    speed=0.5,
                ),
            ],
        )
        strategy = StaticPositionsStrategy(config)

        # Mock _get_all_exchanges
        mock_exchange = MagicMock()
        mock_exchange.config.path = "okx/main"
        mock_exchange.class_name = "okx"
        strategy._get_all_exchanges = MagicMock(return_value=[mock_exchange])

        # Mock collect_context_vars
        strategy.collect_context_vars = MagicMock(return_value={})

        output = strategy.get_target_positions_usd()

        # 应该是新格式 (dict)
        assert isinstance(output, dict)
        key = ("okx/main", "BTC/USDT:USDT")
        assert key in output
        assert isinstance(output[key], dict)
        assert output[key]["position_usd"] == 1000
        assert output[key]["speed"] == 0.5

    def test_output_with_expression(self):
        """测试带表达式的输出"""
        config = StaticPositionsStrategyConfig(
            name="test",
            targets=[
                TargetDefinition(
                    symbol="BTC/USDT:USDT",
                    position_usd="equation_usd * 0.5",
                ),
            ],
        )
        strategy = StaticPositionsStrategy(config)

        # Mock
        mock_exchange = MagicMock()
        mock_exchange.config.path = "okx/main"
        mock_exchange.class_name = "okx"
        strategy._get_all_exchanges = MagicMock(return_value=[mock_exchange])

        # collect_context_vars 返回 equation_usd
        strategy.collect_context_vars = MagicMock(
            return_value={"equation_usd": 10000}
        )

        output = strategy.get_target_positions_usd()

        key = ("okx/main", "BTC/USDT:USDT")
        assert output[key]["position_usd"] == 5000  # 10000 * 0.5


class TestStaticPositionsStrategyVars:
    """StaticPositionsStrategy vars 计算测试"""

    def test_collect_context_vars_basic(self):
        """测试基本变量收集"""
        config = StaticPositionsStrategyConfig(
            name="test",
            vars=[
                VarDefinition(name="ratio", value="0.6"),
                VarDefinition(name="target", value="ratio * 1000"),
            ],
            targets=[],
        )
        strategy = StaticPositionsStrategy(config)

        # Mock indicator_group
        strategy._root = None

        context = strategy.collect_context_vars("okx/main", "BTC/USDT:USDT")

        assert context["ratio"] == 0.6
        assert context["target"] == 600  # 0.6 * 1000

    def test_vars_triggered(self):
        """测试条件变量触发"""
        config = StaticPositionsStrategyConfig(
            name="test",
            vars=[
                VarDefinition(name="rsi", value="25"),
            ],
            vars={
                "signal": ConditionalVarDefinition(
                    value="1",
                    on="rsi < 30",
                    default=0,
                ),
            },
            targets=[],
        )
        strategy = StaticPositionsStrategy(config)
        strategy._root = None

        context = strategy.collect_context_vars("okx/main", "BTC/USDT:USDT")

        # rsi=25 < 30, 所以应该触发
        assert context["signal"] == 1

    def test_vars_not_triggered(self):
        """测试条件变量未触发"""
        config = StaticPositionsStrategyConfig(
            name="test",
            vars=[
                VarDefinition(name="rsi", value="50"),
            ],
            vars={
                "signal": ConditionalVarDefinition(
                    value="1",
                    on="rsi < 30",
                    default=0,
                ),
            },
            targets=[],
        )
        strategy = StaticPositionsStrategy(config)
        strategy._root = None

        context = strategy.collect_context_vars("okx/main", "BTC/USDT:USDT")

        # rsi=50 >= 30, 所以不触发，使用默认值
        assert context["signal"] == 0

    def test_vars_state_persistence(self):
        """测试条件变量状态持久化"""
        config = StaticPositionsStrategyConfig(
            name="test",
            vars=[
                VarDefinition(name="rsi", value="25"),
            ],
            vars={
                "center_price": ConditionalVarDefinition(
                    value="100.0",
                    on="rsi < 30",
                    default=None,
                ),
            },
            targets=[],
        )
        strategy = StaticPositionsStrategy(config)
        strategy._root = None

        # 第一次调用，触发更新
        context1 = strategy.collect_context_vars("okx/main", "BTC/USDT:USDT")
        assert context1["center_price"] == 100.0

        # 修改 config 中的 rsi 表达式不太现实，所以直接修改状态
        # 模拟第二次调用，条件不满足，应该保持上次值
        config.vars[0] = VarDefinition(name="rsi", value="50")
        context2 = strategy.collect_context_vars("okx/main", "BTC/USDT:USDT")

        # 因为条件不满足，应该保持上次的值 100.0
        assert context2["center_price"] == 100.0


# ============================================================
# Feature 0011: Strategy Target 展开式与去特殊化 - 单元测试
# ============================================================

class TestStaticPositionsStrategy:
    """StaticPositionsStrategy 测试（Feature 0011）"""

    def test_static_positions_import(self):
        """测试新模块导入"""
        from hft.strategy.static_positions import (
            StaticPositionsStrategy,
            StaticPositionsStrategyConfig,
        )
        assert StaticPositionsStrategy is not None
        assert StaticPositionsStrategyConfig is not None

    def test_class_name(self):
        """测试 class_name 是 static_positions"""
        from hft.strategy.static_positions import StaticPositionsStrategyConfig
        assert StaticPositionsStrategyConfig.class_name == "static_positions"

    def test_basic_config(self):
        """测试基本配置"""
        from hft.strategy.static_positions import StaticPositionsStrategyConfig
        config = StaticPositionsStrategyConfig(
            name="test_static",
            targets=[
                {"symbol": "BTC/USDT", "position_usd": "1000", "speed": 0.5}
            ]
        )
        assert config.name == "test_static"
        assert len(config.targets) == 1


class TestTargetPairsExpansion:
    """target_pairs 展开式写法测试（Feature 0011）"""

    def test_string_shorthand(self):
        """测试 string 简写格式"""
        from hft.strategy.static_positions import StaticPositionsStrategyConfig
        config = StaticPositionsStrategyConfig(
            name="test",
            target_pairs=["BTC/USDT", "ETH/USDT"],
            target={"position_usd": "1000", "speed": 0.1}
        )

        assert len(config.targets) == 2

        # 检查第一个 target
        assert config.targets[0].symbol == "BTC/USDT"
        assert config.targets[0].exchange_class == "*"
        assert config.targets[0].exchange == "*"
        assert config.targets[0].position_usd == "1000"
        assert config.targets[0].speed == 0.1

        # 检查第二个 target
        assert config.targets[1].symbol == "ETH/USDT"

    def test_dict_format(self):
        """测试 dict 格式"""
        from hft.strategy.static_positions import StaticPositionsStrategyConfig
        config = StaticPositionsStrategyConfig(
            name="test",
            target_pairs=[
                {"symbol": "BTC/USDT", "exchange_class": "okx"},
                {"symbol": "ETH/USDT", "exchange": "binance/main"},
            ],
            target={"position_usd": "500", "speed": 0.2}
        )

        assert len(config.targets) == 2

        # 第一个：exchange_class=okx
        assert config.targets[0].symbol == "BTC/USDT"
        assert config.targets[0].exchange_class == "okx"
        assert config.targets[0].exchange == "*"

        # 第二个：exchange=binance/main
        assert config.targets[1].symbol == "ETH/USDT"
        assert config.targets[1].exchange == "binance/main"

    def test_mixed_format(self):
        """测试混合格式"""
        from hft.strategy.static_positions import StaticPositionsStrategyConfig
        config = StaticPositionsStrategyConfig(
            name="test",
            target_pairs=[
                "BTC/USDT",
                {"symbol": "ETH/USDT", "exchange_class": "okx"},
            ],
            target={"position_usd": "1000"}
        )

        assert len(config.targets) == 2
        assert config.targets[0].symbol == "BTC/USDT"
        assert config.targets[0].exchange_class == "*"
        assert config.targets[1].symbol == "ETH/USDT"
        assert config.targets[1].exchange_class == "okx"

    def test_target_pairs_with_existing_targets(self):
        """测试 target_pairs + targets 混合写法"""
        from hft.strategy.static_positions import StaticPositionsStrategyConfig
        config = StaticPositionsStrategyConfig(
            name="test",
            target_pairs=["BTC/USDT"],
            target={"position_usd": "1000"},
            targets=[
                {"symbol": "SOL/USDT", "position_usd": "500"}
            ]
        )

        # target_pairs 展开后追加到 targets
        assert len(config.targets) == 2
        # 原有 targets 在前
        assert config.targets[0].symbol == "SOL/USDT"
        # target_pairs 展开后在后
        assert config.targets[1].symbol == "BTC/USDT"

    def test_target_pairs_override(self):
        """测试 target_pairs 中的值覆盖 target 中的值"""
        from hft.strategy.static_positions import StaticPositionsStrategyConfig
        config = StaticPositionsStrategyConfig(
            name="test",
            target_pairs=[
                {"symbol": "BTC/USDT", "position_usd": "2000"},  # 覆盖 target 中的值
            ],
            target={"position_usd": "1000", "speed": 0.1}
        )

        assert len(config.targets) == 1
        # position_usd 被 target_pairs 中的值覆盖
        assert config.targets[0].position_usd == "2000"
        # speed 来自 target
        assert config.targets[0].speed == 0.1


class TestBackwardCompatibility:
    """向后兼容性测试（Feature 0011）"""

    def test_legacy_format_still_works(self):
        """测试旧格式仍然有效"""
        from hft.strategy.static_positions import StaticPositionsStrategyConfig
        config = StaticPositionsStrategyConfig(
            name="test",
            exchange_path="okx/main",
            positions_usd={"BTC/USDT": 1000},
            speed=0.5,
        )

        assert config.exchange_path == "okx/main"
        assert config.positions_usd == {"BTC/USDT": 1000}
        assert config.speed == 0.5


class TestStrategyCondition:
    """Strategy condition 门控测试（Feature 0011）"""

    def test_target_condition_field_exists(self):
        """测试 TargetDefinition 有 condition 字段"""
        from hft.strategy.config import TargetDefinition
        target = TargetDefinition(
            symbol="BTC/USDT",
            condition="rsi < 30"
        )
        assert target.condition == "rsi < 30"

    def test_target_condition_default_none(self):
        """测试 condition 默认为 None"""
        from hft.strategy.config import TargetDefinition
        target = TargetDefinition(symbol="BTC/USDT")
        assert target.condition is None

    def test_global_condition_field_exists(self):
        """测试 BaseStrategyConfig 有 condition 字段"""
        from hft.strategy.static_positions import StaticPositionsStrategyConfig
        config = StaticPositionsStrategyConfig(
            name="test",
            condition="equation_usd > 1000",
            targets=[{"symbol": "BTC/USDT", "position_usd": "1000"}]
        )
        assert config.condition == "equation_usd > 1000"

    def test_global_condition_default_none(self):
        """测试全局 condition 默认为 None"""
        from hft.strategy.static_positions import StaticPositionsStrategyConfig
        config = StaticPositionsStrategyConfig(
            name="test",
            targets=[{"symbol": "BTC/USDT", "position_usd": "1000"}]
        )
        assert config.condition is None

    def test_target_pairs_with_condition_in_target(self):
        """测试 target_pairs 展开时 condition 从 target 模板继承"""
        from hft.strategy.static_positions import StaticPositionsStrategyConfig
        config = StaticPositionsStrategyConfig(
            name="test",
            target_pairs=["BTC/USDT", "ETH/USDT"],
            target={
                "position_usd": "1000",
                "condition": "mid_price > 0"
            }
        )

        assert len(config.targets) == 2
        # condition 从 target 模板继承
        assert config.targets[0].condition == "mid_price > 0"
        assert config.targets[1].condition == "mid_price > 0"

    def test_target_pairs_condition_override(self):
        """测试 target_pairs 单项可以覆盖 condition"""
        from hft.strategy.static_positions import StaticPositionsStrategyConfig
        config = StaticPositionsStrategyConfig(
            name="test",
            target_pairs=[
                "BTC/USDT",  # 继承 target 的 condition
                {"symbol": "ETH/USDT", "condition": "rsi < 40"},  # 覆盖 condition
            ],
            target={
                "position_usd": "1000",
                "condition": "mid_price > 0"
            }
        )

        assert len(config.targets) == 2
        # 第一个继承 target 模板的 condition
        assert config.targets[0].condition == "mid_price > 0"
        # 第二个用自己的 condition 覆盖
        assert config.targets[1].condition == "rsi < 40"

    def test_evaluate_condition_none_returns_true(self):
        """测试 _evaluate_condition(None) 返回 True"""
        from hft.strategy.static_positions import (
            StaticPositionsStrategy,
            StaticPositionsStrategyConfig,
        )
        config = StaticPositionsStrategyConfig(
            name="test",
            targets=[{"symbol": "BTC/USDT", "position_usd": "1000"}]
        )
        strategy = StaticPositionsStrategy(config)

        result = strategy._evaluate_condition(None, {}, "test")
        assert result is True

    def test_evaluate_condition_true_expression(self):
        """测试 condition 求值为 True"""
        from hft.strategy.static_positions import (
            StaticPositionsStrategy,
            StaticPositionsStrategyConfig,
        )
        config = StaticPositionsStrategyConfig(
            name="test",
            targets=[{"symbol": "BTC/USDT", "position_usd": "1000"}]
        )
        strategy = StaticPositionsStrategy(config)

        result = strategy._evaluate_condition("10 > 5", {}, "test")
        assert result is True

    def test_evaluate_condition_false_expression(self):
        """测试 condition 求值为 False"""
        from hft.strategy.static_positions import (
            StaticPositionsStrategy,
            StaticPositionsStrategyConfig,
        )
        config = StaticPositionsStrategyConfig(
            name="test",
            targets=[{"symbol": "BTC/USDT", "position_usd": "1000"}]
        )
        strategy = StaticPositionsStrategy(config)

        result = strategy._evaluate_condition("10 < 5", {}, "test")
        assert result is False

    def test_evaluate_condition_with_context(self):
        """测试 condition 使用上下文变量"""
        from hft.strategy.static_positions import (
            StaticPositionsStrategy,
            StaticPositionsStrategyConfig,
        )
        config = StaticPositionsStrategyConfig(
            name="test",
            targets=[{"symbol": "BTC/USDT", "position_usd": "1000"}]
        )
        strategy = StaticPositionsStrategy(config)

        context = {"rsi": 25, "threshold": 30}
        result = strategy._evaluate_condition("rsi < threshold", context, "test")
        assert result is True

        result = strategy._evaluate_condition("rsi > threshold", context, "test")
        assert result is False

    def test_evaluate_condition_exception_returns_false(self):
        """测试 condition 求值异常时返回 False"""
        from hft.strategy.static_positions import (
            StaticPositionsStrategy,
            StaticPositionsStrategyConfig,
        )
        config = StaticPositionsStrategyConfig(
            name="test",
            targets=[{"symbol": "BTC/USDT", "position_usd": "1000"}]
        )
        strategy = StaticPositionsStrategy(config)

        # 引用不存在的变量
        result = strategy._evaluate_condition("undefined_var > 0", {}, "test")
        assert result is False

    def test_evaluate_condition_none_result_returns_false(self):
        """测试 condition 求值结果为 None 时返回 False"""
        from hft.strategy.static_positions import (
            StaticPositionsStrategy,
            StaticPositionsStrategyConfig,
        )
        config = StaticPositionsStrategyConfig(
            name="test",
            targets=[{"symbol": "BTC/USDT", "position_usd": "1000"}]
        )
        strategy = StaticPositionsStrategy(config)

        # 提供一个返回 None 的变量
        context = {"maybe_none": None}
        result = strategy._evaluate_condition("maybe_none", context, "test")
        assert result is False

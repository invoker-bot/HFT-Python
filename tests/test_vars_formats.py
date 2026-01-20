"""
vars 简化格式支持 - 单元测试

测试内容：
1. BaseExecutorConfig vars 的三种格式
2. ScopeConfig vars 的三种格式
3. BaseStrategyConfig vars 的三种格式
4. TargetDefinition vars 的三种格式
5. 混合格式支持
"""
import pytest
from hft.strategy.config import (
    BaseStrategyConfig,
    ScopeConfig,
    ScopeVarDefinition,
    TargetDefinition,
    VarDefinition,
)
from hft.executor.base_config import (
    BaseExecutorConfig,
    ExecutorVarDefinition,
)


class TestBaseExecutorConfigVarsFormats:
    """BaseExecutorConfig vars 格式测试"""

    def test_format1_standard_list(self):
        """测试格式 1：标准 list[ExecutorVarDefinition] 格式"""
        config = BaseExecutorConfig(
            vars=[
                {"name": "delta_usd", "value": "target_usd - current_usd"},
                {"name": "ratio", "value": "delta_usd / max_usd"},
            ]
        )

        assert len(config.vars) == 2
        assert isinstance(config.vars[0], ExecutorVarDefinition)
        assert config.vars[0].name == "delta_usd"
        assert config.vars[0].value == "target_usd - current_usd"

    def test_format1_with_conditional(self):
        """测试格式 1：带条件变量"""
        config = BaseExecutorConfig(
            vars=[
                {
                    "name": "entry_price",
                    "value": "mid_price",
                    "on": "position == 0",
                    "initial_value": None
                }
            ]
        )

        assert len(config.vars) == 1
        assert config.vars[0].name == "entry_price"
        assert config.vars[0].on == "position == 0"
        assert config.vars[0].initial_value is None

    def test_format2_dict(self):
        """测试格式 2：dict 简化格式"""
        config = BaseExecutorConfig(
            vars={
                "delta_usd": "target_usd - current_usd",
                "ratio": "delta_usd / max_usd",
            }
        )

        assert len(config.vars) == 2
        var_names = {v.name for v in config.vars}
        assert "delta_usd" in var_names
        assert "ratio" in var_names

    def test_format3_list_str(self):
        """测试格式 3：list[str] 简化格式"""
        config = BaseExecutorConfig(
            vars=[
                "delta_usd=target_usd - current_usd",
                "ratio=delta_usd / max_usd",
            ]
        )

        assert len(config.vars) == 2
        assert config.vars[0].name == "delta_usd"
        assert config.vars[0].value == "target_usd - current_usd"

    def test_mixed_format(self):
        """测试混合格式"""
        config = BaseExecutorConfig(
            vars=[
                "delta_usd=target_usd - current_usd",
                {
                    "name": "entry_price",
                    "value": "mid_price",
                    "on": "position == 0",
                }
            ]
        )

        assert len(config.vars) == 2
        assert config.vars[0].name == "delta_usd"
        assert config.vars[1].name == "entry_price"
        assert config.vars[1].on == "position == 0"


class TestScopeConfigVarsFormats:
    """ScopeConfig vars 格式测试"""

    def test_format1_standard_list(self):
        """测试格式 1：标准格式"""
        config = ScopeConfig(
            class_name="GlobalScope",
            vars=[
                {"name": "max_position", "value": "10000"},
                {"name": "speed", "value": "0.5"},
            ]
        )

        assert len(config.vars) == 2
        assert isinstance(config.vars[0], ScopeVarDefinition)
        assert config.vars[0].name == "max_position"
        assert config.vars[0].value == "10000"

    def test_format2_dict(self):
        """测试格式 2：dict 简化格式"""
        config = ScopeConfig(
            class_name="GlobalScope",
            vars={
                "max_position": "10000",
                "speed": "0.5",
            }
        )

        assert len(config.vars) == 2
        var_names = {v.name for v in config.vars}
        assert "max_position" in var_names
        assert "speed" in var_names

    def test_format3_list_str(self):
        """测试格式 3：list[str] 简化格式"""
        config = ScopeConfig(
            class_name="GlobalScope",
            vars=[
                "max_position=10000",
                "speed=0.5",
            ]
        )

        assert len(config.vars) == 2
        assert config.vars[0].name == "max_position"
        assert config.vars[0].value == "10000"

    def test_mixed_format(self):
        """测试混合格式"""
        config = ScopeConfig(
            class_name="TradingPairScope",
            vars=[
                "base_amount=1.0",
                {
                    "name": "entry_price",
                    "value": "mid_price",
                    "on": "position == 0",
                    "initial_value": None
                }
            ]
        )

        assert len(config.vars) == 2
        assert config.vars[0].name == "base_amount"
        assert config.vars[1].name == "entry_price"
        assert config.vars[1].on == "position == 0"


class TestBaseStrategyConfigVarsFormats:
    """BaseStrategyConfig vars 格式测试"""

    def test_format1_standard_list(self):
        """测试格式 1：标准 list[VarDefinition] 格式"""
        config = BaseStrategyConfig(
            name="test_strategy",
            vars=[
                {"name": "max_position", "value": "10000"},
                {"name": "risk_ratio", "value": "0.6"},
            ]
        )

        assert len(config.vars) == 2
        assert isinstance(config.vars[0], VarDefinition)
        assert config.vars[0].name == "max_position"
        assert config.vars[0].value == "10000"

    def test_format1_with_conditional(self):
        """测试格式 1：带条件变量"""
        config = BaseStrategyConfig(
            name="test_strategy",
            vars=[
                {
                    "name": "direction",
                    "value": "1 if rsi[-1] < 30 else -1",
                    "on": "rsi[-1] < 30 or rsi[-1] > 70",
                    "initial_value": 0
                }
            ]
        )

        assert len(config.vars) == 1
        assert config.vars[0].name == "direction"
        assert config.vars[0].on == "rsi[-1] < 30 or rsi[-1] > 70"
        assert config.vars[0].initial_value == 0

    def test_format2_dict(self):
        """测试格式 2：dict 简化格式"""
        config = BaseStrategyConfig(
            name="test_strategy",
            vars={
                "max_position": "10000",
                "risk_ratio": "0.6",
            }
        )

        assert len(config.vars) == 2
        var_names = {v.name for v in config.vars}
        assert "max_position" in var_names
        assert "risk_ratio" in var_names

    def test_format3_list_str(self):
        """测试格式 3：list[str] 简化格式"""
        config = BaseStrategyConfig(
            name="test_strategy",
            vars=[
                "max_position=10000",
                "risk_ratio=0.6",
            ]
        )

        assert len(config.vars) == 2
        assert config.vars[0].name == "max_position"
        assert config.vars[0].value == "10000"

    def test_mixed_format(self):
        """测试混合格式"""
        config = BaseStrategyConfig(
            name="test_strategy",
            vars=[
                "max_position=10000",
                {
                    "name": "direction",
                    "value": "1 if rsi[-1] < 30 else -1",
                    "on": "rsi[-1] < 30 or rsi[-1] > 70",
                }
            ]
        )

        assert len(config.vars) == 2
        assert config.vars[0].name == "max_position"
        assert config.vars[1].name == "direction"
        assert config.vars[1].on == "rsi[-1] < 30 or rsi[-1] > 70"


class TestTargetDefinitionVarsFormats:
    """TargetDefinition vars 格式测试"""

    def test_format1_standard_list(self):
        """测试格式 1：标准 list[VarDefinition] 格式"""
        target = TargetDefinition(
            exchange_id="okx/main",
            symbol="BTC/USDT:USDT",
            vars=[
                {"name": "position_usd", "value": "max_position * risk_ratio"},
                {"name": "speed", "value": "0.5"},
            ]
        )

        assert len(target.vars) == 2
        assert isinstance(target.vars[0], VarDefinition)
        assert target.vars[0].name == "position_usd"
        assert target.vars[0].value == "max_position * risk_ratio"

    def test_format1_with_conditional(self):
        """测试格式 1：带条件变量"""
        target = TargetDefinition(
            exchange_id="okx/main",
            symbol="BTC/USDT:USDT",
            vars=[
                {
                    "name": "position_usd",
                    "value": "max_position * direction",
                    "on": "direction != 0",
                    "initial_value": 0
                }
            ]
        )

        assert len(target.vars) == 1
        assert target.vars[0].name == "position_usd"
        assert target.vars[0].on == "direction != 0"
        assert target.vars[0].initial_value == 0

    def test_format2_dict(self):
        """测试格式 2：dict 简化格式"""
        target = TargetDefinition(
            exchange_id="okx/main",
            symbol="BTC/USDT:USDT",
            vars={
                "position_usd": "max_position * risk_ratio",
                "speed": "0.5",
            }
        )

        assert len(target.vars) == 2
        var_names = {v.name for v in target.vars}
        assert "position_usd" in var_names
        assert "speed" in var_names

    def test_format3_list_str(self):
        """测试格式 3：list[str] 简化格式"""
        target = TargetDefinition(
            exchange_id="okx/main",
            symbol="BTC/USDT:USDT",
            vars=[
                "position_usd=max_position * risk_ratio",
                "speed=0.5",
            ]
        )

        assert len(target.vars) == 2
        assert target.vars[0].name == "position_usd"
        assert target.vars[0].value == "max_position * risk_ratio"

    def test_mixed_format(self):
        """测试混合格式"""
        target = TargetDefinition(
            exchange_id="okx/main",
            symbol="BTC/USDT:USDT",
            vars=[
                "position_usd=max_position * risk_ratio",
                {
                    "name": "speed",
                    "value": "0.5 if volatility < 0.01 else 0.8",
                    "on": "volatility is not None",
                }
            ]
        )

        assert len(target.vars) == 2
        assert target.vars[0].name == "position_usd"
        assert target.vars[1].name == "speed"
        assert target.vars[1].on == "volatility is not None"

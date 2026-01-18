"""
vars 简化格式支持 - 单元测试

测试内容：
1. BaseExecutorConfig vars 的三种格式
2. ScopeConfig vars 的三种格式
3. 混合格式支持

注意：BaseStrategyConfig 不再支持顶级 vars 字段，vars 定义在 scopes 中。
"""
import pytest
from hft.strategy.config import (
    ScopeConfig,
    ScopeVarDefinition,
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

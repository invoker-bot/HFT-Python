"""
StaticPositionsStrategy Scope 支持测试

验证 StaticPositionsStrategy 与 Scope 系统的兼容性
"""
from hft.strategy.static_positions import StaticPositionsStrategy, StaticPositionsStrategyConfig


class TestStaticPositionsScopeSupport:
    """测试 StaticPositionsStrategy 的 Scope 支持"""

    def test_strategy_inherits_scope_attributes(self):
        """测试 Strategy 继承了 Scope 相关属性"""
        config = StaticPositionsStrategyConfig(
            name="test_strategy",
            class_name="static_positions"
        )
        strategy = StaticPositionsStrategy(config)

        # 验证继承自 BaseStrategy 的 Scope 属性
        assert hasattr(strategy, 'scope_manager')
        assert hasattr(strategy, 'scope_trees')

    def test_strategy_config_supports_links(self):
        """测试 Strategy 配置支持 links 字段"""
        config = StaticPositionsStrategyConfig(
            name="test_strategy",
            class_name="static_positions",
            links=[
                ["global", "exchange", "trading_pair"]
            ]
        )

        assert hasattr(config, 'links')
        assert len(config.links) == 1
        assert config.links[0] == ["global", "exchange", "trading_pair"]

    def test_strategy_config_supports_scopes(self):
        """测试 Strategy 配置支持 scopes 字段"""
        config = StaticPositionsStrategyConfig(
            name="test_strategy",
            class_name="static_positions",
            scopes={
                "global": {
                    "class": "GlobalScope",
                    "vars": [{"name": "max_position", "value": "10000"}]
                }
            }
        )

        assert hasattr(config, 'scopes')
        assert "global" in config.scopes

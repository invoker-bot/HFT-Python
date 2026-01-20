"""
Strategy Scope 集成单元测试

测试 BaseStrategy 与 Scope 系统的集成功能
"""
from hft.strategy.base import BaseStrategy
from hft.strategy.config import BaseStrategyConfig


class DummyStrategy(BaseStrategy):
    """测试用的简单 Strategy"""

    def get_target_positions_usd(self):
        return {}

    async def on_tick(self) -> bool:
        """实现抽象方法 on_tick"""
        return False


class TestStrategyBasicScopeIntegration:
    """测试 Strategy 基础 Scope 集成"""

    def test_strategy_has_scope_manager_attribute(self):
        """测试 Strategy 有 scope_manager 属性"""
        config = BaseStrategyConfig(
            name="test_strategy",
            class_name="dummy"
        )
        strategy = DummyStrategy(config)

        # 初始状态下 scope_manager 为 None
        assert hasattr(strategy, 'scope_manager')
        assert strategy.scope_manager is None

    def test_strategy_has_scope_trees_attribute(self):
        """测试 Strategy 有 scope_trees 属性"""
        config = BaseStrategyConfig(
            name="test_strategy",
            class_name="dummy"
        )
        strategy = DummyStrategy(config)

        # 初始状态下 scope_trees 为空列表
        assert hasattr(strategy, 'scope_trees')
        assert isinstance(strategy.scope_trees, list)
        assert len(strategy.scope_trees) == 0


class TestStrategyConfigLinksField:
    """测试 Strategy 配置的 links 字段"""

    def test_config_accepts_links_field(self):
        """测试配置接受 links 字段"""
        config = BaseStrategyConfig(
            name="test_strategy",
            class_name="dummy",
            links=[
                ["global", "exchange", "trading_pair"]
            ]
        )

        assert hasattr(config, 'links')
        assert len(config.links) == 1
        assert config.links[0] == ["global", "exchange", "trading_pair"]

    def test_config_links_default_empty(self):
        """测试 links 字段默认为空列表"""
        config = BaseStrategyConfig(
            name="test_strategy",
            class_name="dummy"
        )

        assert config.links == []


class TestStrategyConfigSymbolFilters:
    """测试 Strategy 配置的 symbol 过滤字段"""

    def test_config_accepts_include_symbols(self):
        """测试配置接受 include_symbols 字段"""
        config = BaseStrategyConfig(
            name="test_strategy",
            class_name="dummy",
            include_symbols=["BTC/USDT", "ETH/USDT"]
        )

        assert hasattr(config, 'include_symbols')
        assert config.include_symbols == ["BTC/USDT", "ETH/USDT"]

    def test_config_accepts_exclude_symbols(self):
        """测试配置接受 exclude_symbols 字段"""
        config = BaseStrategyConfig(
            name="test_strategy",
            class_name="dummy",
            exclude_symbols=["DOGE/USDT"]
        )

        assert hasattr(config, 'exclude_symbols')
        assert config.exclude_symbols == ["DOGE/USDT"]

    def test_config_symbol_filters_default_values(self):
        """测试 symbol 过滤字段的默认值"""
        config = BaseStrategyConfig(
            name="test_strategy",
            class_name="dummy"
        )

        # include_symbols 默认为 ['*']
        assert config.include_symbols == ['*']
        # exclude_symbols 默认为空列表
        assert config.exclude_symbols == []

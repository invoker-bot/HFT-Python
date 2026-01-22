"""Feature 0013: MarketNeutralPositions 策略单元测试

测试内容：
- FairPriceIndicator 公平价格指标
- MedalAmountDataSource 账户余额数据源
- MarketNeutralPositionsConfig 策略配置
- MarketNeutralPositionsStrategy 策略
- TradingPairClassGroupScope 分组 Scope
"""
# pylint: disable=import-outside-toplevel,protected-access
import time


class TestFairPriceIndicator:
    """FairPriceIndicator 单元测试"""

    def test_init(self):
        """测试初始化"""
        from hft.indicator.fair_price_indicator import FairPriceIndicator

        indicator = FairPriceIndicator(
            exchange_class="okx",
            symbol="ETH/USDT",
            ready_condition="timeout < 30"
        )

        assert indicator.name == "FairPrice:okx:ETH/USDT"
        assert indicator.exchange_class == "okx"
        assert indicator.symbol == "ETH/USDT"
        assert indicator._mid_price is None

    def test_calculate_vars_no_data(self):
        """测试无数据时的 calculate_vars"""
        from hft.indicator.fair_price_indicator import FairPriceIndicator

        indicator = FairPriceIndicator(
            exchange_class="okx",
            symbol="ETH/USDT"
        )

        # 无数据时应返回 None
        result = indicator.calculate_vars(direction=0)
        assert result == {"trading_pair_std_price": None}

    def test_calculate_vars_with_data(self):
        """测试有数据时的 calculate_vars"""
        from hft.indicator.fair_price_indicator import FairPriceIndicator

        indicator = FairPriceIndicator(
            exchange_class="okx",
            symbol="ETH/USDT"
        )

        # 模拟有 mid_price 数据
        indicator._mid_price = 2000.5
        indicator._last_update = time.time()
        indicator._data.append(time.time(), 2000.5)

        result = indicator.calculate_vars(direction=0)
        assert result == {"trading_pair_std_price": 2000.5}

    def test_is_ready_no_data(self):
        """测试无数据时的 is_ready"""
        from hft.indicator.fair_price_indicator import FairPriceIndicator

        indicator = FairPriceIndicator(
            exchange_class="okx",
            symbol="ETH/USDT"
        )

        # 无数据时不应 ready
        assert not indicator.is_ready()

    def test_is_ready_with_data(self):
        """测试有数据时的 is_ready"""
        from hft.indicator.fair_price_indicator import FairPriceIndicator

        indicator = FairPriceIndicator(
            exchange_class="okx",
            symbol="ETH/USDT",
            ready_condition=None  # 禁用 ready_condition
        )

        # 模拟有数据
        indicator._mid_price = 2000.5
        now = time.time()
        indicator._data.append(now, 2000.5)

        assert indicator.is_ready()


class TestMedalAmountDataSource:
    """MedalAmountDataSource 单元测试"""

    def test_init(self):
        """测试初始化"""
        from hft.datasource.medal_amount_datasource import MedalAmountDataSource

        ds = MedalAmountDataSource(
            exchange_path="okx/main",
            interval=60.0
        )

        assert ds.name == "MedalAmount:okx/main"
        assert ds.exchange_path == "okx/main"

    def test_calculate_vars_no_data(self):
        """测试无数据时的 calculate_vars"""
        from hft.datasource.medal_amount_datasource import MedalAmountDataSource

        ds = MedalAmountDataSource(
            exchange_path="okx/main"
        )

        # 无数据时应返回 0
        result = ds.calculate_vars(direction=0)
        assert result == {"amount": 0.0}

    def test_calculate_vars_with_data(self):
        """测试有数据时的 calculate_vars"""
        from hft.datasource.medal_amount_datasource import (
            MedalAmountDataSource,
            AmountData
        )

        ds = MedalAmountDataSource(
            exchange_path="okx/main"
        )

        # 模拟有数据
        now = time.time()
        data = AmountData(amount=10000.0, timestamp=now)
        ds._data.append(now, data)

        result = ds.calculate_vars(direction=0)
        assert result == {"amount": 10000.0}


class TestMarketNeutralPositionsConfig:
    """MarketNeutralPositionsConfig 单元测试"""

    def test_init_default_values(self):
        """测试默认值"""
        from hft.strategy.market_neutral_positions import MarketNeutralPositionsConfig

        config = MarketNeutralPositionsConfig(
            name="test",
            path="market_neutral_positions/test"
        )

        assert config.max_trading_pair_groups == 10
        assert config.max_position_usd == 2000.0
        assert config.entry_price_threshold == 0.001
        assert config.exit_price_threshold == 0.0005
        assert config.score_threshold == 0.001
        assert config.default_trading_pair_group == "symbol.split('/')[0]"
        assert config.trading_pair_group == {}
        assert config.weights == {}

    def test_init_custom_values(self):
        """测试自定义值"""
        from hft.strategy.market_neutral_positions import MarketNeutralPositionsConfig

        config = MarketNeutralPositionsConfig(
            name="test",
            path="market_neutral_positions/test",
            max_trading_pair_groups=5,
            max_position_usd=5000.0,
            entry_price_threshold=0.002,
            exit_price_threshold=0.001,
            score_threshold=0.002,
            trading_pair_group={"WBETH/USDT": "ETH"},
            weights={"okx/main": 0.5, "binance/spot": 0.5}
        )

        assert config.max_trading_pair_groups == 5
        assert config.max_position_usd == 5000.0
        assert config.entry_price_threshold == 0.002
        assert config.trading_pair_group == {"WBETH/USDT": "ETH"}
        assert config.weights == {"okx/main": 0.5, "binance/spot": 0.5}

    def test_class_name(self):
        """测试 class_name"""
        from hft.strategy.market_neutral_positions import MarketNeutralPositionsConfig

        assert MarketNeutralPositionsConfig.class_name == "market_neutral_positions"


class TestMarketNeutralPositionsStrategy:
    """MarketNeutralPositionsStrategy 单元测试"""

    def test_init(self):
        """测试初始化"""
        from hft.strategy.market_neutral_positions import (
            MarketNeutralPositionsStrategy,
            MarketNeutralPositionsConfig
        )

        config = MarketNeutralPositionsConfig(
            name="test",
            path="market_neutral_positions/test"
        )
        strategy = MarketNeutralPositionsStrategy(config)

        assert strategy.config == config
        assert strategy.DIRECTION_ENTRY_SHORT == -1
        assert strategy.DIRECTION_EXIT == 0
        assert strategy.DIRECTION_ENTRY_LONG == 1
        assert strategy.DIRECTION_HOLD is None

    def test_get_group_id_with_mapping(self):
        """测试使用映射的 group_id 计算"""
        from hft.strategy.market_neutral_positions import (
            MarketNeutralPositionsStrategy,
            MarketNeutralPositionsConfig
        )

        config = MarketNeutralPositionsConfig(
            name="test",
            path="market_neutral_positions/test",
            trading_pair_group={"WBETH/USDT": "ETH"}
        )
        strategy = MarketNeutralPositionsStrategy(config)

        # 使用映射
        assert strategy._get_group_id("WBETH/USDT") == "ETH"

        # 使用默认规则
        assert strategy._get_group_id("BTC/USDT") == "BTC"

    def test_compute_direction(self):
        """测试 Direction 计算"""
        from hft.strategy.market_neutral_positions import (
            MarketNeutralPositionsStrategy,
            MarketNeutralPositionsConfig
        )

        config = MarketNeutralPositionsConfig(
            name="test",
            path="market_neutral_positions/test",
            entry_price_threshold=0.001,
            exit_price_threshold=0.0005
        )
        strategy = MarketNeutralPositionsStrategy(config)

        # delta_min: 大于 entry_threshold → Entry Short
        assert strategy._compute_direction(0.002, is_min=True) == -1

        # delta_min: 大于 exit_threshold → Exit
        assert strategy._compute_direction(0.0006, is_min=True) == 0

        # delta_min: 小于 exit_threshold → Hold
        assert strategy._compute_direction(0.0004, is_min=True) is None

        # delta_max: 大于 entry_threshold → Entry Long
        assert strategy._compute_direction(0.002, is_min=False) == 1

        # delta_max: 大于 exit_threshold → Exit
        assert strategy._compute_direction(0.0006, is_min=False) == 0

        # delta_max: 小于 exit_threshold → Hold
        assert strategy._compute_direction(0.0004, is_min=False) is None

    def test_adjust_ratio_by_direction(self):
        """测试 Ratio 调整逻辑"""
        from hft.strategy.market_neutral_positions import (
            MarketNeutralPositionsStrategy,
            MarketNeutralPositionsConfig
        )

        config = MarketNeutralPositionsConfig(
            name="test",
            path="market_neutral_positions/test"
        )
        strategy = MarketNeutralPositionsStrategy(config)

        # (-1, 0): min(ratio, 0)
        assert strategy._adjust_ratio_by_direction(0.5, -1, 0) == 0
        assert strategy._adjust_ratio_by_direction(-0.5, -1, 0) == -0.5

        # (-1, 1): ratio 不变
        assert strategy._adjust_ratio_by_direction(0.5, -1, 1) == 0.5

        # (-1, None): -1
        assert strategy._adjust_ratio_by_direction(0.5, -1, None) == -1.0

        # (0, 1): max(ratio, 0)
        assert strategy._adjust_ratio_by_direction(-0.5, 0, 1) == 0
        assert strategy._adjust_ratio_by_direction(0.5, 0, 1) == 0.5

        # (None, 1): 1
        assert strategy._adjust_ratio_by_direction(0.5, None, 1) == 1.0

        # (None, None): ratio 不变
        assert strategy._adjust_ratio_by_direction(0.5, None, None) == 0.5


class TestTradingPairClassGroupScope:
    """TradingPairClassGroupScope 单元测试"""

    def test_init(self):
        """测试初始化"""
        from hft.core.scope.scopes import TradingPairClassGroupScope, GlobalScope
        from hft.core.scope.tree import LinkedScopeNode

        parent_scope = GlobalScope(scope_class_id="global", scope_instance_id="global")
        scope = TradingPairClassGroupScope(
            scope_class_id="trading_pair_class_group",
            scope_instance_id="ETH"
        )

        # 创建树结构
        parent_node = LinkedScopeNode(scope=parent_scope, parent=None)
        child_node = LinkedScopeNode(scope=scope, parent=parent_node)
        parent_node.add_child(child_node)

        assert scope.scope_class_id == "trading_pair_class_group"
        assert scope.scope_instance_id == "ETH"
        assert scope.get_var("instance_id") == "ETH"
        assert scope.get_var("group_id") == "ETH"
        assert child_node.parent == parent_node

    def test_children_via_add_child(self):
        """测试通过 LinkedScopeNode 添加 children"""
        from hft.core.scope.scopes import (
            TradingPairClassGroupScope,
            TradingPairClassScope,
            GlobalScope
        )
        from hft.core.scope.tree import LinkedScopeNode

        parent_scope = GlobalScope(scope_class_id="global", scope_instance_id="global")
        group_scope = TradingPairClassGroupScope(
            scope_class_id="trading_pair_class_group",
            scope_instance_id="ETH"
        )

        # 创建树结构
        parent_node = LinkedScopeNode(scope=parent_scope, parent=None)
        group_node = LinkedScopeNode(scope=group_scope, parent=parent_node)
        parent_node.add_child(group_node)

        # 通过 LinkedScopeNode 添加 child
        child1 = TradingPairClassScope(
            scope_class_id="trading_pair_class",
            scope_instance_id="okx-ETH/USDT"
        )
        child1_node = LinkedScopeNode(scope=child1, parent=group_node)
        group_node.add_child(child1_node)

        assert len(group_node.children) == 1
        assert "okx-ETH/USDT" in group_node.children
        assert group_node.children["okx-ETH/USDT"] == child1_node
        assert child1_node.parent == group_node


class TestBaseStrategyGroupIdProvider:
    """BaseStrategy._get_group_id_for_symbol 单元测试"""

    def test_default_group_id(self):
        """测试默认 group_id 计算"""
        # 使用 MarketNeutralPositionsStrategy 作为具体实现
        from hft.strategy.market_neutral_positions import (
            MarketNeutralPositionsStrategy,
            MarketNeutralPositionsConfig
        )

        config = MarketNeutralPositionsConfig(
            name="test",
            path="market_neutral_positions/test"
        )
        strategy = MarketNeutralPositionsStrategy(config)

        # 默认使用 symbol 的第一部分（通过 default_trading_pair_group 表达式）
        assert strategy._get_group_id_for_symbol("ETH/USDT") == "ETH"
        assert strategy._get_group_id_for_symbol("BTC/USDT") == "BTC"

    def test_group_id_with_mapping(self):
        """测试使用映射的 group_id 计算"""
        from hft.strategy.market_neutral_positions import (
            MarketNeutralPositionsStrategy,
            MarketNeutralPositionsConfig
        )

        config = MarketNeutralPositionsConfig(
            name="test",
            path="market_neutral_positions/test",
            trading_pair_group={"WBETH/USDT": "ETH"}
        )
        strategy = MarketNeutralPositionsStrategy(config)

        # 使用映射
        assert strategy._get_group_id_for_symbol("WBETH/USDT") == "ETH"

        # 不在映射中的使用默认规则
        assert strategy._get_group_id_for_symbol("BTC/USDT") == "BTC"


class TestIndicatorFactoryRegistration:
    """测试 IndicatorFactory 中的新 Indicator 注册"""

    def test_fair_price_indicator_registered(self):
        """测试 FairPriceIndicator 已注册"""
        from hft.indicator.factory import IndicatorFactory

        classes: dict[str, type] = IndicatorFactory._get_builtin_classes()
        assert "FairPriceIndicator" in classes

    def test_medal_amount_datasource_registered(self):
        """测试 MedalAmountDataSource 已注册"""
        from hft.indicator.factory import IndicatorFactory

        classes: dict[str, type] = IndicatorFactory._get_builtin_classes()
        assert "MedalAmountDataSource" in classes

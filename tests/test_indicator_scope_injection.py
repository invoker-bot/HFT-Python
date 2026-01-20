"""
Indicator Scope 注入单元测试

测试 Indicator 变量注入到 Scope 的功能
"""
from hft.indicator.datasource.ticker_datasource import TickerDataSource
from hft.indicator.datasource.equation_datasource import MedalEquationDataSource


class TestIndicatorScopeLevel:
    """测试 Indicator scope_level 属性"""

    def test_ticker_datasource_has_scope_level(self):
        """测试 TickerDataSource 有 scope_level 属性"""
        ticker = TickerDataSource(
            exchange_class="okx",
            symbol="BTC/USDT"
        )

        assert hasattr(ticker, 'scope_level')
        assert ticker.scope_level == "trading_pair_class"

    def test_equation_datasource_has_scope_level(self):
        """测试 MedalEquationDataSource 有 scope_level 属性"""
        equation = MedalEquationDataSource(
            exchange_path="okx/main"
        )

        assert hasattr(equation, 'scope_level')
        assert equation.scope_level == "exchange"

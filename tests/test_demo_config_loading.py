"""
Demo 配置集成测试

测试 conf/*/demo/ 目录下的配置能真实连接 Testnet/Sandbox 并执行请求。
使用解密密码: null

测试范围：
- conf/exchange/demo/* - 交易所配置（连接 Testnet，获取市场数据）
- conf/executor/demo/* - 执行器配置
- conf/strategy/demo/* - 策略配置
- conf/app/demo/* - 应用配置

标记：@pytest.mark.integration - 集成测试，需要网络连接

运行方式：
- 默认不运行（pytest -q 会跳过）
- 显式运行：pytest -m integration -v -s
- 运行全部测试（包括集成测试）：pytest -m "" -v -s
"""
# pylint: disable=import-outside-toplevel
import os
from glob import glob
from pathlib import Path

import pytest

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent


def get_demo_configs(subdir: str) -> list[str]:
    """获取 demo 目录下的所有配置文件路径名"""
    pattern = PROJECT_ROOT / "conf" / subdir / "demo" / "**" / "*.yaml"
    files = glob(str(pattern), recursive=True)
    result = []
    for file in files:
        # 转换为相对路径名（不含 .yaml 后缀）
        rel_path = os.path.relpath(file, PROJECT_ROOT / "conf" / subdir)
        result.append(os.path.splitext(rel_path)[0])
    return result


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(scope="module", autouse=True)
def init_fernet():
    """初始化 Fernet 解密（密码: null）"""
    from hft.config.crypto import init_fernet
    init_fernet("null")
    yield


# ============================================================
# Exchange 集成测试
# ============================================================

@pytest.mark.integration
class TestExchangeIntegration:
    """交易所集成测试 - 真实连接 Testnet"""

    @pytest.fixture
    def exchange_configs(self):
        """获取 exchange/demo 下的配置列表"""
        return get_demo_configs("exchange")

    def test_demo_exchange_configs_exist(self, exchange_configs):
        """验证 demo 交易所配置存在"""
        assert len(exchange_configs) > 0, "No demo exchange configs found in conf/exchange/demo/"
        print(f"Found {len(exchange_configs)} demo exchange configs: {exchange_configs}")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("config_path", get_demo_configs("exchange"))
    async def test_exchange_load_markets(self, config_path):
        """测试交易所加载市场数据（真实请求）"""
        from hft.exchange.config import BaseExchangeConfig

        config = BaseExchangeConfig.load(config_path, cwd=str(PROJECT_ROOT))
        exchange = config.instance

        try:
            # 真实请求：加载市场
            markets = await exchange.load_markets()

            assert markets is not None
            assert len(markets) > 0, "No markets loaded"

            # 打印部分市场信息
            sample_symbols = list(markets.keys())[:5]
            print(f"✓ [{config_path}] Loaded {len(markets)} markets. Sample: {sample_symbols}")

        finally:
            # 关闭连接
            await exchange.close()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("config_path", get_demo_configs("exchange"))
    async def test_exchange_fetch_ticker(self, config_path):
        """测试交易所获取 Ticker（真实请求）"""
        from hft.exchange.config import BaseExchangeConfig

        config = BaseExchangeConfig.load(config_path, cwd=str(PROJECT_ROOT))
        exchange = config.instance

        try:
            # 先加载市场
            markets = await exchange.load_markets()

            # 选择一个活跃的交易对
            # 优先选择 BTC/USDT 或第一个可用的
            test_symbol = None
            for symbol in ["BTC/USDT", "BTC/USDT:USDT", "ETH/USDT", "ETH/USDT:USDT"]:
                if symbol in markets:
                    test_symbol = symbol
                    break

            if test_symbol is None:
                test_symbol = list(markets.keys())[0]

            # 真实请求：获取 ticker
            ticker = await exchange.fetch_ticker(test_symbol)

            assert ticker is not None
            assert "last" in ticker or "close" in ticker, "Ticker should have price info"

            price = ticker.get("last") or ticker.get("close")
            print(f"✓ [{config_path}] Ticker {test_symbol}: price={price}")

        finally:
            await exchange.close()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("config_path", get_demo_configs("exchange"))
    async def test_exchange_fetch_balance(self, config_path):
        """测试交易所获取余额（真实请求，需要有效 API Key）

        注意：某些交易所的 test 模式可能不支持 fetch_balance：
        - 如遇到 NotSupported 或 AuthenticationError 会跳过测试
        """
        from ccxt import NotSupported, AuthenticationError
        from hft.exchange.config import BaseExchangeConfig

        config = BaseExchangeConfig.load(config_path, cwd=str(PROJECT_ROOT))
        exchange = config.instance

        try:
            # 先加载市场
            await exchange.load_markets()

            # 真实请求：获取余额
            # 项目使用 medal_fetch_balance(ccxt_instance_key) 方法
            # 获取第一个 ccxt 实例的 key
            ccxt_keys = list(exchange.exchanges.keys())
            if not ccxt_keys:
                pytest.skip("No ccxt instance available")

            try:
                balance = await exchange.medal_fetch_balance(ccxt_keys[0])
            except NotSupported as e:
                pytest.skip(f"Exchange does not support fetch_balance in sandbox mode: {e}")
            except AuthenticationError as e:
                pytest.skip(f"Authentication failed (invalid API key?): {e}")

            assert balance is not None
            assert isinstance(balance, dict), "Balance should be a dict"

            # 打印余额信息（余额可能是嵌套字典）
            print(f"✓ [{config_path}] Balance fetched successfully, keys: {list(balance.keys())[:10]}")

        finally:
            await exchange.close()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("config_path", get_demo_configs("exchange"))
    async def test_exchange_fetch_order_book(self, config_path):
        """测试交易所获取订单簿（真实请求）"""
        from hft.exchange.config import BaseExchangeConfig

        config = BaseExchangeConfig.load(config_path, cwd=str(PROJECT_ROOT))
        exchange = config.instance

        try:
            # 先加载市场
            markets = await exchange.load_markets()

            # 选择测试交易对
            test_symbol = None
            for symbol in ["BTC/USDT", "BTC/USDT:USDT", "ETH/USDT"]:
                if symbol in markets:
                    test_symbol = symbol
                    break

            if test_symbol is None:
                test_symbol = list(markets.keys())[0]

            # 真实请求：获取订单簿
            orderbook = await exchange.fetch_order_book(test_symbol, limit=10)

            assert orderbook is not None
            assert "bids" in orderbook and "asks" in orderbook

            bid_count = len(orderbook.get("bids", []))
            ask_count = len(orderbook.get("asks", []))
            print(f"✓ [{config_path}] OrderBook {test_symbol}: {bid_count} bids, {ask_count} asks")

        finally:
            await exchange.close()


# ============================================================
# Executor 集成测试
# ============================================================

@pytest.mark.integration
class TestExecutorIntegration:
    """执行器集成测试"""

    @pytest.fixture
    def executor_configs(self):
        """获取 executor/demo 下的配置列表"""
        return get_demo_configs("executor")

    def test_demo_executor_configs_check(self, executor_configs):
        """检查 demo 执行器配置"""
        if len(executor_configs) == 0:
            pytest.skip("No demo executor configs found in conf/executor/demo/")
        print(f"Found {len(executor_configs)} demo executor configs: {executor_configs}")

    @pytest.mark.parametrize("config_path", get_demo_configs("executor") or ["skip"])
    def test_load_executor_config(self, config_path):
        """测试加载执行器配置"""
        if config_path == "skip":
            pytest.skip("No demo executor configs")

        from hft.executor.config import BaseExecutorConfig

        config = BaseExecutorConfig.load(config_path, cwd=str(PROJECT_ROOT))

        assert config is not None
        assert config.path == config_path

        print(f"✓ Loaded executor config: {config_path} (class={config.class_name})")


# ============================================================
# Strategy 集成测试
# ============================================================

@pytest.mark.integration
class TestStrategyIntegration:
    """策略集成测试"""

    @pytest.fixture
    def strategy_configs(self):
        """获取 strategy/demo 下的配置列表"""
        return get_demo_configs("strategy")

    def test_demo_strategy_configs_check(self, strategy_configs):
        """检查 demo 策略配置"""
        if len(strategy_configs) == 0:
            pytest.skip("No demo strategy configs found in conf/strategy/demo/")
        print(f"Found {len(strategy_configs)} demo strategy configs: {strategy_configs}")

    @pytest.mark.parametrize("config_path", get_demo_configs("strategy") or ["skip"])
    def test_load_strategy_config(self, config_path):
        """测试加载策略配置"""
        if config_path == "skip":
            pytest.skip("No demo strategy configs")

        from hft.strategy.config import BaseStrategyConfig

        config = BaseStrategyConfig.load(config_path, cwd=str(PROJECT_ROOT))

        assert config is not None
        assert config.path == config_path

        print(f"✓ Loaded strategy config: {config_path} (class={config.class_name})")


# ============================================================
# App 集成测试
# ============================================================

@pytest.mark.integration
class TestAppIntegration:
    """应用集成测试"""

    @pytest.fixture
    def app_configs(self):
        """获取 app/demo 下的配置列表"""
        return get_demo_configs("app")

    def test_demo_app_configs_check(self, app_configs):
        """检查 demo 应用配置"""
        if len(app_configs) == 0:
            pytest.skip("No demo app configs found in conf/app/demo/")
        print(f"Found {len(app_configs)} demo app configs: {app_configs}")

    @pytest.mark.parametrize("config_path", get_demo_configs("app") or ["skip"])
    def test_load_app_config(self, config_path):
        """测试加载应用配置"""
        if config_path == "skip":
            pytest.skip("No demo app configs")

        from hft.core.app.config import AppConfig

        config = AppConfig.load(config_path, cwd=str(PROJECT_ROOT))

        assert config is not None
        assert config.path == config_path

        print(f"✓ Loaded app config: {config_path}")


if __name__ == "__main__":
    # 运行集成测试: pytest -m integration -v -s
    pytest.main([__file__, "-v", "-s", "-m", "integration"])

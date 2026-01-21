"""
测试配置路径系统
"""
# pylint: disable=protected-access
import os
from pathlib import Path
from hft.core.config_path import (
    BaseConfigPath,
    AppConfigPath,
    StrategyConfigPath,
    ExecutorConfigPath,
    ExchangeConfigPath,
    ExchangeConfigPathGroup,
)


class TestBaseConfigPath:
    """测试 BaseConfigPath 基类"""

    def test_init(self):
        """测试初始化"""
        path = BaseConfigPath(name="test")
        assert path.name == "test"

    def test_get_file_path_default_root(self):
        """测试获取文件路径（默认根目录）"""
        # 清除环境变量
        old_root = os.environ.get('HFT_ROOT_PATH')
        if 'HFT_ROOT_PATH' in os.environ:
            del os.environ['HFT_ROOT_PATH']

        path = BaseConfigPath(name="test")
        file_path = path._get_file_path()

        assert file_path == Path(".") / "conf" / "test.yaml"

        # 恢复环境变量
        if old_root:
            os.environ['HFT_ROOT_PATH'] = old_root

    def test_get_file_path_custom_root(self):
        """测试获取文件路径（自定义根目录）"""
        old_root = os.environ.get('HFT_ROOT_PATH')
        os.environ['HFT_ROOT_PATH'] = "/custom/root"

        path = BaseConfigPath(name="test")
        file_path = path._get_file_path()

        assert file_path == Path("/custom/root") / "conf" / "test.yaml"

        # 恢复环境变量
        if old_root:
            os.environ['HFT_ROOT_PATH'] = old_root
        else:
            del os.environ['HFT_ROOT_PATH']

    def test_repr(self):
        """测试字符串表示"""
        path = BaseConfigPath(name="test")
        assert repr(path) == "BaseConfigPath(name='test')"


class TestConfigPathSubclasses:
    """测试 ConfigPath 子类"""

    def test_app_config_path(self):
        """测试 AppConfigPath"""
        path = AppConfigPath(name="main")
        assert path.class_dir == "conf/app/"
        assert path.name == "main"

    def test_strategy_config_path(self):
        """测试 StrategyConfigPath"""
        path = StrategyConfigPath(name="arbitrage")
        assert path.class_dir == "conf/strategy/"
        assert path.name == "arbitrage"

    def test_executor_config_path(self):
        """测试 ExecutorConfigPath"""
        path = ExecutorConfigPath(name="market")
        assert path.class_dir == "conf/executor/"
        assert path.name == "market"

    def test_exchange_config_path(self):
        """测试 ExchangeConfigPath"""
        path = ExchangeConfigPath(name="okx/main")
        assert path.class_dir == "conf/exchange/"
        assert path.name == "okx/main"


class TestExchangeConfigPathGroup:
    """测试 ExchangeConfigPathGroup"""

    def test_init(self):
        """测试初始化"""
        group = ExchangeConfigPathGroup(selectors=["okx/main", "binance/spot"])
        assert group.selectors == ["okx/main", "binance/spot"]

    def test_get_id_map_with_wildcard_selector(self):
        """测试通配符 selector（扫描所有配置）"""
        group = ExchangeConfigPathGroup(selectors=["*"])
        result = group.get_id_map("")

        # 应该包含实际存在的配置文件
        assert "binance" in result
        assert "okx" in result
        assert "demo/okx" in result
        assert "demo/binance" in result
        assert isinstance(result["okx"], ExchangeConfigPath)

    def test_get_id_map_with_include_selector(self):
        """测试包含 selector"""
        group = ExchangeConfigPathGroup(selectors=["demo/*"])
        result = group.get_id_map("")

        assert len(result) == 2
        assert "demo/okx" in result
        assert "demo/binance" in result
        assert "binance" not in result
        assert "okx" not in result

    def test_get_id_map_with_id_filter(self):
        """测试 id_filter 过滤"""
        group = ExchangeConfigPathGroup(selectors=["*"])
        result = group.get_id_map("demo/*")

        assert len(result) == 2
        assert "demo/okx" in result
        assert "demo/binance" in result
        assert "binance" not in result
        assert "okx" not in result

    def test_get_id_map_with_exclude_selector(self):
        """测试排除 selector"""
        group = ExchangeConfigPathGroup(selectors=["*", "!demo/*"])
        result = group.get_id_map("")

        assert "okx" in result
        assert "binance" in result
        assert "demo/okx" not in result
        assert "demo/binance" not in result

    def test_get_id_map_with_include_and_exclude(self):
        """测试组合过滤（selector + id_filter）"""
        group = ExchangeConfigPathGroup(selectors=["*"])
        result = group.get_id_map("demo/*,!demo/binance")

        assert len(result) == 1
        assert "demo/okx" in result
        assert "demo/binance" not in result
        assert "binance" not in result
        assert "okx" not in result

    def test_get_grouped_id_map(self):
        """测试分组 ID 映射"""
        group = ExchangeConfigPathGroup(selectors=["*"])
        result = group.get_grouped_id_map("")

        # 验证分组结构
        assert "okx" in result
        assert "binance" in result
        assert "demo" in result

        # 验证每个组包含的配置
        assert "okx" in result["okx"]  # 顶层 okx.yaml
        assert "binance" in result["binance"]  # 顶层 binance.yaml
        assert "demo/okx" in result["demo"]  # demo 目录下的 okx.yaml
        assert "demo/binance" in result["demo"]  # demo 目录下的 binance.yaml

    def test_get_grouped_map(self):
        """测试分组配置路径映射"""
        group = ExchangeConfigPathGroup(selectors=["*"])
        result = group.get_grouped_map("")

        # 验证分组结构
        assert "okx" in result
        assert "binance" in result
        assert "demo" in result

        # 验证返回的是 ExchangeConfigPath 对象
        assert all(isinstance(path, ExchangeConfigPath) for path in result["okx"])
        assert all(isinstance(path, ExchangeConfigPath) for path in result["binance"])

    def test_repr(self):
        """测试字符串表示"""
        group = ExchangeConfigPathGroup(selectors=["okx/main"])
        assert repr(group) == "ExchangeConfigPathGroup(selectors=['okx/main'])"

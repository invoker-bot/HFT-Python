"""
测试配置路径系统
"""
import os
import pytest
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
        group = ExchangeConfigPathGroup(paths=["okx/main", "binance/spot"])
        assert group.paths == ["okx/main", "binance/spot"]

    def test_get_id_map_no_filter(self):
        """测试获取所有配置（无过滤）"""
        group = ExchangeConfigPathGroup(paths=["okx/main", "okx/test", "binance/spot"])
        result = group.get_id_map("")

        assert len(result) == 3
        assert "okx/main" in result
        assert "okx/test" in result
        assert "binance/spot" in result
        assert isinstance(result["okx/main"], ExchangeConfigPath)

    def test_get_id_map_with_wildcard(self):
        """测试通配符过滤"""
        group = ExchangeConfigPathGroup(paths=["okx/main", "okx/test", "binance/spot"])
        result = group.get_id_map("*")

        assert len(result) == 3

    def test_get_id_map_with_include(self):
        """测试包含过滤"""
        group = ExchangeConfigPathGroup(paths=["okx/main", "okx/test", "binance/spot"])
        result = group.get_id_map("okx/*")

        assert len(result) == 2
        assert "okx/main" in result
        assert "okx/test" in result
        assert "binance/spot" not in result

    def test_get_id_map_with_exclude(self):
        """测试排除过滤"""
        group = ExchangeConfigPathGroup(paths=["okx/main", "okx/test", "binance/spot"])
        result = group.get_id_map("!okx/test")

        assert len(result) == 2
        assert "okx/main" in result
        assert "binance/spot" in result
        assert "okx/test" not in result

    def test_get_id_map_with_include_and_exclude(self):
        """测试组合过滤（包含 + 排除）"""
        group = ExchangeConfigPathGroup(paths=["okx/main", "okx/test", "binance/spot"])
        result = group.get_id_map("okx/*,!okx/test")

        assert len(result) == 1
        assert "okx/main" in result
        assert "okx/test" not in result
        assert "binance/spot" not in result

    def test_repr(self):
        """测试字符串表示"""
        group = ExchangeConfigPathGroup(paths=["okx/main"])
        assert repr(group) == "ExchangeConfigPathGroup(paths=['okx/main'])"

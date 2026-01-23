"""
应用配置模块

定义 AppCore 的配置类，支持：
- 从缓存恢复应用状态
- 配置主循环、健康检查、日志、缓存的时间间隔
- 策略列表配置
- 可选持久化配置
"""
import logging
from functools import cached_property
from typing import ClassVar

from pydantic import BaseModel, ClickHouseDsn, Field

from ...config.base import BaseConfig
from ..config_path import (ExchangeConfigPathGroup, ExecutorConfigPath,
                           StrategyConfigPath)
from .factory import AppFactory

logger = logging.getLogger(__name__)



class PersistConfig(BaseModel):
    """
    持久化配置

    控制哪些数据类型需要保存到 ClickHouse。
    默认全部启用，大数据量的 trades 和 orderbook 可以关闭。
    """
    order_bill: bool = Field(True, description="订单账单")
    funding_rate_bill: bool = Field(True, description="资金费率账单")
    balance_usd: bool = Field(True, description="账户余额快照")
    positions: bool = Field(True, description="持仓快照")
    balances: bool = Field(True, description="余额明细")
    ohlcv: bool = Field(True, description="K线数据")
    ticker: bool = Field(True, description="Ticker数据")
    trades: bool = Field(False, description="成交记录（数据量大，默认关闭）")
    order_book: bool = Field(False, description="订单簿（数据量大，默认关闭）")


class AppConfig(BaseConfig["AppCore"]):
    """
    应用核心配置类

    Attributes:
        interval: 主循环间隔（秒）
        health_check_interval: 健康检查间隔（秒）
        log_interval: 状态日志间隔（秒）
        cache_interval: 缓存保存间隔（秒）
        strategies: 策略配置路径列表
    """
    class_name: ClassVar[str] = "app"
    data_dir: ClassVar[str] = "data/app"
    class_dir: ClassVar[str] = "conf/app"

    @property
    def data_path(self) -> str:
        """获取数据缓存文件路径"""
        return path.join(self.data_dir, f"{self.path}.pkl")

    @classmethod
    def get_class_type(cls) -> Type["AppCore"]:
        """返回 AppCore 类型"""
        return AppCore

    interval: float = Field(1.0, description="主循环间隔（秒）")
    health_check_interval: float = Field(60.0, description="健康检查间隔（秒）")
    log_interval: float = Field(120.0, description="状态日志间隔（秒）")
    cache_interval: float = Field(300.0, description="缓存保存间隔（秒）")

    # 使用配置路径引用
    exchanges: ExchangeConfigPathGroup = Field(description="交易所配置路径组")
    strategy: StrategyConfigPath = Field(description="策略配置路径")
    executor: ExecutorConfigPath = Field(description="执行器配置路径")

    database_url: ClickHouseDsn | None = Field(None, description="ClickHouse 数据库连接 URL（可选）")
    persist: PersistConfig = Field(default_factory=PersistConfig, description="持久化配置")

    # Indicator 配置（Feature 0006）
    indicators: dict[str, dict] = Field(
        default_factory=dict,
        description="指标配置，格式: {indicator_id: {class: 类名, params: {...}}}"
    )

    # Scope 配置（Feature 0012）
    scopes: dict[str, dict] = Field(
        default_factory=dict,
        description=(
            "全局 Scope 配置，格式: "
            "{scope_class_id: {class_name: 类名, instance_id: 实例ID, vars: [...]}}"
        )
    )

    # 调试和测试
    debug: bool = Field(False, description="调试模式，验证流程而不实际下单")
    max_duration: float | None = Field(
        None, description="最大运行时长（秒），None 表示无限运行直到策略退出"
    )

    # 通知配置
    notify_urls: list[str] = Field(
        default_factory=list,
        description=(
            "Apprise 通知 URL 列表，支持 Telegram/Discord/Slack 等，"
            "参考 https://github.com/caronc/apprise"
        )
    )

"""
策略配置基类
"""
from typing import ClassVar, Type, Optional, TYPE_CHECKING
from pydantic import Field
from ..config.base import BaseConfig

if TYPE_CHECKING:
    from .base import BaseStrategy


class BaseStrategyConfig(BaseConfig["BaseStrategy"]):
    """
    策略配置基类

    提供：
    - 策略基本配置
    - 交易所引用
    - 交易对配置
    """
    class_dir: ClassVar[str] = "conf/strategy"

    # 基本配置
    name: str = Field(description="Strategy name")
    exchange_path: str = Field(description="Exchange config path (e.g., 'binance/main')")

    # 交易对配置
    symbols: list[str] = Field(default_factory=list, description="Trading symbols (e.g., 'BTC/USDT:USDT')")
    bases: list[str] = Field(default_factory=list, description="Base currencies (e.g., 'BTC', 'ETH')")
    quote: str = Field("USDT", description="Quote currency")
    market_type: str = Field("linear", description="Market type: spot, linear, inverse")

    # 仓位目标
    targets: dict[str, float] = Field(default_factory=dict, description="Position targets {symbol: amount}")

    @classmethod
    def get_class_type(cls) -> Type["BaseStrategy"]:
        from .base import BaseStrategy
        return BaseStrategy

"""
标准 Scope 类型定义

特殊变量（由 Scope 系统自动提供）：
- 所有 Scope: instance_id（等于 scope_instance_id）
- GlobalScope: app_core
- ExchangeClassScope: exchange_class
- ExchangeScope: exchange_id, exchange
- TradingPairClassScope: symbol, exchange_class（继承）
- TradingPairScope: exchange_id, symbol
"""
from typing import Optional, TYPE_CHECKING
from .base import BaseScope

if TYPE_CHECKING:
    from ...core.app.base import AppCore


class GlobalScope(BaseScope):
    """
    全局 Scope

    特性：
    - 没有 parent
    - 通常只有一个实例
    - 存储全局配置变量

    特殊变量：
    - instance_id: "global"
    - app_core: AppCore 实例引用
    """

    def __init__(
        self,
        scope_class_id: str,
        scope_instance_id: str = "global",
        app_core: "AppCore" = None,
        **kwargs
    ):
        super().__init__(
            scope_class_id=scope_class_id,
            scope_instance_id=scope_instance_id
        )
        # 特殊变量
        self.set_var("instance_id", scope_instance_id)
        if app_core is not None:
            self.set_var("app_core", app_core)


class ExchangeClassScope(BaseScope):
    """
    交易所类 Scope

    特性：
    - parent 是 GlobalScope
    - scope_instance_id 是交易所类名（如 "okx", "binance"）

    特殊变量：
    - instance_id: exchange class 名称
    - exchange_class: exchange class 名称
    """

    def __init__(
        self,
        scope_class_id: str,
        scope_instance_id: str,
        **kwargs
    ):
        super().__init__(
            scope_class_id=scope_class_id,
            scope_instance_id=scope_instance_id
        )
        # 特殊变量
        self.set_var("instance_id", scope_instance_id)
        self.set_var("exchange_class", scope_instance_id)


class ExchangeScope(BaseScope):
    """
    交易所实例 Scope

    特性：
    - parent 是 ExchangeClassScope
    - scope_instance_id 是交易所路径（如 "okx/main", "binance/spot"）

    特殊变量：
    - instance_id: exchange path
    - exchange_id: exchange path（如 "okx/a"）
    - exchange: exchange 实例引用
    """

    def __init__(
        self,
        scope_class_id: str,
        scope_instance_id: str,
        app_core: "AppCore" = None,
        **kwargs
    ):
        super().__init__(
            scope_class_id=scope_class_id,
            scope_instance_id=scope_instance_id
        )
        # 特殊变量
        self.set_var("instance_id", scope_instance_id)
        self.set_var("exchange_id", scope_instance_id)
        self.set_var("exchange_path", scope_instance_id)  # 向后兼容

        # 获取 exchange 实例
        if app_core is not None and hasattr(app_core, 'exchange_group'):
            for exchange in app_core.exchange_group.children.values():
                if exchange.config.path == scope_instance_id:
                    self.set_var("exchange", exchange)
                    break


class TradingPairClassScope(BaseScope):
    """
    交易对类 Scope

    特性：
    - parent 可以是 ExchangeClassScope 或 TradingPairClassGroupScope
    - scope_instance_id 格式为 "exchange_class-symbol"（如 "okx-ETH/USDT"）

    特殊变量：
    - instance_id: "exchange_class-symbol"
    - symbol: 交易对符号（如 "ETH/USDT"）
    - exchange_class: 继承自 parent
    """

    def __init__(
        self,
        scope_class_id: str,
        scope_instance_id: str,
        **kwargs
    ):
        super().__init__(
            scope_class_id=scope_class_id,
            scope_instance_id=scope_instance_id
        )
        # 特殊变量
        self.set_var("instance_id", scope_instance_id)
        # 解析 instance_id: "exchange_class-symbol"
        if '-' in scope_instance_id:
            parts = scope_instance_id.split('-', 1)
            self.set_var("symbol", parts[1])
        else:
            self.set_var("symbol", scope_instance_id)


class TradingPairScope(BaseScope):
    """
    交易对实例 Scope

    特性：
    - parent 是 TradingPairClassScope 或 ExchangeScope
    - scope_instance_id 格式为 "exchange_path-symbol"（如 "okx/a-ETH/USDT"）

    特殊变量：
    - instance_id: "exchange_path-symbol"
    - exchange_id: exchange path（如 "okx/a"）
    - symbol: 交易对符号（如 "ETH/USDT"）
    """

    def __init__(
        self,
        scope_class_id: str,
        scope_instance_id: str,
        exchange_path: Optional[str] = None,
        symbol: Optional[str] = None,
        **kwargs
    ):
        super().__init__(
            scope_class_id=scope_class_id,
            scope_instance_id=scope_instance_id
        )
        # 特殊变量
        self.set_var("instance_id", scope_instance_id)

        # 解析 exchange_path 和 symbol
        # 支持两种格式：
        # 1. "exchange_path-symbol" (新格式，如 "okx/a-ETH/USDT")
        # 2. "exchange_path:symbol" (旧格式，向后兼容)
        if exchange_path is None or symbol is None:
            if "-" in scope_instance_id:
                # 新格式：exchange_path-symbol
                parts = scope_instance_id.split("-", 1)
                exchange_path = exchange_path or parts[0]
                symbol = symbol or parts[1]
            elif ":" in scope_instance_id:
                # 旧格式：exchange_path:symbol
                parts = scope_instance_id.split(":", 1)
                exchange_path = exchange_path or parts[0]
                symbol = symbol or parts[1]

        if exchange_path:
            self.set_var("exchange_id", exchange_path)
            self.set_var("exchange_path", exchange_path)  # 向后兼容
        if symbol:
            self.set_var("symbol", symbol)


class TradingPairClassGroupScope(BaseScope):
    """
    交易对类分组 Scope（自定义 Scope）

    特性：
    - parent 是 ExchangeClassScope
    - children 是 TradingPairClassScope
    - scope_instance_id 是 group_id（如 "ETH", "BTC"）
    - 用于 MarketNeutralPositions 策略的分组计算

    特殊变量：
    - instance_id: group_id
    - group_id: 分组 ID
    """

    def __init__(
        self,
        scope_class_id: str,
        scope_instance_id: str,
        **kwargs
    ):
        super().__init__(
            scope_class_id=scope_class_id,
            scope_instance_id=scope_instance_id
        )
        # 特殊变量
        self.set_var("instance_id", scope_instance_id)
        self.set_var("group_id", scope_instance_id)

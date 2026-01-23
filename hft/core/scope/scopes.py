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
from typing import TYPE_CHECKING, Optional

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
    - 提供常用函数（所有子节点都会继承）

    特殊变量（由 BaseScope 自动提供）：
    - instance_id: scope_instance_id
    - class_id: scope_class_id
    - app_core: AppCore 实例引用

    特殊函数：
    - min, max, sum, len, abs, round
    - clip: 限制值在范围内
    - avg: 计算平均值
    """

    def __init__(
        self,
        scope_class_id: str,
        scope_instance_id: str = "global",
        app_core: "AppCore" = None,
    ):
        super().__init__(
            scope_class_id=scope_class_id,
            scope_instance_id=scope_instance_id,
            app_core=app_core
        )

    def initialize(self) -> None:
        """初始化 GlobalScope 的函数"""
        # 添加常用函数（所有子节点都会继承）
        self.set_function('min', min)
        self.set_function('max', max)
        self.set_function('sum', sum)
        self.set_function('len', len)
        self.set_function('abs', abs)
        self.set_function('round', round)
        self.set_function('clip', lambda x, min_val, max_val: max(min_val, min(x, max_val)))
        self.set_function('avg', lambda lst: sum(lst) / len(lst) if lst else 0)


class ExchangeClassScope(BaseScope):
    """
    交易所类 Scope

    特性：
    - parent 是 GlobalScope
    - scope_instance_id 是交易所类名（如 "okx", "binance"）

    特殊变量（由 BaseScope 自动提供）：
    - instance_id: scope_instance_id
    - class_id: scope_class_id

    额外变量：
    - exchange_class: 交易所类名（等于 scope_instance_id）
    """

    def __init__(
        self,
        scope_class_id: str,
        scope_instance_id: str,
        app_core: "AppCore" = None,
    ):
        super().__init__(
            scope_class_id=scope_class_id,
            scope_instance_id=scope_instance_id,
            app_core=app_core
        )

    def initialize(self) -> None:
        """初始化 ExchangeClassScope 的变量"""
        # 额外变量
        self.set_var("exchange_class", self.scope_instance_id)


class ExchangeScope(BaseScope):
    """
    交易所实例 Scope

    特性：
    - parent 是 ExchangeClassScope
    - scope_instance_id 是交易所路径（如 "okx/main", "binance/spot"）

    特殊变量（由 BaseScope 自动提供）：
    - instance_id: scope_instance_id
    - class_id: scope_class_id
    - app_core: AppCore 实例引用

    额外变量（静态，从 scope_instance_id 解析）：
    - exchange_id: 交易所路径（等于 scope_instance_id）
    - exchange_path: 交易所路径（向后兼容，等于 scope_instance_id）

    动态变量（需要时从 app_core 查找）：
    - exchange: exchange 实例引用（通过 app_core.exchange_group 动态查找）
    """

    def __init__(
        self,
        scope_class_id: str,
        scope_instance_id: str,
        app_core: "AppCore" = None,
    ):
        super().__init__(
            scope_class_id=scope_class_id,
            scope_instance_id=scope_instance_id,
            app_core=app_core
        )

    def initialize(self) -> None:
        """初始化 ExchangeScope 的变量"""
        # 静态变量：从 scope_instance_id 解析
        self.set_var("exchange_id", self.scope_instance_id)
        self.set_var("exchange_path", self.scope_instance_id)  # 向后兼容

        # 注意：exchange 实例引用不在这里设置，应该在需要时动态查找
        # 通过 app_core.exchange_group.children 查找


class TradingPairClassScope(BaseScope):
    """
    交易对类 Scope

    特性：
    - parent 可以是 ExchangeClassScope 或 TradingPairClassGroupScope
    - scope_instance_id 格式为 "exchange_class-symbol"（如 "okx-ETH/USDT"）

    特殊变量（由 BaseScope 自动提供）：
    - instance_id: scope_instance_id
    - class_id: scope_class_id

    额外变量：
    - symbol: 交易对符号（从 scope_instance_id 解析）
    - exchange_class: 继承自 parent
    """

    def __init__(
        self,
        scope_class_id: str,
        scope_instance_id: str,
        app_core: "AppCore" = None,
    ):
        super().__init__(
            scope_class_id=scope_class_id,
            scope_instance_id=scope_instance_id,
            app_core=app_core
        )

    def initialize(self) -> None:
        """初始化 TradingPairClassScope 的变量"""
        # 解析 instance_id: "exchange_class-symbol"
        if '-' in self.scope_instance_id:
            parts = self.scope_instance_id.split('-', 1)
            self.set_var("symbol", parts[1])
        else:
            self.set_var("symbol", self.scope_instance_id)


class TradingPairScope(BaseScope):
    """
    交易对实例 Scope

    特性：
    - parent 是 TradingPairClassScope 或 ExchangeScope
    - scope_instance_id 格式为 "exchange_path-symbol"（如 "okx/a-ETH/USDT"）

    特殊变量（由 BaseScope 自动提供）：
    - instance_id: scope_instance_id
    - class_id: scope_class_id

    额外变量：
    - exchange_id: exchange path（从 scope_instance_id 解析）
    - exchange_path: exchange path（向后兼容）
    - symbol: 交易对符号（从 scope_instance_id 解析）
    """

    def __init__(
        self,
        scope_class_id: str,
        scope_instance_id: str,
        app_core: "AppCore" = None,
    ):
        super().__init__(
            scope_class_id=scope_class_id,
            scope_instance_id=scope_instance_id,
            app_core=app_core
        )

    def initialize(self) -> None:
        """初始化 TradingPairScope 的变量"""
        # 解析 exchange_path 和 symbol
        # 支持两种格式：
        # 1. "exchange_path-symbol" (新格式，如 "okx/a-ETH/USDT")
        # 2. "exchange_path:symbol" (旧格式，向后兼容)
        exchange_path = None
        symbol = None

        if "-" in self.scope_instance_id:
            # 新格式：exchange_path-symbol
            parts = self.scope_instance_id.split("-", 1)
            exchange_path = parts[0]
            symbol = parts[1]
        elif ":" in self.scope_instance_id:
            # 旧格式：exchange_path:symbol
            parts = self.scope_instance_id.split(":", 1)
            exchange_path = parts[0]
            symbol = parts[1]

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

    特殊变量（由 BaseScope 自动提供）：
    - instance_id: scope_instance_id
    - class_id: scope_class_id

    额外变量：
    - group_id: 分组 ID（等于 scope_instance_id）
    """

    def __init__(
        self,
        scope_class_id: str,
        scope_instance_id: str,
        app_core: "AppCore" = None,
    ):
        super().__init__(
            scope_class_id=scope_class_id,
            scope_instance_id=scope_instance_id,
            app_core=app_core
        )

    def initialize(self) -> None:
        """初始化 TradingPairClassGroupScope 的变量"""
        # 额外变量
        self.set_var("group_id", self.scope_instance_id)

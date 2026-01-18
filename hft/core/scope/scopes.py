"""
标准 Scope 类型定义
"""
from typing import Optional
from .base import BaseScope


class GlobalScope(BaseScope):
    """
    全局 Scope

    特性：
    - 没有 parent
    - 通常只有一个实例
    - 存储全局配置变量
    """

    def __init__(self, scope_class_id: str, scope_instance_id: str = "global", parent: Optional[BaseScope] = None):
        super().__init__(
            scope_class_id=scope_class_id,
            scope_instance_id=scope_instance_id,
            parent=parent  # 始终为 None，但保持接口一致
        )


class ExchangeClassScope(BaseScope):
    """
    交易所类 Scope

    特性：
    - parent 是 GlobalScope
    - scope_instance_id 是交易所类名（如 "okx", "binance"）
    """

    def __init__(self, scope_class_id: str, scope_instance_id: str, parent: BaseScope):
        super().__init__(
            scope_class_id=scope_class_id,
            scope_instance_id=scope_instance_id,
            parent=parent
        )
        self.set_var("exchange_class", scope_instance_id)


class ExchangeScope(BaseScope):
    """
    交易所实例 Scope

    特性：
    - parent 是 ExchangeClassScope
    - scope_instance_id 是交易所路径（如 "okx/main", "binance/spot"）
    """

    def __init__(self, scope_class_id: str, scope_instance_id: str, parent: BaseScope):
        super().__init__(
            scope_class_id=scope_class_id,
            scope_instance_id=scope_instance_id,
            parent=parent
        )
        self.set_var("exchange_path", scope_instance_id)


class TradingPairClassScope(BaseScope):
    """
    交易对类 Scope

    特性：
    - parent 可以是 ExchangeClassScope 或 TradingPairClassGroupScope
    - scope_instance_id 是交易对符号（如 "ETH/USDT"）
    """

    def __init__(self, scope_class_id: str, scope_instance_id: str, parent: BaseScope):
        super().__init__(
            scope_class_id=scope_class_id,
            scope_instance_id=scope_instance_id,
            parent=parent
        )
        self.set_var("symbol", scope_instance_id)


class TradingPairScope(BaseScope):
    """
    交易对实例 Scope

    特性：
    - parent 是 TradingPairClassScope 或 ExchangeScope
    - scope_instance_id 是 "exchange_path:symbol"
    """

    def __init__(
        self,
        scope_class_id: str,
        scope_instance_id: str,
        parent: BaseScope,
        exchange_path: Optional[str] = None,
        symbol: Optional[str] = None
    ):
        super().__init__(
            scope_class_id=scope_class_id,
            scope_instance_id=scope_instance_id,
            parent=parent
        )
        # 如果没有提供，尝试从 scope_instance_id 解析
        if exchange_path is None or symbol is None:
            if ":" in scope_instance_id:
                parts = scope_instance_id.split(":", 1)
                exchange_path = exchange_path or parts[0]
                symbol = symbol or parts[1]

        if exchange_path:
            self.set_var("exchange_path", exchange_path)
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
    """

    def __init__(self, scope_class_id: str, scope_instance_id: str, parent: BaseScope):
        super().__init__(
            scope_class_id=scope_class_id,
            scope_instance_id=scope_instance_id,
            parent=parent
        )
        self.set_var("group_id", scope_instance_id)

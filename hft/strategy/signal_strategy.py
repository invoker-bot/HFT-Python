"""
交易信号模块（已弃用）

.. deprecated::
    本模块设计为事件驱动的信号系统，但新架构改用轮询模式：
    - 新架构：Strategy.get_trade_targets() 返回目标仓位字典
    - 新架构：Executor.on_tick() 轮询获取目标并执行
    TradeSignal 和 emit_signal() 机制未被实际采用。

原设计（未使用）：
- TradeSignal 是策略输出的唯一形式
- 使用 value + speed 双参数描述交易意图
- value: 目标仓位比例，与具体金额解耦
- speed: 执行紧急度，让执行器决定执行方式

原数据流（未使用）：
    Strategy -> TradeSignal -> StrategyGroup.emit_signal() -> Executor.on_signal()
"""
import time
from dataclasses import dataclass, field
from enum import Enum


class SignalSide(Enum):
    """
    信号方向枚举

    根据 TradeSignal.value 推导得出：
    - LONG: value > 0，做多
    - SHORT: value < 0，做空
    - FLAT: value == 0，平仓/空仓
    """
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


@dataclass
class TradeSignal:
    """
    交易信号（由 Strategy 发出，Executor 消费）

    这是策略和执行器之间的通信协议。策略只需要表达"想要什么仓位"，
    具体如何执行（市价/限价、分批/一次性）由执行器决定。

    核心字段：
        exchange_class: 交易所类型，用于路由到正确的交易所组
        symbol: 交易对，ccxt 标准格式
        value: 目标仓位比例 [-1.0, 1.0]
            - +1.0 = 最大仓位全做多
            - -1.0 = 最大仓位全做空
            - 0.0 = 平仓（无持仓）
            - 0.5 = 50% 最大仓位做多
        speed: 执行紧急度 [0.0, 1.0]
            - 1.0 = 立即市价执行（适合突发信号）
            - 0.5 = 常规执行
            - 0.0 = 可以慢慢限价执行（适合建仓）

    元数据字段：
        source: 发出信号的策略名称，用于追踪和调试
        timestamp: 信号生成时间
        reason: 人类可读的信号原因
        metadata: 扩展字段，存储策略特定数据
    """
    # === 必需字段 ===
    exchange_class: str  # 交易所类型: "okx", "binance", "bybit"
    symbol: str          # 交易对: "BTC/USDT:USDT", "ETH/USDT:USDT"
    value: float         # 目标仓位 [-1.0, 1.0]，自动裁剪到有效范围
    speed: float = 0.5   # 执行紧急度 [0.0, 1.0]，自动裁剪到有效范围

    # === 元数据字段 ===
    source: str = ""     # 策略名称，便于追踪信号来源
    timestamp: float = field(default_factory=time.time)  # 信号生成时间
    reason: str = ""     # 信号原因，用于日志和复盘
    metadata: dict = field(default_factory=dict)  # 扩展数据

    def __post_init__(self):
        """
        初始化后处理：裁剪 value 和 speed 到有效范围

        这确保了无论输入什么值，最终都是有效的：
        - value: [-1.0, 1.0]
        - speed: [0.0, 1.0]
        """
        self.value = max(-1.0, min(1.0, self.value))
        self.speed = max(0.0, min(1.0, self.speed))

    @property
    def side(self) -> SignalSide:
        """
        根据 value 推导交易方向

        Returns:
            SignalSide.LONG: value > 0
            SignalSide.SHORT: value < 0
            SignalSide.FLAT: value == 0
        """
        if self.value > 0:
            return SignalSide.LONG
        elif self.value < 0:
            return SignalSide.SHORT
        return SignalSide.FLAT

    @property
    def is_urgent(self) -> bool:
        """
        是否是紧急信号

        紧急信号（speed > 0.8）通常需要立即执行，
        执行器应该使用市价单而非限价单。
        """
        return self.speed > 0.8

    @property
    def is_close(self) -> bool:
        """
        是否是平仓信号

        当 |value| < 0.01 时视为平仓，
        这个小阈值是为了处理浮点数精度问题。
        """
        return abs(self.value) < 0.01

    def __repr__(self) -> str:
        """格式化输出，便于日志和调试"""
        return (
            f"TradeSignal({self.exchange_class}/{self.symbol}, "
            f"value={self.value:.2f}, speed={self.speed:.2f}, "
            f"source={self.source})"
        )

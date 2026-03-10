"""
StaticPositionsStrategy - 静态仓位策略

一个简单的策略，用于将账户仓位保持在目标 USD 价值。

功能：
- 支持合约持仓（如 BTC/USDT:USDT）
- 支持现货持仓（如 BTC/USDT）
- 可配置是否达到目标后退出

Feature 0008 Phase 4:
- 支持 targets 通用字段
- 支持表达式求值
- 支持多 Exchange 目标匹配

Feature 0011:
- 重命名 keep_positions -> static_positions
- 支持 target_pairs + target 展开式写法
- 去特殊化：position_usd/speed 等字段为普通通用字典字段

Example Config (旧格式，仍支持):
    class_name: static_positions
    exchange_path: okx/main
    positions_usd:
      BTC/USDT:USDT: 1000

Example Config (新格式，Feature 0008):
    class_name: static_positions
    requires:
      - equation
    targets:
      - exchange: '*'
        exchange_class: okx
        symbol: BTC/USDT:USDT
        position_usd: '0.6 * equation_usd'
        speed: 0.5

Example Config (展开式写法，Feature 0011):
    class_name: static_positions
    target_pairs:
      - BTC/USDT
      - ETH/USDT
    target:
      exchange_class: okx
      position_usd: 1000
      speed: 0.1
"""
from typing import TYPE_CHECKING, ClassVar
from .base import BaseStrategy # , StrategyOutput, TargetPositions
from .config import BaseStrategyConfig  # , TargetDefinition

if TYPE_CHECKING:
    from ..exchange.base import BaseExchange


# class TargetPairDefinition(TargetDefinition):
#     """
#     target_pairs 中的单个条目定义（Feature 0011）
#
#     支持两种格式：
#     - string: "BTC/USDT" -> {"symbol": "BTC/USDT", "exchange_class": "*"}
#     - dict: {"symbol": "BTC/USDT", "exchange_class": "okx"}
#     """
#     # 继承 TargetDefinition，但 position_usd 等字段在展开时由 target 提供
#     pass


class StaticPositionsStrategyConfig(BaseStrategyConfig):
    """
    静态仓位策略配置（Feature 0011）

    支持三种配置方式：

    旧格式（向后兼容）：
        exchange_path: okx/main
        positions_usd:
          BTC/USDT:USDT: 1000

    新格式（Feature 0008）：
        requires:
          - equation
        targets:
          - exchange: '*'
            exchange_class: okx
            symbol: BTC/USDT:USDT
            position_usd: '0.6 * equation_usd'

    展开式写法（Feature 0011）：
        target_pairs:
          - BTC/USDT
          - ETH/USDT
        target:
          exchange_class: okx
          position_usd: 1000
          speed: 0.1

    Attributes:
        exchange_path: 交易所配置路径（旧格式使用）
        positions_usd: 目标仓位字典（旧格式使用）
        targets: 目标定义列表（新格式使用，Feature 0008）
        target_pairs: 目标交易对列表（展开式写法，Feature 0011）
        target: 目标模板（与 target_pairs 配合使用，Feature 0011）
        exit_on_target: 达到目标仓位后是否退出
        tolerance: 仓位容忍度
        speed: 默认执行紧急度（旧格式使用）
    """
    class_name: ClassVar[str] = "static_positions"

    # 新格式字段（Feature 0008）
    # targets: list[TargetDefinition] = Field(
    #     default_factory=list,
    #     description="目标定义列表（Feature 0008 新格式）"
    # )

    # 展开式写法字段（Feature 0011）
    # target_pairs: list[Union[str, dict[str, Any]]] = Field(
    #     default_factory=list,
    #     description="目标交易对列表（Feature 0011 展开式写法）"
    # )
    # target: Optional[dict[str, Any]] = Field(
    #     None,
    #     description="目标模板（与 target_pairs 配合使用，Feature 0011）"
    # )

    # 通用字段
    # exit_on_target: bool = Field(
    #     True,
    #     description="Exit strategy after reaching target positions"
    # )
    # tolerance: float = Field(
    #     0.05,
    #     description="Position tolerance (0.05 = 5%), within this range is considered on target"
    # )
    @classmethod
    def get_class_type(cls):
        return StaticPositionsStrategy


class StaticPositionsStrategy(BaseStrategy):
    """
    静态仓位策略（Feature 0011）

    策略职责：
    - 通过 get_target_positions_usd() 返回配置的目标仓位
    - 监控仓位是否达标，达标后可选择退出

    执行职责（由 Executor 处理）：
    - 获取当前仓位
    - 计算与目标的差值
    - 执行交易

    Feature 0008 Phase 4:
    - 支持 targets 通用字段（position_usd, position_amount, max_position_usd 等）
    - 支持表达式求值（如 '0.6 * equation_usd'）
    - 支持多 Exchange 目标匹配（通过 exchange 和 exchange_class 模式）
    """

    async def on_tick(self) -> bool:
        """
        检查仓位是否达标

        Returns:
            True 如果策略应该退出（exit_on_target=True 且所有仓位达标）
        """
        return

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
from typing import ClassVar, Type, Optional, Any, Union, TYPE_CHECKING
from functools import cached_property
from fnmatch import fnmatch
from pydantic import Field, model_validator
from rich.console import Console
from rich.table import Table
from .base import BaseStrategy, TargetPositions, StrategyOutput
from .config import BaseStrategyConfig, TargetDefinition

if TYPE_CHECKING:
    from ..exchange.base import BaseExchange


class TargetPairDefinition(TargetDefinition):
    """
    target_pairs 中的单个条目定义（Feature 0011）

    支持两种格式：
    - string: "BTC/USDT" -> {"symbol": "BTC/USDT", "exchange_class": "*"}
    - dict: {"symbol": "BTC/USDT", "exchange_class": "okx"}
    """
    # 继承 TargetDefinition，但 position_usd 等字段在展开时由 target 提供
    pass


class StaticPositionsStrategyConfig(BaseStrategyConfig):
    """
    静态仓位策略配置（Feature 0011 重命名自 KeepPositionsStrategyConfig）

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

    # 旧格式字段（向后兼容）
    exchange_path: Optional[str] = Field(
        None,
        description="Exchange config path (e.g., 'okx/main'), 旧格式使用"
    )
    positions_usd: dict[str, float] = Field(
        default_factory=dict,
        description="Target positions in USD {symbol: usd_value}, 旧格式使用"
    )
    speed: float = Field(
        0.8,
        description="Default execution urgency [0.0, 1.0], 旧格式使用"
    )

    # 新格式字段（Feature 0008）
    targets: list[TargetDefinition] = Field(
        default_factory=list,
        description="目标定义列表（Feature 0008 新格式）"
    )

    # 展开式写法字段（Feature 0011）
    target_pairs: list[Union[str, dict[str, Any]]] = Field(
        default_factory=list,
        description="目标交易对列表（Feature 0011 展开式写法）"
    )
    target: Optional[dict[str, Any]] = Field(
        None,
        description="目标模板（与 target_pairs 配合使用，Feature 0011）"
    )

    # 通用字段
    exit_on_target: bool = Field(
        True,
        description="Exit strategy after reaching target positions"
    )
    tolerance: float = Field(
        0.05,
        description="Position tolerance (0.05 = 5%), within this range is considered on target"
    )

    @model_validator(mode='after')
    def expand_target_pairs(self) -> 'StaticPositionsStrategyConfig':
        """
        展开 target_pairs + target 为 targets 列表（Feature 0011）

        展开规则：
        - target_pairs 中的 string 条目转换为 {"symbol": string, "exchange_class": "*"}
        - target_pairs 中的 dict 条目直接使用
        - 展开的 targets 与 target 模板合并
        - 展开后的 targets 追加到已有 targets 列表
        """
        if not self.target_pairs:
            return self

        target_template = self.target or {}

        for pair in self.target_pairs:
            if isinstance(pair, str):
                # string 简写：symbol 为 pair，exchange_class 为 *
                pair_dict = {"symbol": pair, "exchange_class": "*", "exchange": "*"}
            elif isinstance(pair, dict):
                # dict 格式：直接使用，确保有默认值
                pair_dict = {
                    "exchange_class": "*",
                    "exchange": "*",
                    **pair
                }
            else:
                continue

            # 合并 pair_dict 和 target_template
            # pair_dict 中的值优先（特定于此交易对的配置）
            merged = {**target_template, **pair_dict}

            # 创建 TargetDefinition
            target_def = TargetDefinition(**merged)
            self.targets.append(target_def)

        return self

    @classmethod
    def get_class_type(cls) -> Type["StaticPositionsStrategy"]:
        return StaticPositionsStrategy

    @cached_property
    def instance(self) -> "StaticPositionsStrategy":
        return StaticPositionsStrategy(config=self)


class StaticPositionsStrategy(BaseStrategy):
    """
    静态仓位策略（Feature 0011 重命名自 KeepPositionsStrategy）

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

    Feature 0011:
    - 重命名 keep_positions -> static_positions
    - 支持 target_pairs + target 展开式写法
    """

    def __init__(self, config: StaticPositionsStrategyConfig):
        super().__init__(config)
        self.config: StaticPositionsStrategyConfig = config
        # 追踪已达标的 symbol
        self._targets_reached: set[str] = set()

    @property
    def exchange_group(self):
        """获取交易所组"""
        return self.root.exchange_group

    def _get_exchange(self, exchange_path: Optional[str] = None) -> Optional["BaseExchange"]:
        """根据配置路径获取交易所实例"""
        path = exchange_path or self.config.exchange_path
        if path is None:
            return None
        for exchange in self.exchange_group.children.values():
            if exchange.config.path == path:
                return exchange
        return None

    def _get_all_exchanges(self) -> list["BaseExchange"]:
        """获取所有可用的交易所实例"""
        return list(self.exchange_group.children.values())

    def _match_target_to_exchanges(
        self,
        target: TargetDefinition,
    ) -> list[tuple[str, str]]:
        """
        匹配 target 到 exchange 列表

        Feature 0008 Phase 4: 多 Exchange 目标匹配

        匹配规则：
        - exchange: 匹配 exchange path，'*' 表示所有
        - exchange_class: 匹配 exchange class_name，'*' 表示所有

        Returns:
            [(exchange_path, symbol), ...] 匹配到的目标列表
        """
        matches = []
        for exchange in self._get_all_exchanges():
            # 检查 exchange_path 匹配
            if target.exchange != '*':
                if not fnmatch(exchange.config.path, target.exchange):
                    continue

            # 检查 exchange_class 匹配
            if target.exchange_class != '*':
                if not fnmatch(exchange.class_name, target.exchange_class):
                    continue

            matches.append((exchange.config.path, target.symbol))

        return matches

    def _evaluate_condition(
        self,
        condition: Optional[str],
        context: dict[str, Any],
        label: str = "condition"
    ) -> bool:
        """
        求值 condition 表达式（Feature 0011）

        Args:
            condition: 条件表达式，None 等价 True
            context: 求值上下文
            label: 用于日志的标签

        Returns:
            True: condition 为 None 或求值为 True
            False: condition 求值为 False 或发生异常
        """
        if condition is None:
            return True

        try:
            result = self._safe_eval(condition, context)
            if result is None:
                self.logger.debug(
                    "[%s] condition '%s' evaluated to None, treating as False",
                    label, condition
                )
                return False
            return bool(result)
        except Exception as e:
            self.logger.warning(
                "[%s] Failed to evaluate condition '%s': %s, treating as False",
                label, condition, e
            )
            return False

    def _evaluate_target_fields(
        self,
        target: TargetDefinition,
        exchange_path: str,
        symbol: str,
    ) -> dict[str, Any]:
        """
        求值 target 中的表达式字段

        Feature 0008 Phase 4: targets 字段表达式求值

        Returns:
            {字段名: 求值后的值, ...}
        """
        # 收集上下文变量
        context = self.collect_context_vars(exchange_path, symbol)

        result: dict[str, Any] = {}

        # 定义需要求值的字段
        expression_fields = [
            'position_usd',
            'position_amount',
            'max_position_usd',
        ]

        # 处理表达式字段
        for field_name in expression_fields:
            field_value = getattr(target, field_name, None)
            if field_value is not None:
                try:
                    # 尝试作为表达式求值
                    evaluated = self._safe_eval(str(field_value), context)
                    if evaluated is not None:
                        result[field_name] = evaluated
                except Exception as e:
                    self.logger.warning(
                        "Failed to evaluate %s expression '%s': %s",
                        field_name, field_value, e
                    )

        # 处理 speed（可以是数字或表达式）
        if target.speed is not None:
            result['speed'] = target.speed

        # 处理额外字段（通过 model_extra 访问）
        if hasattr(target, 'model_extra') and target.model_extra:
            for field_name, field_value in target.model_extra.items():
                try:
                    if isinstance(field_value, str):
                        evaluated = self._safe_eval(field_value, context)
                        if evaluated is not None:
                            result[field_name] = evaluated
                    else:
                        result[field_name] = field_value
                except Exception as e:
                    self.logger.warning(
                        "Failed to evaluate extra field %s: %s",
                        field_name, e
                    )

        return result

    def get_target_positions_usd(self) -> Union[TargetPositions, StrategyOutput]:
        """
        返回配置的目标仓位

        支持三种格式：
        1. 旧格式（向后兼容）：使用 exchange_path + positions_usd
        2. 新格式（Feature 0008）：使用 targets 列表
        3. 展开式（Feature 0011）：target_pairs + target 已在配置加载时展开为 targets

        Feature 0011: condition 门控
        - 全局 condition: 为 False 时返回空 {}
        - target 级 condition: 为 False 时忽略该 target

        Returns:
            旧格式：{(exchange_path, symbol): (position_usd, speed)}
            新格式：{(exchange_path, symbol): {"position_usd": ..., "speed": ..., ...}}
        """
        # 新格式：使用 targets 列表（包括展开式转换后的）
        if self.config.targets:
            output: StrategyOutput = {}

            # Feature 0011: 检查全局 condition
            # 注意：全局 condition 在没有特定 exchange/symbol 上下文时求值
            # 使用通用上下文（不含特定交易对的数据）
            if self.config.condition is not None:
                # 使用第一个 target 的 exchange/symbol 作为上下文参考
                # 或者创建一个通用上下文
                first_target = self.config.targets[0] if self.config.targets else None
                if first_target:
                    matches = self._match_target_to_exchanges(first_target)
                    if matches:
                        ex_path, sym = matches[0]
                        global_context = self.collect_context_vars(ex_path, sym)
                        if not self._evaluate_condition(
                            self.config.condition,
                            global_context,
                            label="global"
                        ):
                            self.logger.debug(
                                "Global condition '%s' is False, returning empty targets",
                                self.config.condition
                            )
                            return {}

            for target in self.config.targets:
                # 匹配 exchanges
                matches = self._match_target_to_exchanges(target)

                for exchange_path, symbol in matches:
                    # 收集上下文
                    context = self.collect_context_vars(exchange_path, symbol)

                    # Feature 0011: 检查 target 级 condition
                    if not self._evaluate_condition(
                        target.condition,
                        context,
                        label=f"{exchange_path}:{symbol}"
                    ):
                        self.logger.debug(
                            "[%s:%s] Target condition '%s' is False, skipping",
                            exchange_path, symbol, target.condition
                        )
                        continue

                    # 求值表达式字段
                    fields = self._evaluate_target_fields(
                        target, exchange_path, symbol
                    )

                    if fields:
                        key = (exchange_path, symbol)
                        if key in output:
                            # 合并字段（后面的覆盖前面的）
                            output[key].update(fields)
                        else:
                            output[key] = fields

            return output

        # 旧格式：使用 exchange_path + positions_usd
        if not self.config.positions_usd:
            return {}

        if self.config.exchange_path is None:
            self.logger.warning("exchange_path not configured for legacy format")
            return {}

        return {
            (self.config.exchange_path, symbol): (usd, self.config.speed)
            for symbol, usd in self.config.positions_usd.items()
        }

    async def on_start(self):
        await super().on_start()
        if self.config.targets:
            self.logger.info(
                "StaticPositionsStrategy started: targets=%d, exit=%s",
                len(self.config.targets),
                self.config.exit_on_target
            )
        else:
            self.logger.info(
                "StaticPositionsStrategy started: exchange=%s, positions=%s, exit=%s",
                self.config.exchange_path,
                self.config.positions_usd,
                self.config.exit_on_target
            )

    def _get_target_positions_for_check(self) -> dict[tuple[str, str], float]:
        """
        获取需要检查的目标仓位（仅 position_usd 字段）

        用于 on_tick 检查是否达标

        Returns:
            {(exchange_path, symbol): target_usd}
        """
        result: dict[tuple[str, str], float] = {}

        if self.config.targets:
            # 新格式
            for target in self.config.targets:
                matches = self._match_target_to_exchanges(target)
                for exchange_path, symbol in matches:
                    fields = self._evaluate_target_fields(target, exchange_path, symbol)
                    if 'position_usd' in fields:
                        result[(exchange_path, symbol)] = fields['position_usd']
        elif self.config.positions_usd and self.config.exchange_path:
            # 旧格式
            for symbol, usd in self.config.positions_usd.items():
                result[(self.config.exchange_path, symbol)] = usd

        return result

    async def on_tick(self) -> bool:
        """
        检查仓位是否达标

        Returns:
            True 如果策略应该退出（exit_on_target=True 且所有仓位达标）
        """
        target_positions = self._get_target_positions_for_check()

        if not target_positions:
            # 没有配置任何目标
            if not self.config.targets and not self.config.positions_usd:
                self.logger.warning("No positions configured, exiting")
                return True
            # targets 可能暂时没有匹配到任何 exchange，继续等待
            return False

        if not self.config.exit_on_target:
            return False  # 不退出，持续运行

        try:
            all_on_target = True

            for (exchange_path, symbol), target_usd in target_positions.items():
                # 获取交易所
                exchange = self._get_exchange(exchange_path)
                if exchange is None:
                    all_on_target = False
                    continue

                # 获取当前仓位
                positions = await exchange.medal_fetch_positions()
                current_amount = positions.get(symbol, 0.0)

                # 需要价格来计算 USD 价值
                try:
                    ticker = await exchange.fetch_ticker(symbol)
                    price = ticker.get('last', 0)
                    if price <= 0:
                        all_on_target = False
                        continue
                except Exception:
                    all_on_target = False
                    continue

                current_usd = current_amount * price

                # 检查是否在容忍度范围内
                if abs(target_usd) > 0:
                    diff_ratio = abs(current_usd - target_usd) / abs(target_usd)
                else:
                    diff_ratio = abs(current_usd) / 100 if current_usd != 0 else 0

                key = f"{exchange_path}:{symbol}"
                if diff_ratio > self.config.tolerance:
                    all_on_target = False
                    self._targets_reached.discard(key)
                elif key not in self._targets_reached:
                    self.logger.info(
                        "[%s] Position on target: %.2f USD (target: %.2f USD)",
                        key, current_usd, target_usd
                    )
                    self._targets_reached.add(key)

            if all_on_target:
                self.logger.info("All positions on target, strategy finished")
                return True

        except Exception as e:
            self.logger.warning("Error checking positions: %s", e)

        return False

    @property
    def log_state_dict(self) -> dict:
        if self.config.targets:
            return {
                "targets": len(self.config.targets),
                "targets_reached": len(self._targets_reached),
            }
        return {
            "positions": len(self.config.positions_usd),
            "targets_reached": len(self._targets_reached),
        }

    def _build_table(self) -> Table:
        """构建仓位状态表格"""
        # 使用新格式或旧格式的标题
        if self.config.targets:
            title = "Static Positions (Targets)"
        else:
            title = f"Static Positions ({self.config.exchange_path})"

        table = Table(
            title=title,
            show_header=True,
            header_style="bold cyan",
        )

        table.add_column("Exchange", width=15)
        table.add_column("Symbol", width=18)
        table.add_column("Target", width=12, justify="right")
        table.add_column("Current", width=12, justify="right")
        table.add_column("Diff", width=10, justify="right")
        table.add_column("Status", width=10)

        # 获取目标仓位
        target_positions = self._get_target_positions_for_check()

        for (exchange_path, symbol), target_usd in target_positions.items():
            target_str = f"${target_usd:,.0f}"

            exchange = self._get_exchange(exchange_path)
            has_data = exchange is not None and exchange.ready

            if not has_data:
                table.add_row(
                    exchange_path,
                    symbol,
                    target_str,
                    "[dim]--[/dim]",
                    "[dim]--[/dim]",
                    "[dim]...[/dim]"
                )
                continue

            # 获取当前仓位
            current_usd = 0.0
            try:
                positions = exchange._positions
                current_amount = positions.get(symbol, 0.0)
                ticker = exchange._tickers.get(symbol, {})
                price = ticker.get('last', 0)
                if price > 0:
                    current_usd = current_amount * price
            except Exception:
                pass

            current_str = f"${current_usd:,.0f}"
            diff = current_usd - target_usd
            diff_str = f"${diff:+,.0f}"

            # 检查是否达标
            if abs(target_usd) > 0:
                diff_ratio = abs(diff) / abs(target_usd)
            else:
                diff_ratio = abs(current_usd) / 100 if current_usd != 0 else 0

            on_target = diff_ratio <= self.config.tolerance

            if on_target:
                status = "[green]OK[/green]"
                current_str = f"[green]{current_str}[/green]"
            else:
                status = "[yellow]...[/yellow]"
                if diff > 0:
                    diff_str = f"[red]{diff_str}[/red]"
                else:
                    diff_str = f"[yellow]{diff_str}[/yellow]"

            table.add_row(exchange_path, symbol, target_str, current_str, diff_str, status)

        return table

    def log_state(self, console: Console, recursive: bool = True):
        """输出状态到控制台"""
        super().log_state(console, recursive)
        if self.config.positions_usd or self.config.targets:
            table = self._build_table()
            console.print(table)

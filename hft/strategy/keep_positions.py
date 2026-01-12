"""
KeepPositionsStrategy - 保持目标仓位策略

一个简单的策略，用于将账户仓位保持在目标 USD 价值。

功能：
- 支持合约持仓（如 BTC/USDT:USDT）
- 支持现货持仓（如 BTC/USDT）
- 可配置是否达到目标后退出

Example Config (conf/strategy/keep_positions/main.yaml):
    class_name: keep_positions
    exchange_path: okx/main
    exit_on_target: true
    tolerance: 0.05
    speed: 0.8
    positions_usd:
      BTC/USDT:USDT: 1000   # 持有价值 1000 USD 的 BTC 多单
      ETH/USDT:USDT: -500   # 持有价值 500 USD 的 ETH 空单
"""
from typing import ClassVar, Type, Optional, TYPE_CHECKING
from functools import cached_property
from pydantic import Field
from rich.console import Console
from rich.table import Table
from .base import BaseStrategy, TargetPositions
from .config import BaseStrategyConfig

if TYPE_CHECKING:
    from ..exchange.base import BaseExchange


class KeepPositionsStrategyConfig(BaseStrategyConfig):
    """
    保持仓位策略配置

    Attributes:
        exchange_path: 交易所配置路径（如 'okx/main', 'binance/spot'）
        positions_usd: 目标仓位字典 {symbol: usd_value}
            - 正数表示多仓
            - 负数表示空仓
        exit_on_target: 达到目标仓位后是否退出
        tolerance: 仓位容忍度（0.05 表示 5%），在此范围内视为达标
        speed: 执行紧急度 [0.0, 1.0]，传递给 Executor
    """
    class_name: ClassVar[str] = "keep_positions"

    exchange_path: str = Field(..., description="Exchange config path (e.g., 'okx/main')")
    positions_usd: dict[str, float] = Field(
        default_factory=dict,
        description="Target positions in USD {symbol: usd_value}"
    )
    exit_on_target: bool = Field(
        True,
        description="Exit strategy after reaching target positions"
    )
    tolerance: float = Field(
        0.05,
        description="Position tolerance (0.05 = 5%), within this range is considered on target"
    )
    speed: float = Field(
        0.8,
        description="Execution urgency [0.0, 1.0], passed to Executor"
    )

    @classmethod
    def get_class_type(cls) -> Type["KeepPositionsStrategy"]:
        return KeepPositionsStrategy

    @cached_property
    def instance(self) -> "KeepPositionsStrategy":
        return KeepPositionsStrategy(config=self)


class KeepPositionsStrategy(BaseStrategy):
    """
    保持目标仓位策略

    策略职责：
    - 通过 get_target_positions_usd() 返回配置的目标仓位
    - 监控仓位是否达标，达标后可选择退出

    执行职责（由 Executor 处理）：
    - 获取当前仓位
    - 计算与目标的差值
    - 执行交易

    工作流程：
    1. Executor 调用 get_target_positions_usd() 获取目标
    2. Executor 计算差值并执行交易
    3. on_tick() 检查是否达标，决定是否退出
    """

    def __init__(self, config: KeepPositionsStrategyConfig):
        super().__init__(config)
        self.config: KeepPositionsStrategyConfig = config
        # 追踪已达标的 symbol
        self._targets_reached: set[str] = set()

    @property
    def exchange_group(self):
        """获取交易所组"""
        return self.root.exchange_group

    def _get_exchange(self) -> Optional["BaseExchange"]:
        """根据配置路径获取交易所实例"""
        for exchange in self.exchange_group.children.values():
            if exchange.config.path == self.config.exchange_path:
                return exchange
        return None

    def get_target_positions_usd(self) -> TargetPositions:
        """
        返回配置的目标仓位

        Returns:
            {(exchange_path, symbol): (position_usd, speed)}
        """
        if not self.config.positions_usd:
            return {}

        return {
            (self.config.exchange_path, symbol): (usd, self.config.speed)
            for symbol, usd in self.config.positions_usd.items()
        }

    async def on_start(self):
        await super().on_start()
        self.logger.info(
            "KeepPositionsStrategy started: exchange=%s, positions=%s, exit=%s",
            self.config.exchange_path,
            self.config.positions_usd,
            self.config.exit_on_target
        )

    async def on_tick(self) -> bool:
        """
        检查仓位是否达标

        Returns:
            True 如果策略应该退出（exit_on_target=True 且所有仓位达标）
        """
        if not self.config.positions_usd:
            self.logger.warning("No positions configured, exiting")
            return True

        if not self.config.exit_on_target:
            return False  # 不退出，持续运行

        # 获取交易所检查仓位
        exchange = self._get_exchange()
        if exchange is None:
            return False  # 交易所未就绪，继续等待

        try:
            # 获取当前仓位
            positions = await exchange.medal_fetch_positions()
            all_on_target = True

            for symbol, target_usd in self.config.positions_usd.items():
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

                if diff_ratio > self.config.tolerance:
                    all_on_target = False
                    self._targets_reached.discard(symbol)
                elif symbol not in self._targets_reached:
                    self.logger.info(
                        "[%s] Position on target: %.2f USD (target: %.2f USD)",
                        symbol, current_usd, target_usd
                    )
                    self._targets_reached.add(symbol)

            if all_on_target:
                self.logger.info("All positions on target, strategy finished")
                return True

        except Exception as e:
            self.logger.warning("Error checking positions: %s", e)

        return False

    @property
    def log_state_dict(self) -> dict:
        return {
            "positions": len(self.config.positions_usd),
            "targets_reached": len(self._targets_reached),
        }

    def _build_table(self) -> Table:
        """构建仓位状态表格"""
        table = Table(
            title=f"Positions ({self.config.exchange_path})",
            show_header=True,
            header_style="bold cyan",
        )

        table.add_column("Symbol", width=18)
        table.add_column("Target", width=12, justify="right")
        table.add_column("Current", width=12, justify="right")
        table.add_column("Diff", width=10, justify="right")
        table.add_column("Status", width=10)

        exchange = self._get_exchange()
        has_data = exchange is not None and exchange.ready

        for symbol, target_usd in self.config.positions_usd.items():
            target_str = f"${target_usd:,.0f}"

            if not has_data:
                table.add_row(
                    symbol,
                    target_str,
                    "[dim]--[/dim]",
                    "[dim]--[/dim]",
                    "[dim]⏳[/dim]"
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
                status = "[green]✓[/green]"
                current_str = f"[green]{current_str}[/green]"
            else:
                status = "[yellow]...[/yellow]"
                if diff > 0:
                    diff_str = f"[red]{diff_str}[/red]"
                else:
                    diff_str = f"[yellow]{diff_str}[/yellow]"

            table.add_row(symbol, target_str, current_str, diff_str, status)

        return table

    def log_state(self, console: Console, recursive: bool = True):
        """输出状态到控制台"""
        super().log_state(console, recursive)
        if self.config.positions_usd:
            table = self._build_table()
            console.print(table)

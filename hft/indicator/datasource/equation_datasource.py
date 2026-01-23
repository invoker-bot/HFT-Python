"""
账户权益数据源

Feature 0008: Strategy 数据驱动增强

MedalEquationDataSource 是 ExchangePath 级别的 Indicator，
用于获取特定交易所实例的账户总权益（USD）。

层级：
- Global: GlobalFundingRateIndicator
- ExchangePath: MedalEquationDataSource ← 本模块
- Pair: TickerDataSource, TradesDataSource, etc.
"""
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from ..base import BaseIndicator

if TYPE_CHECKING:
    from ...core.app.core import AppCore


@dataclass
class EquationData:
    """账户权益数据"""
    equation_usd: float  # 账户总权益（USD）
    timestamp: float


class MedalEquationDataSource(BaseIndicator[EquationData]):
    """
    账户权益数据源（Feature 0008）

    ExchangePath 级别的 Indicator，定期获取账户总权益。

    提供变量：
    - equation_usd: 账户总权益（USD）

    使用场景：
    - Strategy 根据账户权益动态计算目标仓位
    - 如 position_usd = 0.6 * equation_usd
    """

    def __init__(
        self,
        exchange_path: str,
        exchange_class: Optional[str] = None,
        symbol: Optional[str] = None,
        interval: float = 60.0,
        ready_condition: str = "timeout < 120",
        **kwargs,
    ):
        """
        Args:
            exchange_path: 交易所实例路径（如 "okx/main"）
            exchange_class: 交易所类名（可选，由 factory 传入）
            symbol: 交易对（可选，由 factory 传入，此数据源不使用）
            interval: 更新间隔（秒），默认 60 秒
            ready_condition: 就绪条件，默认 2 分钟内有数据
        """
        name = f"Equation:{exchange_path}"
        super().__init__(
            name=name,
            interval=interval,
            ready_condition=ready_condition,
            window=0,  # 不需要历史窗口
            **kwargs,
        )
        self._exchange_path = exchange_path
        # Feature 0012: 注入到 exchange 层级（按交易所实例）
        self.scope_level = "exchange"

    @property
    def exchange_path(self) -> str:
        """交易所实例路径"""
        return self._exchange_path

    def _get_exchange(self):
        """获取交易所实例"""
        if self.root is None:
            return None
        exchange_group = getattr(self.root, 'exchange_group', None)
        if exchange_group is None:
            return None
        return exchange_group.get_exchange(self._exchange_path)

    async def on_tick(self) -> bool:
        """定期获取账户权益"""
        exchange = self._get_exchange()
        if exchange is None:
            self.logger.warning(
                "Exchange not found for path: %s", self._exchange_path
            )
            return False

        try:
            equation_usd = await exchange.medal_fetch_total_balance_usd()
            now = time.time()

            data = EquationData(
                equation_usd=equation_usd,
                timestamp=now,
            )
            self._data.append(now, data)
            self._emit_update(now, data)

            self.logger.debug(
                "Equation updated: %s = %.2f USD",
                self._exchange_path, equation_usd
            )

        except Exception as e:
            self._emit_error(e)
            self.logger.error(
                "Failed to fetch equation for %s: %s",
                self._exchange_path, e
            )

        return False

    def calculate_vars(self, direction: int) -> dict[str, Any]:
        """
        返回账户权益变量

        提供：
        - equation_usd: 账户总权益（USD）
        """
        if not self._data:
            return {"equation_usd": 0.0}

        data = self._data.latest
        return {
            "equation_usd": data.equation_usd,
        }

"""
MedalAmountDataSource - 账户余额数据源

用于 MarketNeutralPositions 策略，获取合约/现货账户的真实存量。
"""
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any, TYPE_CHECKING
from ..indicator.base import BaseIndicator

if TYPE_CHECKING:
    from ..core.app.core import AppCore


@dataclass
class AmountData:
    """账户余额数据"""
    amount: float  # 账户余额（币的数量）
    timestamp: float


class MedalAmountDataSource(BaseIndicator[AmountData]):
    """
    账户余额数据源

    特性：
    - 汇总合约/现货账户的真实存量
    - 形成标准 amount 字段
    - 注入到 exchange scope
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
            exchange_class: 交易所类名（可选）
            symbol: 交易对（可选，此数据源不使用）
            interval: 更新间隔（秒），默认 60 秒
            ready_condition: 就绪条件，默认 2 分钟内有数据
        """
        name = f"MedalAmount:{exchange_path}"
        super().__init__(
            name=name,
            interval=interval,
            ready_condition=ready_condition,
            window=0,  # 不需要历史窗口
            **kwargs,
        )
        self._exchange_path = exchange_path

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
        """定期获取账户余额"""
        exchange = self._get_exchange()
        if exchange is None:
            self.logger.warning(
                "Exchange not found for path: %s", self._exchange_path
            )
            return False

        try:
            # 获取账户余额（USD 价值）
            # 使用 medal_fetch_total_balance_usd 获取所有账户的总余额
            amount_usd = await exchange.medal_fetch_total_balance_usd()
            now = time.time()

            data = AmountData(
                amount=amount_usd,
                timestamp=now,
            )
            self._data.append(now, data)
            self._emit_update(now, data)

            return False  # 返回 False 表示继续运行
        except Exception as e:
            self.logger.error(
                "Failed to fetch amount for %s: %s",
                self._exchange_path,
                e,
                exc_info=True,
            )
            return False

    def calculate_vars(self, direction: Optional[str] = None) -> Dict[str, Any]:
        """
        计算变量（注入到 exchange scope）

        Returns:
            变量字典：{"amount": amount_usd}
        """
        latest = self._data.latest()
        if latest is None:
            return {"amount": 0.0}
        return {"amount": latest.amount}


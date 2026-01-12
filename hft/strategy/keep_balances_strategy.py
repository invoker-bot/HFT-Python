"""
KeepBalancesStrategy - 跨交易所余额平衡策略

定期检查多个交易所的现货余额，当某个交易所余额不足时，
自动从其他有充足余额的交易所转移资金。

使用场景：
- 多交易所套利时保持各交易所有足够资金
- 自动化资金调度，避免手动转账

配置示例 (conf/strategy/keep_balances/usdt.yaml):
    class_name: keep_balances
    name: USDT Balance Keeper
    interval: 60.0  # 每分钟检查一次
    currency: USDT
    deposit_amount: 1000.0  # 每次转移数量
    exchanges:
      - name: okx/main
        min_amount: 500.0  # 低于此值触发转入
      - name: binance/main
        min_amount: 500.0
"""
import asyncio
from typing import ClassVar, Type, Optional, TYPE_CHECKING
from pydantic import BaseModel, Field
from .base import BaseStrategy, TargetPositions
from .config import BaseStrategyConfig

if TYPE_CHECKING:
    from ..exchange.base import BaseExchange


class ExchangeBalanceConfig(BaseModel):
    """单个交易所的余额配置"""
    name: str = Field(..., description="交易所配置路径（如 'okx/main'）")
    min_amount: float = Field(..., description="最低余额阈值，低于此值触发转入")


class KeepBalancesConfig(BaseStrategyConfig):
    """
    余额平衡策略配置

    Attributes:
        currency: 要管理的币种（如 'USDT'）
        deposit_amount: 每次转移的数量
        exchanges: 交易所列表及其最低余额配置
    """
    class_name: ClassVar[str] = "keep_balances"

    currency: str = Field(..., description="要管理的币种（如 'USDT', 'BTC'）")
    deposit_amount: float = Field(..., description="每次转移的数量")
    exchanges: list[ExchangeBalanceConfig] = Field(
        ..., description="交易所配置列表"
    )

    # 覆盖默认值
    interval: float = Field(60.0, description="检查间隔（秒）")
    trading_pairs: list[str] = Field(default_factory=list)  # 不需要交易对
    max_trading_pairs: int = Field(0)

    @classmethod
    def get_class_type(cls) -> Type["KeepBalancesStrategy"]:
        return KeepBalancesStrategy


class KeepBalancesStrategy(BaseStrategy):
    """
    跨交易所余额平衡策略

    工作流程：
    1. 定期检查所有配置交易所的现货余额
    2. 找出余额低于 min_amount 的交易所（需要资金）
    3. 找出余额 > min_amount + deposit_amount 的交易所（可提供资金）
    4. 执行转账（使用 medal_auto_deposit）

    注意：
    - 这是一个管理策略，不参与仓位计算
    - get_target_positions_usd() 返回空字典
    - 主要逻辑在 on_tick() 中
    """

    config: KeepBalancesConfig

    def __init__(self, config: KeepBalancesConfig):
        super().__init__(config)
        self._transfer_in_progress = False
        self._last_transfer_time = 0.0

    @property
    def exchange_group(self):
        """获取交易所组"""
        return self.root.exchange_group

    def get_target_positions_usd(self) -> TargetPositions:
        """
        此策略不管理仓位，返回空字典
        """
        return {}

    def _get_exchange(self, exchange_path: str) -> Optional["BaseExchange"]:
        """根据配置路径获取交易所实例"""
        # exchange_path 格式: "okx/main" -> 需要找到对应的 BaseExchange
        for exchange in self.exchange_group.children.values():
            if exchange.config.path == exchange_path:
                return exchange
        return None

    def _get_spot_balance(self, exchange: "BaseExchange", currency: str) -> float:
        """获取交易所现货余额"""
        spot_balances = exchange._balances.get('spot', {})
        currency_balance = spot_balances.get(currency, {})
        return currency_balance.get('free', 0.0)

    async def on_tick(self) -> bool:
        """
        主循环：检查余额并执行转账

        Returns:
            False（此策略不会自动退出）
        """
        # 如果正在转账，跳过本次检查
        if self._transfer_in_progress:
            self.logger.debug("Transfer in progress, skipping check")
            return False

        currency = self.config.currency
        deposit_amount = self.config.deposit_amount

        # 收集所有交易所的余额信息
        exchange_balances: list[tuple["BaseExchange", ExchangeBalanceConfig, float]] = []

        for ex_config in self.config.exchanges:
            exchange = self._get_exchange(ex_config.name)
            if exchange is None:
                self.logger.warning("Exchange not found: %s", ex_config.name)
                continue

            if not exchange.ready:
                self.logger.debug("Exchange not ready: %s", ex_config.name)
                continue

            balance = self._get_spot_balance(exchange, currency)
            exchange_balances.append((exchange, ex_config, balance))

        if len(exchange_balances) < 2:
            self.logger.debug("Not enough exchanges available for balance check")
            return False

        # 找出需要资金的交易所（余额 < min_amount）
        need_funds: list[tuple["BaseExchange", ExchangeBalanceConfig, float]] = []
        for exchange, ex_config, balance in exchange_balances:
            if balance < ex_config.min_amount:
                need_funds.append((exchange, ex_config, balance))
                self.logger.info(
                    "%s %s balance %.2f < min %.2f, needs funds",
                    exchange.name, currency, balance, ex_config.min_amount
                )

        if not need_funds:
            self.logger.debug("All exchanges have sufficient %s balance", currency)
            return False

        # 找出可以提供资金的交易所（余额 > min_amount + deposit_amount）
        can_provide: list[tuple["BaseExchange", ExchangeBalanceConfig, float]] = []
        for exchange, ex_config, balance in exchange_balances:
            threshold = ex_config.min_amount + deposit_amount
            if balance > threshold:
                can_provide.append((exchange, ex_config, balance))

        if not can_provide:
            self.logger.warning(
                "No exchange has enough %s to transfer (need > min + %.2f)",
                currency, deposit_amount
            )
            # 发送通知
            notify = getattr(self.root, 'notify', None)
            if notify:
                await notify.warning(
                    "余额不足",
                    f"所有交易所 {currency} 余额不足，无法执行转账\n"
                    f"需要转移: {deposit_amount}"
                )
            return False

        # 选择第一个需要资金的交易所作为目标
        to_exchange, to_config, to_balance = need_funds[0]

        # 选择余额最多的交易所作为来源
        can_provide.sort(key=lambda x: x[2], reverse=True)
        from_exchange, from_config, from_balance = can_provide[0]

        # 执行转账
        self.logger.info(
            "Initiating transfer: %.2f %s from %s (%.2f) to %s (%.2f)",
            deposit_amount, currency,
            from_exchange.name, from_balance,
            to_exchange.name, to_balance
        )

        self._transfer_in_progress = True
        try:
            result = await from_exchange.medal_auto_deposit(
                to_exchange=to_exchange,
                currency=currency,
                amount=deposit_amount,
                network="auto",
            )
            self.logger.info(
                "Transfer completed: %.2f %s received",
                result.get('received_amount', deposit_amount), currency
            )
        except TimeoutError as e:
            self.logger.error("Transfer timeout: %s", e)
            # 通知已在 medal_auto_deposit 中发送
        except ValueError as e:
            self.logger.error("Transfer failed: %s", e)
            notify = getattr(self.root, 'notify', None)
            if notify:
                await notify.error(
                    "转账失败",
                    f"从 {from_exchange.name} 到 {to_exchange.name}\n"
                    f"币种: {currency}\n"
                    f"数量: {deposit_amount}\n"
                    f"错误: {e}"
                )
        except Exception as e:
            self.logger.exception("Unexpected error during transfer: %s", e)
            notify = getattr(self.root, 'notify', None)
            if notify:
                await notify.error(
                    "转账异常",
                    f"从 {from_exchange.name} 到 {to_exchange.name}\n"
                    f"错误: {e}"
                )
        finally:
            self._transfer_in_progress = False

        return False

    @property
    def log_state_dict(self) -> dict:
        """返回状态日志"""
        currency = self.config.currency
        balances = {}

        for ex_config in self.config.exchanges:
            exchange = self._get_exchange(ex_config.name)
            if exchange and exchange.ready:
                balance = self._get_spot_balance(exchange, currency)
                balances[ex_config.name] = {
                    "balance": balance,
                    "min": ex_config.min_amount,
                    "status": "ok" if balance >= ex_config.min_amount else "low"
                }

        return {
            **super().log_state_dict,
            "currency": currency,
            "deposit_amount": self.config.deposit_amount,
            "balances": balances,
            "transfer_in_progress": self._transfer_in_progress,
        }

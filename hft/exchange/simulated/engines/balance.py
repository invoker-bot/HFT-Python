"""
BalanceTracker - 余额追踪器

维护各币种余额，支持交易扣减和 funding 结算。
"""


class BalanceTracker:
    """余额追踪器"""

    def __init__(self, initial_usdt: float = 100_000.0):
        self._balances: dict[str, float] = {'USDT': initial_usdt}

    def get_balance(self, currency: str) -> float:
        """获取指定币种余额"""
        return self._balances.get(currency, 0.0)

    def get_usdt_balance(self) -> float:
        """获取 USDT 余额"""
        return self._balances.get('USDT', 0.0)

    def apply_trade(self, side: str, cost_usdt: float, symbol: str, realized_pnl: float = 0.0):
        """交易成交后更新余额

        对于 spot：买入扣减 USDT，卖出增加 USDT
        对于 swap：开仓不动余额，减仓/平仓时按已实现 PnL 调整余额
        """
        is_spot = ':' not in symbol

        if is_spot:
            if side == 'buy':
                self._balances['USDT'] = self._balances.get('USDT', 0.0) - cost_usdt
            else:
                self._balances['USDT'] = self._balances.get('USDT', 0.0) + cost_usdt
        else:
            # swap：按已实现 PnL 调整余额
            if realized_pnl != 0.0:
                self._balances['USDT'] = self._balances.get('USDT', 0.0) + realized_pnl

    def apply_funding(self, amount: float):
        """资金费率结算

        amount > 0: 收到资金费（空头获得正费率时）
        amount < 0: 支付资金费
        """
        self._balances['USDT'] = self._balances.get('USDT', 0.0) + amount

    def apply_fee(self, fee_usdt: float):
        """扣除手续费"""
        self._balances['USDT'] = self._balances.get('USDT', 0.0) - abs(fee_usdt)

    def to_ccxt_format(self, account_type: str = 'swap') -> dict:
        """转换为 ccxt 兼容的余额字典"""
        usdt = self.get_usdt_balance()
        result = {
            'info': {
                'totalWalletBalance': str(usdt),
                'data': [{'totalEq': str(usdt)}],
            },
            'total': {},
            'free': {},
            'used': {},
        }
        for currency, amount in self._balances.items():
            result[currency] = {'free': amount, 'used': 0.0, 'total': amount}
            result['total'][currency] = amount
            result['free'][currency] = amount
            result['used'][currency] = 0.0
        return result

    def reset(self, initial_usdt: float = 100_000.0):
        """重置余额"""
        self._balances.clear()
        self._balances['USDT'] = initial_usdt

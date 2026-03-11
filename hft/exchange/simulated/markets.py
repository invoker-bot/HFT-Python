"""
模拟交易所市场数据定义

定义 30+ 真实交易对（spot + swap），每个包含完整的 MarketInterface 信息。
"""
from typing import Optional


# 交易对配置：base currency → 初始价格、年化波动率、合约大小、精度
SYMBOLS_CONFIG: dict[str, dict] = {
    # 主流 (Tier 1)
    'BTC':   {'price': 80000,  'vol': 0.02,  'contract_size': 0.001,  'amount_prec': 0.00001, 'price_prec': 0.1},
    'ETH':   {'price': 3000,   'vol': 0.025, 'contract_size': 0.01,   'amount_prec': 0.0001,  'price_prec': 0.01},
    'SOL':   {'price': 150,    'vol': 0.04,  'contract_size': 1,      'amount_prec': 0.01,    'price_prec': 0.001},
    'BNB':   {'price': 600,    'vol': 0.03,  'contract_size': 0.1,    'amount_prec': 0.001,   'price_prec': 0.01},
    'XRP':   {'price': 0.55,   'vol': 0.04,  'contract_size': 1,      'amount_prec': 0.1,     'price_prec': 0.0001},

    # 主流 (Tier 2)
    'DOGE':  {'price': 0.15,   'vol': 0.06,  'contract_size': 1,      'amount_prec': 1,       'price_prec': 0.00001},
    'ADA':   {'price': 0.45,   'vol': 0.05,  'contract_size': 1,      'amount_prec': 0.1,     'price_prec': 0.0001},
    'AVAX':  {'price': 35,     'vol': 0.05,  'contract_size': 0.1,    'amount_prec': 0.01,    'price_prec': 0.001},
    'LINK':  {'price': 14,     'vol': 0.05,  'contract_size': 1,      'amount_prec': 0.01,    'price_prec': 0.001},
    'DOT':   {'price': 7,      'vol': 0.05,  'contract_size': 1,      'amount_prec': 0.01,    'price_prec': 0.001},
    'MATIC': {'price': 0.80,   'vol': 0.05,  'contract_size': 1,      'amount_prec': 0.1,     'price_prec': 0.0001},
    'UNI':   {'price': 9,      'vol': 0.05,  'contract_size': 1,      'amount_prec': 0.01,    'price_prec': 0.001},
    'ATOM':  {'price': 8.5,    'vol': 0.05,  'contract_size': 1,      'amount_prec': 0.01,    'price_prec': 0.001},

    # DeFi / L2
    'ARB':   {'price': 1.2,    'vol': 0.06,  'contract_size': 1,      'amount_prec': 0.1,     'price_prec': 0.0001},
    'OP':    {'price': 2.5,    'vol': 0.06,  'contract_size': 1,      'amount_prec': 0.1,     'price_prec': 0.0001},
    'APT':   {'price': 9,      'vol': 0.06,  'contract_size': 1,      'amount_prec': 0.01,    'price_prec': 0.001},
    'SUI':   {'price': 1.5,    'vol': 0.07,  'contract_size': 1,      'amount_prec': 0.1,     'price_prec': 0.0001},
    'SEI':   {'price': 0.50,   'vol': 0.07,  'contract_size': 1,      'amount_prec': 0.1,     'price_prec': 0.0001},
    'INJ':   {'price': 25,     'vol': 0.06,  'contract_size': 0.1,    'amount_prec': 0.01,    'price_prec': 0.01},
    'FET':   {'price': 2.0,    'vol': 0.07,  'contract_size': 1,      'amount_prec': 0.1,     'price_prec': 0.0001},
    'NEAR':  {'price': 5.5,    'vol': 0.06,  'contract_size': 1,      'amount_prec': 0.01,    'price_prec': 0.001},
    'FIL':   {'price': 5,      'vol': 0.06,  'contract_size': 1,      'amount_prec': 0.01,    'price_prec': 0.001},

    # Meme / 高波动
    'PEPE':  {'price': 0.000012, 'vol': 0.10, 'contract_size': 1000, 'amount_prec': 1000,   'price_prec': 0.0000000001},
    'SHIB':  {'price': 0.000025, 'vol': 0.08, 'contract_size': 1000, 'amount_prec': 1000,   'price_prec': 0.0000000001},
    'WIF':   {'price': 2.5,    'vol': 0.10,  'contract_size': 1,      'amount_prec': 0.1,     'price_prec': 0.0001},
    'BONK':  {'price': 0.00003, 'vol': 0.10, 'contract_size': 10000, 'amount_prec': 10000,  'price_prec': 0.00000000001},
    'FLOKI': {'price': 0.0002, 'vol': 0.09,  'contract_size': 1000,   'amount_prec': 100,     'price_prec': 0.00000001},

    # 老牌
    'LTC':   {'price': 85,     'vol': 0.04,  'contract_size': 0.1,    'amount_prec': 0.001,   'price_prec': 0.01},
    'ETC':   {'price': 25,     'vol': 0.05,  'contract_size': 0.1,    'amount_prec': 0.01,    'price_prec': 0.01},
    'BCH':   {'price': 350,    'vol': 0.04,  'contract_size': 0.01,   'amount_prec': 0.001,   'price_prec': 0.01},
    'FTM':   {'price': 0.70,   'vol': 0.07,  'contract_size': 1,      'amount_prec': 0.1,     'price_prec': 0.0001},
    'AAVE':  {'price': 250,    'vol': 0.05,  'contract_size': 0.01,   'amount_prec': 0.001,   'price_prec': 0.01},
    'MKR':   {'price': 2800,   'vol': 0.05,  'contract_size': 0.001,  'amount_prec': 0.0001,  'price_prec': 0.1},
    'CRV':   {'price': 0.60,   'vol': 0.07,  'contract_size': 1,      'amount_prec': 0.1,     'price_prec': 0.0001},
}

# 流动性档次（影响 bid/ask spread）
LIQUIDITY_TIERS = {
    1: {'spread_bps': 1.0,  'bases': ['BTC', 'ETH']},
    2: {'spread_bps': 2.0,  'bases': ['SOL', 'BNB', 'XRP', 'DOGE']},
    3: {'spread_bps': 5.0,  'bases': ['ADA', 'AVAX', 'LINK', 'DOT', 'MATIC', 'UNI', 'ATOM',
                                        'LTC', 'BCH', 'ETC']},
    4: {'spread_bps': 10.0, 'bases': ['ARB', 'OP', 'APT', 'SUI', 'SEI', 'INJ', 'FET', 'NEAR',
                                        'FIL', 'AAVE', 'MKR', 'CRV', 'FTM', 'WIF']},
    5: {'spread_bps': 20.0, 'bases': ['PEPE', 'SHIB', 'BONK', 'FLOKI']},
}

# 反向映射：base → spread_bps
_BASE_TO_SPREAD: dict[str, float] = {}
for _tier_info in LIQUIDITY_TIERS.values():
    for _base in _tier_info['bases']:
        _BASE_TO_SPREAD[_base] = _tier_info['spread_bps']


def get_spread_bps(base: str) -> float:
    """获取交易对的 bid/ask half-spread (basis points)"""
    return _BASE_TO_SPREAD.get(base, 10.0)


def build_spot_market(base: str, config: dict) -> dict:
    """构建现货市场数据 (MarketInterface 兼容)"""
    symbol = f"{base}/USDT"
    return {
        'id': symbol,
        'symbol': symbol,
        'base': base,
        'quote': 'USDT',
        'type': 'spot',
        'spot': True,
        'swap': False,
        'future': False,
        'option': False,
        'contract': False,
        'contractSize': None,
        'subType': None,
        'settle': None,
        'expiry': None,
        'active': True,
        'precision': {
            'amount': config['amount_prec'],
            'price': config['price_prec'],
        },
        'limits': {
            'amount': {'min': config['amount_prec'], 'max': 1_000_000},
            'price': {'min': config['price_prec'], 'max': config['price'] * 100},
            'cost': {'min': 5.0, 'max': None},
            'leverage': {'min': None, 'max': None},
        },
        'info': {},
    }


def build_swap_market(base: str, config: dict) -> dict:
    """构建永续合约市场数据 (MarketInterface 兼容)"""
    symbol = f"{base}/USDT:USDT"
    return {
        'id': symbol,
        'symbol': symbol,
        'base': base,
        'quote': 'USDT',
        'type': 'swap',
        'spot': False,
        'swap': True,
        'future': False,
        'option': False,
        'contract': True,
        'contractSize': config['contract_size'],
        'subType': 'linear',
        'settle': 'USDT',
        'expiry': None,
        'active': True,
        'precision': {
            'amount': config['amount_prec'],
            'price': config['price_prec'],
        },
        'limits': {
            'amount': {'min': config['amount_prec'], 'max': 1_000_000},
            'price': {'min': config['price_prec'], 'max': config['price'] * 100},
            'cost': {'min': 5.0, 'max': None},
            'leverage': {'min': 1, 'max': 125},
        },
        'info': {},
    }


def build_all_markets() -> dict[str, dict]:
    """构建所有市场数据"""
    markets = {}
    for base, config in SYMBOLS_CONFIG.items():
        markets[f"{base}/USDT"] = build_spot_market(base, config)
        markets[f"{base}/USDT:USDT"] = build_swap_market(base, config)
    return markets


def build_currencies() -> dict[str, dict]:
    """构建货币信息"""
    currencies = {
        'USDT': {
            'id': 'USDT', 'code': 'USDT', 'name': 'Tether',
            'active': True, 'deposit': True, 'withdraw': True,
            'precision': 0.01, 'fee': 1.0,
            'limits': {'amount': {'min': 0.01, 'max': None}, 'withdraw': {'min': 10, 'max': None}},
            'networks': {},
            'info': {},
        }
    }
    for base in SYMBOLS_CONFIG:
        currencies[base] = {
            'id': base, 'code': base, 'name': base,
            'active': True, 'deposit': True, 'withdraw': True,
            'precision': SYMBOLS_CONFIG[base]['amount_prec'],
            'fee': 0.0, 'limits': {'amount': {'min': 0, 'max': None}, 'withdraw': {'min': 0, 'max': None}},
            'networks': {},
            'info': {},
        }
    return currencies


def get_spot_symbols() -> list[str]:
    """获取所有现货交易对"""
    return [f"{base}/USDT" for base in SYMBOLS_CONFIG]


def get_swap_symbols() -> list[str]:
    """获取所有永续合约交易对"""
    return [f"{base}/USDT:USDT" for base in SYMBOLS_CONFIG]


def get_all_symbols() -> list[str]:
    """获取所有交易对"""
    return get_spot_symbols() + get_swap_symbols()

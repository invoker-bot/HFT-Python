"""
交易对选品系统（已弃用）

.. deprecated::
    本模块属于旧的 Controller/Command 架构，已被新的 Strategy/Executor 架构取代。
    新架构直接使用 (exchange_class, symbol) 字符串对标识交易对。
    请使用 hft.strategy.base.BaseStrategy 替代。

TradingPairs: 最小交易单元
TradingPairsRow: 一组相关交易对（如 BTC/USDC, BTC/USDT, BTC/USDT:USDT）
TradingPairsTable: 按 score 排序的交易对表
"""
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Callable, Iterator
from functools import cached_property


class QuoteType(Enum):
    """计价货币类型"""
    USDT = "USDT"
    USDC = "USDC"
    BUSD = "BUSD"
    USD = "USD"
    BTC = "BTC"
    ETH = "ETH"


class MarketType(Enum):
    """市场类型"""
    SPOT = "spot"               # 现货
    LINEAR = "linear"           # U本位合约 (如 BTC/USDT:USDT)
    INVERSE = "inverse"         # 币本位合约 (如 BTC/USD:BTC)
    OPTION = "option"           # 期权


@dataclass(frozen=True)
class TradingPairs:
    """
    最小交易单元

    Examples:
        TradingPairs("BTC", "USDT", "binance", MarketType.SPOT)   # BTC/USDT 现货
        TradingPairs("BTC", "USDT", "binance", MarketType.LINEAR) # BTC/USDT:USDT 合约
        TradingPairs("BTC", "USD", "binance", MarketType.INVERSE) # BTC/USD:BTC 币本位
    """
    base: str                                   # 基础货币 (如 BTC)
    quote: str                                  # 计价货币 (如 USDT)
    exchange: str                               # 交易所 (如 binance)
    market_type: MarketType = MarketType.SPOT   # 市场类型

    # 可选属性
    settle: Optional[str] = None                # 结算货币 (合约专用，如 USDT)

    @cached_property
    def symbol(self) -> str:
        """CCXT 标准交易对格式"""
        if self.market_type == MarketType.SPOT:
            return f"{self.base}/{self.quote}"
        elif self.market_type == MarketType.LINEAR:
            settle = self.settle or self.quote
            return f"{self.base}/{self.quote}:{settle}"
        elif self.market_type == MarketType.INVERSE:
            settle = self.settle or self.base
            return f"{self.base}/{self.quote}:{settle}"
        else:
            return f"{self.base}/{self.quote}"

    @cached_property
    def is_spot(self) -> bool:
        return self.market_type == MarketType.SPOT

    @cached_property
    def is_linear(self) -> bool:
        return self.market_type == MarketType.LINEAR

    @cached_property
    def is_inverse(self) -> bool:
        return self.market_type == MarketType.INVERSE

    @cached_property
    def is_futures(self) -> bool:
        return self.market_type in (MarketType.LINEAR, MarketType.INVERSE)

    @cached_property
    def is_usd_quoted(self) -> bool:
        """是否是 USD 系计价"""
        return self.quote in ("USDT", "USDC", "BUSD", "USD")

    @cached_property
    def is_coin_quoted(self) -> bool:
        """是否是币本位计价"""
        return self.quote in ("BTC", "ETH") or self.market_type == MarketType.INVERSE

    def __str__(self) -> str:
        return f"{self.symbol}@{self.exchange}"

    def __hash__(self) -> int:
        return hash((self.base, self.quote, self.exchange, self.market_type, self.settle))


@dataclass
class TradingPairsRow:
    """
    一组相关交易对（同一 base 货币的不同交易方式）

    例如 BTC 行包含: BTC/USDT, BTC/USDC, BTC/USDT:USDT 等
    """
    base: str                                       # 基础货币
    pairs: list[TradingPairs] = field(default_factory=list)
    _score: float = 0.0                             # 计算后的分数

    def add(self, pair: TradingPairs) -> None:
        """添加交易对"""
        if pair.base != self.base:
            raise ValueError(f"交易对 base 不匹配: {pair.base} != {self.base}")
        if pair not in self.pairs:
            self.pairs.append(pair)

    def remove(self, pair: TradingPairs) -> None:
        """移除交易对"""
        if pair in self.pairs:
            self.pairs.remove(pair)

    def get_by_exchange(self, exchange: str) -> list[TradingPairs]:
        """获取指定交易所的交易对"""
        return [p for p in self.pairs if p.exchange == exchange]

    def get_by_market_type(self, market_type: MarketType) -> list[TradingPairs]:
        """获取指定市场类型的交易对"""
        return [p for p in self.pairs if p.market_type == market_type]

    def get_spot(self) -> list[TradingPairs]:
        """获取所有现货交易对"""
        return self.get_by_market_type(MarketType.SPOT)

    def get_linear(self) -> list[TradingPairs]:
        """获取所有 U 本位合约"""
        return self.get_by_market_type(MarketType.LINEAR)

    def get_inverse(self) -> list[TradingPairs]:
        """获取所有币本位合约"""
        return self.get_by_market_type(MarketType.INVERSE)

    @property
    def score(self) -> float:
        return self._score

    @score.setter
    def score(self, value: float) -> None:
        self._score = value

    def __len__(self) -> int:
        return len(self.pairs)

    def __iter__(self) -> Iterator[TradingPairs]:
        return iter(self.pairs)

    def __contains__(self, pair: TradingPairs) -> bool:
        return pair in self.pairs


class TableType(Enum):
    """表类型"""
    USD_QUOTED = "usd_quoted"       # U 本位表 (USDT/USDC/BUSD 计价)
    COIN_QUOTED = "coin_quoted"     # 币本位表


ScoreFunction = Callable[[TradingPairsRow], float]


class TradingPairsTable:
    """
    交易对排序表

    按 score 函数计算的分数排序，分数越高优先级越高
    """

    def __init__(
        self,
        table_type: TableType = TableType.USD_QUOTED,
        score_fn: Optional[ScoreFunction] = None,
    ):
        self.table_type = table_type
        self._rows: dict[str, TradingPairsRow] = {}   # base -> row
        self._score_fn = score_fn or self._default_score
        self._sorted_cache: Optional[list[TradingPairsRow]] = None

    @staticmethod
    def _default_score(row: TradingPairsRow) -> float:
        """默认评分：按交易对数量"""
        return float(len(row))

    def set_score_function(self, score_fn: ScoreFunction) -> None:
        """设置评分函数"""
        self._score_fn = score_fn
        self._invalidate_cache()

    def add_pair(self, pair: TradingPairs) -> None:
        """添加交易对"""
        if pair.base not in self._rows:
            self._rows[pair.base] = TradingPairsRow(base=pair.base)
        self._rows[pair.base].add(pair)
        self._invalidate_cache()

    def remove_pair(self, pair: TradingPairs) -> None:
        """移除交易对"""
        if pair.base in self._rows:
            self._rows[pair.base].remove(pair)
            if len(self._rows[pair.base]) == 0:
                del self._rows[pair.base]
            self._invalidate_cache()

    def get_row(self, base: str) -> Optional[TradingPairsRow]:
        """获取指定 base 的行"""
        return self._rows.get(base)

    def update_scores(self) -> None:
        """更新所有行的分数"""
        for row in self._rows.values():
            row.score = self._score_fn(row)
        self._invalidate_cache()

    def _invalidate_cache(self) -> None:
        """使排序缓存失效"""
        self._sorted_cache = None

    @property
    def sorted_rows(self) -> list[TradingPairsRow]:
        """获取按分数排序的行（降序）"""
        if self._sorted_cache is None:
            self.update_scores()
            self._sorted_cache = sorted(
                self._rows.values(),
                key=lambda r: r.score,
                reverse=True
            )
        return self._sorted_cache

    def top(self, n: int = 10) -> list[TradingPairsRow]:
        """获取前 N 个高分行"""
        return self.sorted_rows[:n]

    def filter_by_score(self, min_score: float) -> list[TradingPairsRow]:
        """获取分数大于等于 min_score 的行"""
        return [r for r in self.sorted_rows if r.score >= min_score]

    def __len__(self) -> int:
        return len(self._rows)

    def __iter__(self) -> Iterator[TradingPairsRow]:
        return iter(self.sorted_rows)

    def __contains__(self, base: str) -> bool:
        return base in self._rows

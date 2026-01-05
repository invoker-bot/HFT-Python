from collections import defaultdict
from .base import BaseExchange


class ExchangeGroups:

    def __init__(self, exchanges: list[BaseExchange]):
        self.exchanges = defaultdict(list)
        for ex in exchanges:
            self.exchanges[ex.class_name].append(ex)

    def get_exchange(self, class_name: str) -> BaseExchange:
        return self.exchanges[class_name][0]
    
    def get_exchanges(self, class_name: str) -> list[BaseExchange]:
        return self.exchanges[class_name]

    def exchange_names(self) -> list[str]:
        return list(self.exchanges.keys())
from __future__ import annotations

from fundbacktesting.contracts import BacktestContext

STRATEGY_NAME = "portfolio2_four_long_24_short_3_cash_1"

TARGET_WEIGHTS = {
    "BZ22167.OF": 0.24,
    "BZ22106.OF": 0.24,
    "BZ23085.OF": 0.24,
    "BZ20342.OF": 0.24,
    "BZ22121.OF": 0.03,
}


class FourLongBondsShortBondCashStrategy:
    def decide(self, context: BacktestContext) -> dict[str, float]:
        return dict(TARGET_WEIGHTS)


def create_strategy() -> FourLongBondsShortBondCashStrategy:
    return FourLongBondsShortBondCashStrategy()

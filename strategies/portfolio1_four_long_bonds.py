from __future__ import annotations

from fundbacktesting.contracts import BacktestContext

STRATEGY_NAME = "portfolio1_four_long_bonds_equal_25pct"

TARGET_WEIGHTS = {
    "BZ22167.OF": 0.25,
    "BZ22106.OF": 0.25,
    "BZ23085.OF": 0.25,
    "BZ20342.OF": 0.25,
}


class FourLongBondsEqualWeightStrategy:
    def decide(self, context: BacktestContext) -> dict[str, float]:
        return dict(TARGET_WEIGHTS)


def create_strategy() -> FourLongBondsEqualWeightStrategy:
    return FourLongBondsEqualWeightStrategy()

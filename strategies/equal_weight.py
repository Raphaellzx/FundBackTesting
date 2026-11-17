from __future__ import annotations

from fundbacktesting.contracts import BacktestContext

STRATEGY_NAME = "equal_weight"


class EqualWeightStrategy:
    """Allocate equal weight to all funds available on each rebalance date."""

    def decide(self, context: BacktestContext) -> dict[str, float]:
        if not context.active_funds:
            return {}
        weight = 1.0 / len(context.active_funds)
        return {fund_code: weight for fund_code in context.active_funds}


def create_strategy() -> EqualWeightStrategy:
    return EqualWeightStrategy()

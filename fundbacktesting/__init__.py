"""A small, contract-driven fund backtesting framework."""

from .contracts import (
    BacktestConfig,
    BacktestContext,
    BacktestResult,
    EvaluationResult,
    FundMeta,
    MarketData,
    Strategy,
)
from .engine import run_backtest
from .evaluation import available_conventions, evaluate

__all__ = [
    "BacktestConfig",
    "BacktestContext",
    "BacktestResult",
    "EvaluationResult",
    "FundMeta",
    "MarketData",
    "Strategy",
    "available_conventions",
    "evaluate",
    "run_backtest",
]

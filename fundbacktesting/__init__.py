"""A small, contract-driven fund backtesting framework."""

from .contracts import BacktestConfig, BacktestResult, MarketData
from .engine import run_backtest
from .evaluation import evaluate

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "MarketData",
    "evaluate",
    "run_backtest",
]

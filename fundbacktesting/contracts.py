from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping, Protocol


@dataclass(frozen=True)
class MarketData:
    dates: tuple[date, ...]
    prices: Mapping[date, Mapping[str, float]]
    fund_names: Mapping[str, str]
    source_sha256: str


@dataclass(frozen=True)
class BacktestConfig:
    initial_capital: float = 1_000_000.0
    rebalance_frequency: str = "monthly"
    fee_rate: float = 0.0005
    start_date: date | None = None
    end_date: date | None = None
    annual_risk_free_rate: float = 0.02
    annual_mar: float = 0.0
    var_confidence_level: float = 0.95


@dataclass(frozen=True)
class BacktestContext:
    current_date: date
    history: Mapping[date, Mapping[str, float]]
    holdings: Mapping[str, float]
    active_funds: tuple[str, ...]


class Strategy(Protocol):
    def decide(self, context: BacktestContext) -> Mapping[str, float]:
        """Return asset target weights; any unallocated remainder stays in cash."""


@dataclass(frozen=True)
class NavPoint:
    date: date
    value: float


@dataclass(frozen=True)
class HoldingsSnapshot:
    date: date
    units: Mapping[str, float]
    market_values: Mapping[str, float]
    weights: Mapping[str, float]
    cash_weight: float


@dataclass(frozen=True)
class TradeRecord:
    date: date
    fund_code: str
    direction: str
    units: float
    notional: float
    cost: float


@dataclass(frozen=True)
class CostRecord:
    date: date
    fund_code: str
    notional: float
    fee_rate: float
    cost: float


@dataclass(frozen=True)
class BacktestResult:
    nav_series: tuple[NavPoint, ...]
    holdings_series: tuple[HoldingsSnapshot, ...]
    trade_log: tuple[TradeRecord, ...]
    cost_log: tuple[CostRecord, ...]
    config_snapshot: Mapping[str, object]

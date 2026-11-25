from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Mapping, Protocol


@dataclass(frozen=True)
class FundMeta:
    """可选的产品属性。框架不解释这些字段，只负责把数据传递到策略。"""

    product_type: str | None = None
    inception_date: date | None = None
    eligibility_start: date | None = None
    name: str | None = None


@dataclass(frozen=True)
class MarketData:
    dates: tuple[date, ...]
    prices: Mapping[date, Mapping[str, float]]
    fund_names: Mapping[str, str]
    source_sha256: str
    risk_free_rates: Mapping[date, float] | None = None
    fund_meta: Mapping[str, FundMeta] | None = None
    source_fingerprints: Mapping[str, str] | None = None


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
    rebalance_dates: tuple[date, ...] | None = None
    charge_initial_cost: bool = True
    metric_convention: str = "framework"
    evaluation_start_date: date | None = None
    decision_lag: int = 0


@dataclass(frozen=True)
class BacktestContext:
    current_date: date
    history: Mapping[date, Mapping[str, float]]
    holdings: Mapping[str, float]
    active_funds: tuple[str, ...]
    history_before_current: Mapping[date, Mapping[str, float]] = field(default_factory=dict)
    risk_free_rates: Mapping[date, float] | None = None
    fund_meta: Mapping[str, FundMeta] | None = None
    decision_date: date | None = None


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
class EvaluationResult:
    """结构化绩效结果。字段、单位与计算约定可追溯。"""

    metrics: Mapping[str, float | int | None]
    convention: str
    units: Mapping[str, str] = field(default_factory=dict)
    convention_notes: Mapping[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, float | int | None]:
        """兼容旧调用方：返回指标字典，不混入口径元数据。"""
        return dict(self.metrics)

    def __getitem__(self, key: str) -> float | int | None:
        return self.metrics[key]

    def keys(self):
        return self.metrics.keys()

    def items(self):
        return self.metrics.items()


@dataclass(frozen=True)
class BacktestResult:
    nav_series: tuple[NavPoint, ...]
    holdings_series: tuple[HoldingsSnapshot, ...]
    trade_log: tuple[TradeRecord, ...]
    cost_log: tuple[CostRecord, ...]
    config_snapshot: Mapping[str, object]
    rebalance_changes: tuple[int, ...] = ()
    rebalance_turnover: tuple[tuple[date, float], ...] = ()
    risk_free_rates: Mapping[date, float] | None = None


__all__ = [
    "FundMeta",
    "MarketData",
    "BacktestConfig",
    "BacktestContext",
    "Strategy",
    "NavPoint",
    "HoldingsSnapshot",
    "TradeRecord",
    "CostRecord",
    "EvaluationResult",
    "BacktestResult",
]

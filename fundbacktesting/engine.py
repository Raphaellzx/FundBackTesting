from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date

from .contracts import (
    BacktestConfig,
    BacktestContext,
    BacktestResult,
    CostRecord,
    HoldingsSnapshot,
    MarketData,
    NavPoint,
    Strategy,
    TradeRecord,
)

_WEIGHT_TOLERANCE = 1e-8
_TRADE_TOLERANCE = 1e-10


def _validate_config(config: BacktestConfig) -> None:
    if not math.isfinite(config.initial_capital) or config.initial_capital <= 0:
        raise ValueError("initial_capital must be finite and greater than zero.")
    if not math.isfinite(config.fee_rate) or not 0 <= config.fee_rate < 1:
        raise ValueError("fee_rate must be finite and in the interval [0, 1).")
    if (
        not math.isfinite(config.annual_risk_free_rate)
        or config.annual_risk_free_rate <= -1
    ):
        raise ValueError("annual_risk_free_rate must be finite and greater than -1.")
    if not math.isfinite(config.annual_mar) or config.annual_mar <= -1:
        raise ValueError("annual_mar must be finite and greater than -1.")
    if (
        not math.isfinite(config.var_confidence_level)
        or not 0 < config.var_confidence_level < 1
    ):
        raise ValueError("var_confidence_level must be strictly between 0 and 1.")
    if config.rebalance_frequency not in {"none", "daily", "weekly", "monthly"}:
        raise ValueError(
            "rebalance_frequency must be one of: none, daily, weekly, monthly."
        )
    if (
        config.start_date is not None
        and config.end_date is not None
        and config.start_date > config.end_date
    ):
        raise ValueError("start_date must be on or before end_date.")


def _rebalance_dates(
    dates: tuple[date, ...], frequency: str
) -> set[date]:
    if frequency == "none":
        return {dates[0]} if dates else set()
    if frequency == "daily":
        return set(dates)

    selected: set[date] = set()
    previous_period: tuple[int, ...] | None = None
    for trading_date in dates:
        if frequency == "monthly":
            period = (trading_date.year, trading_date.month)
        else:
            iso = trading_date.isocalendar()
            period = (iso.year, iso.week)
        if period != previous_period:
            selected.add(trading_date)
            previous_period = period
    return selected


def _validated_target(
    target: Mapping[str, float], active_funds: tuple[str, ...], trading_date: date
) -> dict[str, float]:
    if not isinstance(target, Mapping):
        raise ValueError(f"Strategy must return a weight mapping on {trading_date}.")

    active = set(active_funds)
    normalized: dict[str, float] = {}
    for fund_code, raw_weight in target.items():
        if fund_code not in active:
            raise ValueError(
                f"Strategy selected unavailable fund {fund_code!r} on {trading_date}."
            )
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"Invalid target weight for {fund_code!r} on {trading_date}."
            ) from error
        if not math.isfinite(weight) or weight < 0:
            raise ValueError(
                f"Target weight for {fund_code!r} must be finite and nonnegative."
            )
        normalized[fund_code] = weight

    total_weight = sum(normalized.values())
    if not normalized or total_weight > 1.0 + _WEIGHT_TOLERANCE:
        raise ValueError(
            "Target fund weights must sum to at most 1 (the remainder is cash) "
            f"on {trading_date}; got {total_weight:.12g}."
        )
    return normalized


def run_backtest(
    market_data: MarketData, strategy: Strategy, config: BacktestConfig
) -> BacktestResult:
    _validate_config(config)
    dates = tuple(
        trading_date
        for trading_date in market_data.dates
        if (config.start_date is None or trading_date >= config.start_date)
        and (config.end_date is None or trading_date <= config.end_date)
    )
    if not dates:
        raise ValueError("No market data falls within the configured date range.")

    scheduled_dates = _rebalance_dates(dates, config.rebalance_frequency)
    units: dict[str, float] = {}
    last_prices: dict[str, float] = {}
    cash = config.initial_capital
    nav_series: list[NavPoint] = []
    holdings_series: list[HoldingsSnapshot] = []
    trade_log: list[TradeRecord] = []
    cost_log: list[CostRecord] = []
    rebalance_count = 0

    for index, trading_date in enumerate(dates):
        daily_prices = market_data.prices[trading_date]
        last_prices.update(daily_prices)
        active_funds = tuple(sorted(daily_prices))

        current_values = {
            fund_code: holding_units * last_prices[fund_code]
            for fund_code, holding_units in units.items()
        }
        portfolio_value = cash + sum(current_values.values())
        if not math.isfinite(portfolio_value) or portfolio_value <= 0:
            raise ValueError(f"Portfolio value became nonpositive on {trading_date}.")

        if trading_date in scheduled_dates:
            context = BacktestContext(
                current_date=trading_date,
                history={
                    historical_date: market_data.prices[historical_date]
                    for historical_date in dates[: index + 1]
                },
                holdings={
                    fund_code: value / portfolio_value
                    for fund_code, value in current_values.items()
                    if value > 0
                },
                active_funds=active_funds,
            )
            target = _validated_target(
                strategy.decide(context), active_funds, trading_date
            )

            estimated_cost = 0.0
            for _ in range(100):
                target_budget = max(0.0, portfolio_value - estimated_cost)
                target_values = {
                    fund_code: weight * target_budget
                    for fund_code, weight in target.items()
                }
                all_funds = set(current_values) | set(target_values)
                next_cost = config.fee_rate * sum(
                    abs(target_values.get(fund_code, 0.0) - current_values.get(fund_code, 0.0))
                    for fund_code in all_funds
                )
                if math.isclose(
                    next_cost,
                    estimated_cost,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                ):
                    estimated_cost = next_cost
                    break
                estimated_cost = next_cost

            target_budget = max(0.0, portfolio_value - estimated_cost)
            target_values = {
                fund_code: weight * target_budget
                for fund_code, weight in target.items()
            }
            total_cost = 0.0
            for fund_code in sorted(set(current_values) | set(target_values)):
                old_value = current_values.get(fund_code, 0.0)
                new_value = target_values.get(fund_code, 0.0)
                delta_value = new_value - old_value
                if abs(delta_value) <= _TRADE_TOLERANCE:
                    continue
                price = last_prices[fund_code]
                trade_units = delta_value / price
                notional = abs(delta_value)
                cost = notional * config.fee_rate
                total_cost += cost
                direction = "BUY" if delta_value > 0 else "SELL"
                trade_log.append(
                    TradeRecord(
                        date=trading_date,
                        fund_code=fund_code,
                        direction=direction,
                        units=trade_units,
                        notional=notional,
                        cost=cost,
                    )
                )
                cost_log.append(
                    CostRecord(
                        date=trading_date,
                        fund_code=fund_code,
                        notional=notional,
                        fee_rate=config.fee_rate,
                        cost=cost,
                    )
                )

            units = {
                fund_code: value / last_prices[fund_code]
                for fund_code, value in target_values.items()
                if value > _TRADE_TOLERANCE
            }
            cash -= sum(
                target_values.get(fund_code, 0.0) - current_values.get(fund_code, 0.0)
                for fund_code in set(current_values) | set(target_values)
            )
            cash -= total_cost
            if abs(cash) <= config.initial_capital * 1e-12:
                cash = 0.0
            rebalance_count += 1

        marked_values = {
            fund_code: holding_units * last_prices[fund_code]
            for fund_code, holding_units in units.items()
        }
        portfolio_value = cash + sum(marked_values.values())
        if not math.isfinite(portfolio_value) or portfolio_value <= 0:
            raise ValueError(f"Portfolio value became nonpositive on {trading_date}.")
        nav_series.append(
            NavPoint(date=trading_date, value=portfolio_value / config.initial_capital)
        )
        holdings_series.append(
            HoldingsSnapshot(
                date=trading_date,
                units=dict(units),
                market_values=marked_values,
                weights={
                    fund_code: value / portfolio_value
                    for fund_code, value in marked_values.items()
                },
                cash_weight=cash / portfolio_value,
            )
        )

    config_snapshot: dict[str, object] = {
        "initial_capital": config.initial_capital,
        "rebalance_frequency": config.rebalance_frequency,
        "fee_rate": config.fee_rate,
        "annual_risk_free_rate": config.annual_risk_free_rate,
        "annual_mar": config.annual_mar,
        "var_confidence_level": config.var_confidence_level,
        "start_date": dates[0].isoformat(),
        "end_date": dates[-1].isoformat(),
        "source_sha256": market_data.source_sha256,
        "rebalance_count": rebalance_count,
    }
    return BacktestResult(
        nav_series=tuple(nav_series),
        holdings_series=tuple(holdings_series),
        trade_log=tuple(trade_log),
        cost_log=tuple(cost_log),
        config_snapshot=config_snapshot,
    )

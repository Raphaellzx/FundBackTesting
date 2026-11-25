from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from datetime import date

from .contracts import BacktestResult, EvaluationResult

_MONTHS_PER_YEAR = 12
_DAYS_PER_YEAR = 365
_TRADING_DAYS_PER_YEAR = 252
_DEFAULT_DAILY_RISK_FREE_RATE = 0.016


def _framework_metrics(result: BacktestResult) -> dict[str, float | int | None]:
    """月度再平衡约定：月频年化、固定年化无风险利率、历史最高点起算回撤、双边换手。"""
    nav_points = result.nav_series
    if not nav_points:
        raise ValueError("Cannot evaluate an empty NAV series.")

    nav_values = [point.value for point in nav_points]
    if any(not math.isfinite(value) or value <= 0 for value in nav_values):
        raise ValueError("NAV values must be finite and greater than zero.")

    daily_returns = [nav_values[0] - 1.0]
    daily_returns.extend(
        current / previous - 1.0
        for previous, current in zip(nav_values, nav_values[1:])
    )
    monthly_ends: dict[tuple[int, int], float] = {}
    for point in nav_points:
        monthly_ends[(point.date.year, point.date.month)] = point.value
    monthly_returns: list[float] = []
    previous_month_end = 1.0
    for month_end in monthly_ends.values():
        monthly_returns.append(month_end / previous_month_end - 1.0)
        previous_month_end = month_end

    periods_per_year = _MONTHS_PER_YEAR
    elapsed_years = len(monthly_returns) / periods_per_year
    final_nav = nav_values[-1]
    annualized_return = (
        final_nav ** (1.0 / elapsed_years) - 1.0 if final_nav > 0 else None
    )
    annual_risk_free_rate = float(
        result.config_snapshot.get("annual_risk_free_rate", 0.02)
    )
    annual_mar = float(result.config_snapshot.get("annual_mar", 0.0))
    confidence = float(result.config_snapshot.get("var_confidence_level", 0.95))
    if not math.isfinite(annual_risk_free_rate) or annual_risk_free_rate <= -1:
        raise ValueError("annual_risk_free_rate must be finite and greater than -1.")
    if not math.isfinite(annual_mar) or annual_mar <= -1:
        raise ValueError("annual_mar must be finite and greater than -1.")
    if not math.isfinite(confidence) or not 0 < confidence < 1:
        raise ValueError("var_confidence_level must be strictly between 0 and 1.")
    monthly_risk_free_rate = (1.0 + annual_risk_free_rate) ** (
        1.0 / periods_per_year
    ) - 1.0
    monthly_mar = (1.0 + annual_mar) ** (1.0 / periods_per_year) - 1.0

    excess_returns = [value - monthly_risk_free_rate for value in monthly_returns]
    mean_excess_return = sum(excess_returns) / len(excess_returns)
    mean_monthly_return = sum(monthly_returns) / len(monthly_returns)
    sample_variance = (
        sum((value - mean_monthly_return) ** 2 for value in monthly_returns)
        / (len(monthly_returns) - 1)
        if len(monthly_returns) > 1
        else None
    )
    monthly_volatility = (
        math.sqrt(sample_variance) if sample_variance is not None else None
    )
    annualized_volatility = (
        monthly_volatility * math.sqrt(periods_per_year)
        if monthly_volatility is not None
        else None
    )
    sharpe = (
        mean_excess_return / monthly_volatility * math.sqrt(periods_per_year)
        if monthly_volatility is not None and monthly_volatility > 0
        else None
    )

    monthly_shortfalls = [min(value - monthly_mar, 0.0) for value in monthly_returns]
    monthly_downside_deviation = math.sqrt(
        sum(value**2 for value in monthly_shortfalls) / len(monthly_shortfalls)
    )
    downside_deviation = monthly_downside_deviation * math.sqrt(periods_per_year)
    mean_mar_excess_return = sum(
        value - monthly_mar for value in monthly_returns
    ) / len(monthly_returns)
    sortino = (
        mean_mar_excess_return
        / monthly_downside_deviation
        * math.sqrt(periods_per_year)
        if monthly_downside_deviation > 0
        else None
    )
    omega_losses = sum(abs(value) for value in monthly_shortfalls)
    omega_gains = sum(max(value - monthly_mar, 0.0) for value in monthly_returns)
    omega = omega_gains / omega_losses if omega_losses > 0 else None

    high_water_mark = 1.0
    max_drawdown = 0.0
    underwater_days = 0
    max_drawdown_duration = 0
    for value in nav_values:
        if value >= high_water_mark:
            high_water_mark = value
            underwater_days = 0
        else:
            underwater_days += 1
            max_drawdown_duration = max(max_drawdown_duration, underwater_days)
        max_drawdown = min(max_drawdown, value / high_water_mark - 1.0)

    ordered_daily_returns = sorted(daily_returns)
    tail_count = max(
        1, math.ceil((1.0 - confidence) * len(ordered_daily_returns))
    )
    tail_returns = ordered_daily_returns[:tail_count]
    historical_var = max(0.0, -tail_returns[-1])
    historical_cvar = max(0.0, -sum(tail_returns) / len(tail_returns))

    initial_capital = float(result.config_snapshot["initial_capital"])
    total_cost = sum(record.cost for record in result.cost_log)
    total_traded = sum(record.notional for record in result.trade_log)
    annual_turnover = total_traded / initial_capital / elapsed_years
    return {
        "total_return": final_nav - 1.0,
        "annualized_return": annualized_return,
        "annualized_volatility": annualized_volatility,
        "max_drawdown": max_drawdown,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "calmar_ratio": (
            annualized_return / abs(max_drawdown)
            if annualized_return is not None and max_drawdown < 0
            else None
        ),
        "annual_turnover": annual_turnover,
        "total_cost": total_cost,
        "cost_ratio": total_cost / initial_capital,
        "rebalance_count": int(result.config_snapshot["rebalance_count"]),
        "downside_deviation": downside_deviation,
        "omega_ratio": omega,
        "max_drawdown_duration_trading_days": max_drawdown_duration,
        "historical_var_1d": historical_var,
        "historical_cvar_1d": historical_cvar,
        "var_confidence_level": confidence,
        "annual_risk_free_rate": annual_risk_free_rate,
        "annual_mar": annual_mar,
    }


def _daily_365_metrics(result: BacktestResult) -> dict[str, float | int | None]:
    """日频约定：自然日年化、日收益按 √252 缩放、逐日无风险利率、区间首日起算回撤。"""
    nav_points = result.nav_series
    if not nav_points:
        raise ValueError("Cannot evaluate an empty NAV series.")
    if len(nav_points) < 2:
        raise ValueError("Daily-365 convention requires at least two NAV points.")

    nav_values = [point.value for point in nav_points]
    if any(not math.isfinite(value) or value <= 0 for value in nav_values):
        raise ValueError("NAV values must be finite and greater than zero.")

    dates = [point.date for point in nav_points]
    days = (dates[-1] - dates[0]).days
    if days <= 0:
        raise ValueError("NAV series must span a positive number of calendar days.")
    years = days / _DAYS_PER_YEAR

    annualized_return = nav_values[-1] ** (_DAYS_PER_YEAR / days) - 1.0

    daily_returns = [
        current / previous - 1.0
        for previous, current in zip(nav_values, nav_values[1:])
    ]
    mean_return = sum(daily_returns) / len(daily_returns)
    variance = (
        sum((value - mean_return) ** 2 for value in daily_returns)
        / (len(daily_returns) - 1)
        if len(daily_returns) > 1
        else 0.0
    )
    annualized_volatility = math.sqrt(variance) * math.sqrt(_TRADING_DAYS_PER_YEAR)

    peak = nav_values[0]
    max_drawdown = 0.0
    for value in nav_values:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value / peak - 1.0)

    rates = result.risk_free_rates
    if rates is None:
        raise ValueError(
            "Daily-365 convention requires a risk-free rate series on the result."
        )
    used_rates = [float(rates.get(day, _DEFAULT_DAILY_RISK_FREE_RATE)) for day in dates]
    if any(not math.isfinite(rate) for rate in used_rates):
        raise ValueError("Risk-free rate series contains non-finite values.")
    average_risk_free_rate = sum(used_rates) / len(used_rates)

    sharpe = (
        (annualized_return - average_risk_free_rate) / annualized_volatility
        if annualized_volatility > 0
        else None
    )
    calmar = (
        annualized_return / abs(max_drawdown) if max_drawdown < 0 else None
    )

    turnover_two_way = sum(turn for _, turn in result.rebalance_turnover) / years
    turnover_one_way = turnover_two_way / 2.0

    return {
        "total_return": nav_values[-1] - 1.0,
        "annualized_return": annualized_return,
        "annualized_volatility": annualized_volatility,
        "max_drawdown": max_drawdown,
        "sharpe_ratio": sharpe,
        "calmar_ratio": calmar,
        "turnover_one_way_annualized": turnover_one_way,
        "turnover_two_way_annualized": turnover_two_way,
        "rebalance_count_sum": sum(result.rebalance_changes),
        "elapsed_calendar_days": days,
        "elapsed_years": years,
        "average_daily_risk_free_rate": average_risk_free_rate,
        "total_cost": sum(record.cost for record in result.cost_log),
        "rebalance_count": int(result.config_snapshot["rebalance_count"]),
        "evaluation_start_date": dates[0].isoformat(),
        "evaluation_end_date": dates[-1].isoformat(),
    }


_UNITS: Mapping[str, Mapping[str, str]] = {
    "framework": {
        "annualized_return": "decimal per year",
        "annualized_volatility": "decimal per year",
        "max_drawdown": "decimal",
        "annual_turnover": "two-way notional / capital / year",
    },
    "daily_365": {
        "annualized_return": "decimal per year, 365 calendar days",
        "annualized_volatility": "decimal per year, sqrt(252) scaling",
        "max_drawdown": "decimal from period start",
        "turnover_one_way_annualized": "one-way notional / capital / year",
        "turnover_two_way_annualized": "two-way notional / capital / year",
    },
}

_NOTES: Mapping[str, Mapping[str, str]] = {
    "framework": {
        "periods_per_year": "12 months",
        "risk_free_rate": "fixed annual rate from config",
        "drawdown_origin": "running high-water mark from 1.0",
    },
    "daily_365": {
        "periods_per_year": "365 calendar days for return, 252 trading days for volatility",
        "risk_free_rate": "daily series, missing days use 0.016",
        "drawdown_origin": "first NAV value of the evaluation window",
    },
}

_CONVENTIONS: Mapping[str, Callable[[BacktestResult], dict[str, float | int | None]]] = {
    "framework": _framework_metrics,
    "daily_365": _daily_365_metrics,
}

__all__ = ["evaluate", "available_conventions"]


def available_conventions() -> tuple[str, ...]:
    return tuple(sorted(_CONVENTIONS))


def evaluate(
    result: BacktestResult, convention: str | None = None
) -> EvaluationResult:
    """计算绩效指标。默认使用框架约定；计算约定由配置或显式参数决定。"""
    selected = convention or str(result.config_snapshot.get("metric_convention", "framework"))
    if selected not in _CONVENTIONS:
        raise ValueError(
            f"Unknown metric convention {selected!r}; available: "
            f"{', '.join(available_conventions())}"
        )
    metrics = _CONVENTIONS[selected](result)
    return EvaluationResult(
        metrics=metrics,
        convention=selected,
        units=dict(_UNITS.get(selected, {})),
        convention_notes=dict(_NOTES.get(selected, {})),
    )

from __future__ import annotations

import math

from .contracts import BacktestResult

_MONTHS_PER_YEAR = 12


def evaluate(result: BacktestResult) -> dict[str, float | int | None]:
    """Calculate monthly return metrics and daily drawdown and tail risk."""
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

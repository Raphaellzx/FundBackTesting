# Copilot instructions for this repository

## Build and test

Use Python 3.10+. The runtime dependency is `openpyxl`.

```powershell
py -3.13 -m pip install -r requirements.txt
py -3.13 -m fundbacktesting
py -3.13 -m unittest discover -s tests -v
```

Run one test module with `py -3.13 -m unittest tests.test_generic_backtest -v`. There is no separate linter configured.

The reference test `tests/test_study_v17_reference.py` skips automatically when the three input CSVs in `data/` or the baseline file in `tests/reference/study-baseline.json` are absent. It compares framework results against the external baseline (17 rebalance holdings and seven primary metrics); never make framework code depend on any removed study tree.

## Current architecture

This is a contract-driven general-purpose fund backtesting framework. It provides mechanisms, not investment rules.

- `fundbacktesting/contracts.py`: frozen data contracts and the `Strategy` protocol.
- `fundbacktesting/adapter.py`: Excel adapter and CSV readers producing `MarketData`.
- `fundbacktesting/engine.py`: trading-day loop, rebalance scheduling, target validation, trade execution, costs, NAV and rebalance diagnostics.
- `fundbacktesting/evaluation.py`: metric conventions computed from `BacktestResult`.
- `fundbacktesting/reference.py`: external baseline comparison; report only, never a calculation input.
- `fundbacktesting/runner.py`: composition root and artifact writer, including the run manifest.
- `fundbacktesting/strategy_loader.py`: loads `create_strategy()` and `STRATEGY_NAME` from a strategy file.
- `strategies/`: repository-root strategy modules. `strategies/study_v17.py` is an ordinary case-level strategy, not a framework component.
- `cases/study_v17/data.py`: case-level data assembly for the external study inputs.

Dependencies point inward toward contracts. The engine must not import a concrete data source, strategy, evaluation convention, or case module. The runner is the only module that composes all layers.

## Strategy contract

A strategy file exposes `create_strategy()` returning an object with `decide(context) -> Mapping[str, float]`. Target weights may sum to less than 1; the remainder stays in cash. Strategies may keep internal state across rebalances, but portfolio state, trades and costs belong to the engine.

The engine controls when `decide()` is called. It runs on scheduled rebalance dates only.

## Key mechanisms

- `rebalance_frequency`: `none`, `daily`, `weekly`, `monthly`.
- `rebalance_dates`: explicit tuple of dates; takes precedence over frequency and intersects with the configured range.
- `decision_lag`: separates visible data date from execution date. `context.current_date` is the visible date; `context.decision_date` is the execution date.
- `context.history`: legacy view including data through `current_date`. Use `context.history_before_current` for strict point-in-time data.
- `MarketData.risk_free_rates`, `MarketData.fund_meta`, `MarketData.source_fingerprints`: optional context passed through by the framework and interpreted only by strategies or evaluation conventions.
- `charge_initial_cost=False`: first build records trades but no cost and no turnover diagnostic.
- `metric_convention`: `framework` or `daily_365`. Do not mix metrics across conventions.
- `EvaluationResult`: carries `metrics`, `convention`, `units` and `convention_notes`; `as_dict()` is the compatibility view.
- `run_from_files()` writes the run manifest with code version, environment, input fingerprints, source hashes and config snapshot.

## Metric conventions

- `framework`: month-end returns, 12-period annualization, fixed annual risk-free rate, high-water-mark drawdown from 1.0, two-way notional turnover, historical VaR/CVaR.
- `daily_365`: 365 calendar-day annualization, sample daily volatility scaled by √252, daily risk-free series with 0.016 default, drawdown from the first NAV, weight-difference one-way/two-way turnover, `rebalance_count_sum`.

## Boundaries

Do not add a plugin registry, a built-in study workflow, benchmark attribution, live trading, or fund subscription/redemption confirmation lag. Do not move specific research rules or terminology into `fundbacktesting/`. External baselines are acceptance artifacts only.

When adding behavior, add tests under `tests/`. Preserve backward compatibility for `load_market_data()`, existing output filenames, and `evaluate()` compatibility views unless explicitly asked to change them.

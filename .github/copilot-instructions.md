# Copilot instructions for this repository

## Build, test, and lint commands

Use Python 3.10+ and install the sole runtime dependency:

```powershell
py -3.13 -m pip install -r requirements.txt
py -3.13 -m fundbacktesting
py -3.13 -m unittest discover -s tests -v
```

To run one test, use `py -3.13 -m unittest tests.test_backtest.BacktestTests.test_equal_weight_rebalances_and_charges_costs -v`. There is no separate linter configured.

Configure runs by editing `DATA_FILE`, `OUTPUT_DIR`, `STRATEGY_FILE`, `INITIAL_CAPITAL`, `FEE_RATE`, `REBALANCE_FREQUENCY`, `START_DATE`, `END_DATE`, `ANNUAL_RISK_FREE_RATE`, `ANNUAL_MAR`, `VAR_CONFIDENCE_LEVEL`, and optionally `RESULT_NAME` in `fundbacktesting/__main__.py`. Strategies live in the repository-root `strategies/` folder; framework implementation lives in `fundbacktesting/`. Run `py -3.13 -m fundbacktesting`; artifacts go under the repository-root `results/`.
`REBALANCE_FREQUENCY` accepts `none`, `daily`, `weekly`, or `monthly`. `none` invokes the strategy and trades only on the first backtest date; subsequent holdings are held without rebalancing.

## High-level architecture

The project is a general-purpose fund backtesting framework with strict separation of responsibilities:

- Data ingestion and adaptation: external market data sources are normalized into a source-agnostic standard format.
- Core backtesting engine: the engine owns the date loop, state transitions, and execution flow.
- Strategy layer: strategies decide target portfolio weights without directly mutating engine state.
- Execution layer: the executor translates target allocations into trades, applies costs, and updates portfolio state.
- Evaluation layer: metrics are computed from the raw results object instead of being mixed into the engine.

The README describes a flow with three primary phases:

1. Read data
   - Adapt raw source data into standard fields.
   - Normalize values and align dates to the market calendar.
   - Produce ready-to-consume structures such as a wide NAV table, metadata lookup dictionaries, and a date-based fund pool snapshot.

2. Run the backtest engine
   - Walk the trading calendar.
   - On rebalance dates, assemble a context object and call `strategy.decide(context)`.
   - Validate the target weights.
   - Execute the trade with a cost model and update holdings/cash/cost logs.
   - Accumulate daily portfolio NAV using the holdings and returns.

3. Produce results and evaluation
   - The engine emits raw result objects: NAV series, holdings series, trade logs, cost logs, and config snapshots.
   - The evaluation layer consumes those results and calculates returns, risk, relative metrics, turnover, and cost metrics without altering the engine behavior.

The architectural backbone is a contract-centered dependency model:

- `fundbacktesting/contracts.py`: shared market data, strategy context, configuration, and result types.
- `fundbacktesting/engine.py`: owns rebalance scheduling, portfolio state, target validation, trade execution, and costs.
- `fundbacktesting/adapter.py`: maps the sample workbook layout into source-independent market data.
- `strategies/`: repository-root folder for user-editable strategy modules; each module exposes `create_strategy()` and may define `STRATEGY_NAME`.
- `fundbacktesting/evaluation.py`: computes metrics only from the result object.
- `fundbacktesting/runner.py`: outer composition and CSV/JSON output.
- `fundbacktesting/strategy_loader.py`: loads the selected strategy module from its file path.

The default workbook is `data/Data.xlsx`. Its adapter reads fund names from row 4, codes from row 5, and dated adjusted NAV values from row 6 onward; change `DATA_FILE` in `fundbacktesting/__main__.py` to select another workbook. The runner writes each run's trajectory and config snapshot under `results/<run_id>/`, while keeping the aggregate `metrics_summary.csv` at the results root.
Output filenames start with the strategy name and run timestamp; `RESULT_NAME` in `fundbacktesting/__main__.py` can override the shared output prefix.

The runner is the composition boundary; keep imports flowing inward to contracts. The engine must not depend on a concrete data source, strategy, or evaluation implementation.

## Key conventions

These conventions are more important than file-level details because they define the design intent of the repository:

- Unidirectional dependency boundaries: adapters depend on contracts, the engine depends on contracts, strategies depend on contracts, and the evaluation layer depends on result contracts. The engine does not know about concrete data sources or concrete strategy implementations.
- Time is controlled by the engine, not the strategy. Strategies are asked only on rebalance dates and do not decide when the engine runs.
- Strategies are pure decision functions: `decide(context) -> target`, with state changes handled by the executor rather than inside strategy code. Target fund weights may sum to less than 1; the remainder is retained as cash.
- Execution and decision are separated. The strategy declares the desired target weights; the executor determines how to trade to reach those weights and how much the trade costs.
- Cost logic is centralized. Fees, slippage, and holding-period penalties should live in one execution/cost model rather than being spread across strategies or adapters.
- Data-source independence is a core principle. The system must consume source-specific inputs only through adapters that convert them into standard contract objects.
- The result object is the canonical source of truth for performance analysis. The evaluation layer consumes it, rather than re-deriving outcomes from internal engine state.
- Configuration is data, not code. Parameter and mapping changes should not require structural code changes in the engine or strategies.
- Use the `Strategy` protocol (`decide(context) -> target weights`) when adding a strategy; keep portfolio mutation and transaction-cost calculations in the engine.
- Preserve the data availability rule: do not backfill pre-inception NAVs. The engine may mark an existing position at its last observed NAV between observations.
- Add behavior tests under `tests/` and use the exact unittest command above, or its single-test form.

## Working in this repository

- Use [README.md](../README.md) as the main source of truth for system boundaries, sample data format, and the execution assumption.
- When implementing code, keep the design layered and contract-based rather than mixing engine logic, data-source logic, and evaluation logic together.
- Prefer a clean mapping from raw data sources to standard contracts, then from contracts to execution and evaluation.
- Do not introduce a direct dependency from the engine to a specific source, strategy, or evaluation model; keep that coupling at the orchestration boundary only.

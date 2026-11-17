from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from .contracts import BacktestConfig
from .runner import run_from_files
from .strategy_loader import load_strategy

# Edit these values to configure a run.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPOSITORY_ROOT / "data"
DATA_FILE = DATA_DIR / "Data.xlsx"
OUTPUT_DIR = REPOSITORY_ROOT / "results"
STRATEGY_FILE = REPOSITORY_ROOT / "strategies" / "equal_weight.py"
INITIAL_CAPITAL = 1_000_000.0
FEE_RATE = 0.0005
REBALANCE_FREQUENCY = "monthly"  # "none", "daily", "weekly", or "monthly"
START_DATE: date | None = None
END_DATE: date | None = None
RESULT_NAME: str | None = None
ANNUAL_RISK_FREE_RATE = 0.02
ANNUAL_MAR = 0.0
VAR_CONFIDENCE_LEVEL = 0.95


def main() -> None:
    strategy, strategy_name = load_strategy(STRATEGY_FILE)
    run_id = RESULT_NAME or f"{strategy_name}_{datetime.now():%Y%m%d_%H%M%S}"
    config = BacktestConfig(
        initial_capital=INITIAL_CAPITAL,
        rebalance_frequency=REBALANCE_FREQUENCY,
        fee_rate=FEE_RATE,
        start_date=START_DATE,
        end_date=END_DATE,
        annual_risk_free_rate=ANNUAL_RISK_FREE_RATE,
        annual_mar=ANNUAL_MAR,
        var_confidence_level=VAR_CONFIDENCE_LEVEL,
    )
    paths = run_from_files(DATA_FILE, OUTPUT_DIR, config, strategy, run_id)
    print(f"Backtest completed: {run_id}")
    for name, path in paths.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()

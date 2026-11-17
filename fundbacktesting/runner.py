from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from .adapter import load_market_data
from .contracts import BacktestConfig, Strategy
from .engine import run_backtest
from .evaluation import evaluate


def run_from_files(
    data_path: str | Path,
    output_dir: str | Path,
    config: BacktestConfig,
    strategy: Strategy,
    run_id: str,
) -> dict[str, Path]:
    if not re.fullmatch(r"[\w-]+", run_id, flags=re.UNICODE):
        raise ValueError(
            "run_id may contain only letters, numbers, underscores, and hyphens."
        )

    market_data = load_market_data(data_path)
    result = run_backtest(market_data, strategy, config)
    metrics = evaluate(result)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    run_output = output / run_id
    run_output.mkdir(parents=True, exist_ok=True)

    paths = {
        "nav": run_output / f"{run_id}_nav.csv",
        "holdings": run_output / f"{run_id}_holdings.csv",
        "trades": run_output / f"{run_id}_trades.csv",
        "costs": run_output / f"{run_id}_costs.csv",
        "metrics": run_output / f"{run_id}_metrics.json",
        "config": run_output / f"{run_id}_config.json",
        "metrics_summary": output / "metrics_summary.csv",
    }
    with paths["nav"].open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(("date", "nav"))
        writer.writerows((point.date.isoformat(), point.value) for point in result.nav_series)

    with paths["holdings"].open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(("date", "fund_code", "units", "market_value", "weight"))
        for snapshot in result.holdings_series:
            for fund_code, units in sorted(snapshot.units.items()):
                writer.writerow(
                    (
                        snapshot.date.isoformat(),
                        fund_code,
                        units,
                        snapshot.market_values[fund_code],
                        snapshot.weights.get(fund_code, 0.0),
                    )
                )
            writer.writerow(
                (snapshot.date.isoformat(), "CASH", "", "", snapshot.cash_weight)
            )

    with paths["trades"].open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(("date", "fund_code", "direction", "units", "notional", "cost"))
        writer.writerows(
            (
                trade.date.isoformat(),
                trade.fund_code,
                trade.direction,
                trade.units,
                trade.notional,
                trade.cost,
            )
            for trade in result.trade_log
        )

    with paths["costs"].open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(("date", "fund_code", "notional", "fee_rate", "cost"))
        writer.writerows(
            (
                cost.date.isoformat(),
                cost.fund_code,
                cost.notional,
                cost.fee_rate,
                cost.cost,
            )
            for cost in result.cost_log
        )

    for key, payload in (
        ("metrics", metrics),
        ("config", {**result.config_snapshot, "run_id": run_id}),
    ):
        paths[key].write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )

    summary_rows: list[dict[str, str]] = []
    summary_fields = ["run_id", *metrics.keys()]
    if paths["metrics_summary"].exists():
        with paths["metrics_summary"].open(
            "r", newline="", encoding="utf-8-sig"
        ) as file:
            reader = csv.DictReader(file)
            for field in reader.fieldnames or ():
                if field not in summary_fields:
                    summary_fields.append(field)
            summary_rows.extend(
                row for row in reader if row.get("run_id") != run_id
            )
    summary_rows.append({"run_id": run_id, **metrics})
    with paths["metrics_summary"].open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=summary_fields)
        writer.writeheader()
        writer.writerows(summary_rows)
    return paths

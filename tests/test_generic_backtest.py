from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from openpyxl import Workbook

from fundbacktesting.adapter import (
    load_instrument_meta_csv,
    load_nav_csv,
    load_risk_free_csv,
    with_market_context,
)
from fundbacktesting.contracts import (
    BacktestConfig,
    BacktestContext,
    BacktestResult,
    EvaluationResult,
    MarketData,
    NavPoint,
)
from fundbacktesting.engine import run_backtest
from fundbacktesting.evaluation import available_conventions, evaluate
from fundbacktesting.reference import compare_to_baseline, load_baseline
from fundbacktesting.runner import run_from_files


class RecordingStrategy:
    def __init__(self, target: dict[str, float] | None = None) -> None:
        self.target = target or {}
        self.contexts: list[BacktestContext] = []
        self.call_dates: list[date] = []

    def decide(self, context: BacktestContext) -> dict[str, float]:
        self.contexts.append(context)
        self.call_dates.append(context.current_date)
        return dict(self.target)


class LastVisiblePriceStrategy:
    def __init__(self) -> None:
        self.decisions: list[tuple[date, date, float]] = []

    def decide(self, context: BacktestContext) -> dict[str, float]:
        visible = context.history_before_current
        last_day = max(visible)
        last_price = float(visible[last_day]["A"])
        self.decisions.append((context.current_date, last_day, last_price))
        weight = min(1.0, last_price / 2.0)
        return {"A": weight, "B": 1.0 - weight}


class GenericBacktestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dates = (date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4))
        self.market_data = MarketData(
            dates=self.dates,
            prices={
                self.dates[0]: {"A": 1.0, "B": 2.0},
                self.dates[1]: {"A": 1.1, "B": 2.0},
                self.dates[2]: {"A": 1.2, "B": 2.2},
            },
            fund_names={"A": "Fund A", "B": "Fund B"},
            source_sha256="test-hash",
        )

    def test_explicit_rebalance_dates_override_frequency(self) -> None:
        strategy = RecordingStrategy({"A": 0.5, "B": 0.5})
        config = BacktestConfig(
            fee_rate=0.0,
            rebalance_frequency="monthly",
            rebalance_dates=(self.dates[1], self.dates[2]),
            charge_initial_cost=False,
        )

        result = run_backtest(self.market_data, strategy, config)

        self.assertEqual(strategy.call_dates, [self.dates[1], self.dates[2]])
        self.assertEqual(result.config_snapshot["rebalance_count"], 2)
        self.assertEqual(
            result.config_snapshot["rebalance_dates"],
            [day.isoformat() for day in (self.dates[1], self.dates[2])],
        )
        self.assertEqual(
            {trade.date for trade in result.trade_log},
            {self.dates[1], self.dates[2]},
        )

    def test_history_before_current_excludes_execution_day(self) -> None:
        first = LastVisiblePriceStrategy()
        second = LastVisiblePriceStrategy()
        config = BacktestConfig(
            fee_rate=0.0,
            rebalance_dates=self.dates[1:],
            charge_initial_cost=False,
        )
        changed_prices = dict(self.market_data.prices)
        changed_prices[self.dates[2]] = {"A": 9.9, "B": 2.2}
        changed_data = MarketData(
            dates=self.market_data.dates,
            prices=changed_prices,
            fund_names=self.market_data.fund_names,
            source_sha256=self.market_data.source_sha256,
        )

        baseline_result = run_backtest(self.market_data, first, config)
        changed_result = run_backtest(changed_data, second, config)

        self.assertEqual(first.decisions, second.decisions)
        for called_date, visible_date, _ in first.decisions:
            self.assertLess(visible_date, called_date)
        self.assertEqual(
            [day for day, _, _ in first.decisions],
            [self.dates[1], self.dates[2]],
        )
        self.assertNotEqual(
            baseline_result.nav_series[-1].value,
            changed_result.nav_series[-1].value,
        )

    def test_decision_lag_separates_visible_date_from_execution_date(self) -> None:
        strategy = RecordingStrategy({"A": 1.0})
        config = BacktestConfig(
            fee_rate=0.0,
            rebalance_dates=(self.dates[2],),
            decision_lag=1,
        )

        run_backtest(self.market_data, strategy, config)

        self.assertEqual(len(strategy.contexts), 1)
        context = strategy.contexts[0]
        self.assertEqual(context.current_date, self.dates[1])
        self.assertEqual(context.decision_date, self.dates[2])
        self.assertEqual(max(context.history), self.dates[1])
        self.assertEqual(max(context.history_before_current), self.dates[0])

    def test_initial_build_can_skip_costs_and_turnover(self) -> None:
        target = {"A": 0.5, "B": 0.5}
        config = BacktestConfig(
            fee_rate=0.01,
            rebalance_dates=self.dates[:2],
            charge_initial_cost=False,
        )
        result = run_backtest(self.market_data, RecordingStrategy(target), config)

        self.assertEqual(result.cost_log[0].date, self.dates[1])
        self.assertGreater(sum(item.cost for item in result.cost_log), 0)
        self.assertEqual(
            [day for day, _ in result.rebalance_turnover],
            [self.dates[1]],
        )
        expected_turnover = abs(1.1 / 2.1 - 0.5) * 2
        self.assertAlmostEqual(result.rebalance_turnover[0][1], expected_turnover)

    def test_initial_build_is_charged_by_default(self) -> None:
        config = BacktestConfig(
            fee_rate=0.01,
            rebalance_dates=self.dates[:2],
        )
        result = run_backtest(
            self.market_data,
            RecordingStrategy({"A": 0.5, "B": 0.5}),
            config,
        )

        self.assertEqual(result.cost_log[0].date, self.dates[0])
        self.assertGreater(len(result.rebalance_turnover), 1)

    def test_invalid_new_config_fields_are_rejected(self) -> None:
        strategy = RecordingStrategy({"A": 1.0})
        with self.assertRaisesRegex(ValueError, "decision_lag"):
            run_backtest(self.market_data, strategy, BacktestConfig(decision_lag=-1))
        with self.assertRaisesRegex(ValueError, "rebalance_dates must be a tuple"):
            run_backtest(
                self.market_data,
                strategy,
                BacktestConfig(rebalance_dates=[self.dates[0]]),
            )
        with self.assertRaisesRegex(ValueError, "No explicit rebalance date"):
            run_backtest(
                self.market_data,
                strategy,
                BacktestConfig(rebalance_dates=(date(2030, 1, 1),)),
            )

    def test_evaluation_conventions_are_explicit_and_compareable(self) -> None:
        self.assertEqual(available_conventions(), ("daily_365", "framework"))
        start, end = date(2024, 1, 1), date(2024, 1, 11)
        result = BacktestResult(
            nav_series=(NavPoint(start, 1.0), NavPoint(end, 1.1)),
            holdings_series=(),
            trade_log=(),
            cost_log=(),
            config_snapshot={
                "initial_capital": 1_000_000.0,
                "rebalance_count": 1,
                "annual_risk_free_rate": 0.02,
                "annual_mar": 0.0,
                "var_confidence_level": 0.95,
                "metric_convention": "daily_365",
            },
            rebalance_changes=(2,),
            rebalance_turnover=((end, 0.2),),
            risk_free_rates={start: 0.01, end: 0.02},
        )

        evaluation = evaluate(result)
        metrics = evaluation.metrics

        self.assertEqual(evaluation.convention, "daily_365")
        self.assertAlmostEqual(metrics["annualized_return"], 1.1 ** (365 / 10) - 1)
        self.assertEqual(metrics["annualized_volatility"], 0.0)
        self.assertIsNone(metrics["sharpe_ratio"])
        self.assertAlmostEqual(metrics["average_daily_risk_free_rate"], 0.015)
        self.assertAlmostEqual(
            metrics["turnover_one_way_annualized"],
            (0.2 / (10 / 365)) / 2,
        )
        self.assertEqual(metrics["rebalance_count_sum"], 2)
        self.assertIn("365 calendar days", evaluation.units["annualized_return"])
        self.assertIn("daily series", evaluation.convention_notes["risk_free_rate"])
        self.assertEqual(evaluation.as_dict(), metrics)

    def test_reference_comparison_is_report_only(self) -> None:
        evaluation = EvaluationResult(
            metrics={"return": 0.11, "missing": None},
            convention="framework",
        )
        baseline = {
            "name": "unit-baseline",
            "metrics": {
                "return": {"value": 0.10, "tolerance": 0.02},
                "missing": {"value": 0.0, "tolerance": 0.01},
            },
        }

        report = compare_to_baseline(evaluation, baseline)

        self.assertFalse(report.passed)
        self.assertEqual(report.baseline_name, "unit-baseline")
        self.assertTrue(report.checks[0].passed)
        self.assertFalse(report.checks[1].passed)
        self.assertAlmostEqual(report.checks[0].computed, 0.11)
        self.assertEqual(report.as_dict()["checks"][1]["computed"], None)

    def test_csv_adapters_attach_optional_context_without_interpolation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            nav_path = root / "nav.csv"
            nav_path.write_text(
                "日期,A,B\n2024-01-02,1.0,\n2024-01-03,1.1,2.0\n",
                encoding="utf-8",
            )
            rf_path = root / "rf.csv"
            rf_path.write_text("date,rate\n2024-01-02,1.6\n", encoding="utf-8")
            meta_path = root / "meta.csv"
            meta_path.write_text(
                "code,product_type,inception_date,eligibility_start,name\n"
                "A,public_fund,2023-01-01,2023-02-01,基金A\n",
                encoding="utf-8",
            )

            loaded = load_nav_csv(nav_path)
            rates = load_risk_free_csv(rf_path)
            meta = load_instrument_meta_csv(meta_path)
            enriched = with_market_context(
                loaded,
                risk_free_rates=rates,
                fund_meta=meta,
                extra_fingerprints={"rf.csv": "rf-hash"},
            )

        self.assertEqual(loaded.prices[date(2024, 1, 2)], {"A": 1.0})
        self.assertEqual(loaded.prices[date(2024, 1, 3)], {"A": 1.1, "B": 2.0})
        self.assertAlmostEqual(rates[date(2024, 1, 2)], 0.016)
        self.assertEqual(meta["A"].product_type, "public_fund")
        self.assertEqual(meta["A"].inception_date, date(2023, 1, 1))
        self.assertIsNone(loaded.risk_free_rates)
        self.assertEqual(enriched.risk_free_rates, rates)
        self.assertEqual(enriched.source_fingerprints["rf.csv"], "rf-hash")

    def test_runner_writes_auditable_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workbook_path = root / "sample.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(["开始日期", None])
            sheet.append(["截止日期", None])
            sheet.append(["复权单位净值", "NAV_adj"])
            sheet.append(["日期", "基金甲"])
            sheet.append(["Date", "FUND_A"])
            sheet.append([date(2024, 1, 2), 1.0])
            sheet.append([date(2024, 1, 3), 1.1])
            workbook.save(workbook_path)

            paths = run_from_files(
                workbook_path,
                root / "results",
                BacktestConfig(fee_rate=0, metric_convention="framework"),
                RecordingStrategy({"FUND_A": 1.0}),
                "manifest_test",
            )
            manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))

        for key in (
            "schema",
            "run_id",
            "created_at_utc",
            "code_version",
            "python_version",
            "platform",
            "metric_convention",
            "input_fingerprints",
            "source_sha256",
            "config",
        ):
            self.assertIn(key, manifest)
        self.assertEqual(manifest["schema"], "run-manifest/v1")
        self.assertEqual(manifest["run_id"], "manifest_test")
        self.assertEqual(manifest["metric_convention"], "framework")
        self.assertIn("sample.xlsx", manifest["input_fingerprints"])
        self.assertEqual(len(manifest["source_sha256"]), 64)

    def test_baseline_loader_requires_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "baseline.json"
            path.write_text('{"name": "bad"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "metrics"):
                load_baseline(path)


if __name__ == "__main__":
    unittest.main()

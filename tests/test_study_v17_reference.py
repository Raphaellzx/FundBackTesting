from __future__ import annotations

import hashlib
import unittest
from datetime import date
from pathlib import Path

from cases.study_v17.data import build_market_data
from fundbacktesting.contracts import BacktestConfig
from fundbacktesting.engine import run_backtest
from fundbacktesting.evaluation import evaluate
from fundbacktesting.reference import compare_to_baseline, load_baseline
from fundbacktesting.strategy_loader import load_strategy

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
BASELINE_PATH = Path(__file__).resolve().parent / "reference" / "study-baseline.json"
STRATEGY_PATH = ROOT / "strategies" / "study_v17.py"

INPUT_FILES = ("nav_pub119.csv", "nav5c.csv", "rf1y.csv")


def _reference_inputs_available() -> bool:
    return (
        STRATEGY_PATH.is_file()
        and BASELINE_PATH.is_file()
        and all((DATA_DIR / name).is_file() for name in INPUT_FILES)
    )


@unittest.skipUnless(_reference_inputs_available(), "reference inputs are not available")
class StudyV17ReferenceTests(unittest.TestCase):
    """用外置基准验证通用框架上的研究策略实现。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.baseline = load_baseline(BASELINE_PATH)
        params = cls.baseline["parameters"]
        cls.strategy, cls.strategy_name = load_strategy(STRATEGY_PATH)
        cls.market_data = build_market_data()
        cls.config = BacktestConfig(
            initial_capital=float(params["initial_capital"]),
            rebalance_frequency="none",
            fee_rate=float(params["fee_rate"]),
            start_date=date.fromisoformat(params["start_date"]),
            end_date=date.fromisoformat(params["end_date"]),
            rebalance_dates=tuple(
                date.fromisoformat(day) for day in cls.baseline["rebalance_dates"]
            ),
            charge_initial_cost=False,
            metric_convention="daily_365",
        )
        cls.result = run_backtest(cls.market_data, cls.strategy, cls.config)
        cls.evaluation = evaluate(cls.result, "daily_365")

    def test_strategy_is_loaded_through_standard_slot(self) -> None:
        self.assertEqual(self.strategy_name, "study_v17")
        self.assertTrue(callable(self.strategy.decide))

    def test_input_fingerprints_match_baseline(self) -> None:
        expected = self.baseline["inputs"]
        self.assertEqual(set(expected), set(INPUT_FILES))
        for name, digest in expected.items():
            with self.subTest(input=name):
                actual = hashlib.sha256((DATA_DIR / name).read_bytes()).hexdigest()
                self.assertEqual(actual, digest)

    def test_every_rebalance_holding_matches_reference(self) -> None:
        expected_dates = tuple(
            date.fromisoformat(day) for day in self.baseline["rebalance_dates"]
        )
        actual_by_date = {
            snapshot.date: {
                code for code, weight in snapshot.weights.items() if weight > 0
            }
            for snapshot in self.result.holdings_series
        }
        divergences: list[dict[str, object]] = []
        for trading_date in expected_dates:
            expected = set(self.baseline["holdings"][trading_date.isoformat()])
            actual = actual_by_date.get(trading_date, set())
            if actual != expected:
                divergences.append(
                    {
                        "date": trading_date.isoformat(),
                        "expected": sorted(expected),
                        "actual": sorted(actual),
                    }
                )

        self.assertEqual(len(expected_dates), 17)
        self.assertEqual(divergences, [])
        self.assertEqual(
            len(self.result.rebalance_changes),
            len(expected_dates),
        )

    def test_primary_metrics_match_external_baseline(self) -> None:
        report = compare_to_baseline(self.evaluation, self.baseline)
        failures = [
            check.as_dict() if hasattr(check, "as_dict") else check
            for check in report.checks
            if not check.passed
        ]
        self.assertTrue(report.passed, failures)
        self.assertEqual(len(report.checks), 7)

    def test_baseline_is_not_a_calculation_input(self) -> None:
        self.assertNotIn("metrics", tuple(self.result.config_snapshot))
        self.assertEqual(self.config.metric_convention, "daily_365")
        self.assertEqual(self.baseline["schema"], "reference-baseline/v1")


if __name__ == "__main__":
    unittest.main()

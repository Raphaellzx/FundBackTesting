from __future__ import annotations

import hashlib
import unittest

from fundbacktesting.contracts import BacktestConfig
from fundbacktesting.engine import run_backtest
from fundbacktesting.evaluation import evaluate
from fundbacktesting.reference import compare_to_baseline, load_baseline
from fundbacktesting.strategy_loader import load_strategy
from studies.study_v17 import config
from studies.study_v17.build import build_market_data

BASELINE_PATH = config.BASELINE_PATH
STRATEGY_PATH = config.STRATEGY_PATH
INPUT_FILES = config.INPUT_FILES
INPUT_DIR = config.INPUT_DIR


def _reference_inputs_available() -> bool:
    return (
        STRATEGY_PATH.is_file()
        and BASELINE_PATH.is_file()
        and all((INPUT_DIR / name).is_file() for name in INPUT_FILES)
    )


@unittest.skipUnless(_reference_inputs_available(), "study inputs are not available")
class StudyV17ReferenceTests(unittest.TestCase):
    """用研究目录内的外置基准验证通用框架上的策略实现。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.baseline = load_baseline(BASELINE_PATH)
        cls.strategy, cls.strategy_name = load_strategy(STRATEGY_PATH)
        cls.market_data = build_market_data()
        cls.config = BacktestConfig(
            initial_capital=config.INITIAL_CAPITAL,
            rebalance_frequency=config.REBALANCE_FREQUENCY,
            fee_rate=config.FEE_RATE,
            start_date=config.START_DATE,
            end_date=config.END_DATE,
            rebalance_dates=config.REBALANCE_DATES,
            charge_initial_cost=config.CHARGE_INITIAL_COST,
            metric_convention=config.METRIC_CONVENTION,
        )
        cls.result = run_backtest(cls.market_data, cls.strategy, cls.config)
        cls.evaluation = evaluate(cls.result, config.METRIC_CONVENTION)

    def test_strategy_is_loaded_through_standard_slot(self) -> None:
        self.assertEqual(self.strategy_name, config.STUDY_NAME)
        self.assertTrue(callable(self.strategy.decide))

    def test_study_config_matches_recorded_baseline_parameters(self) -> None:
        recorded = self.baseline["parameters"]
        self.assertEqual(recorded["initial_capital"], config.INITIAL_CAPITAL)
        self.assertEqual(recorded["fee_rate"], config.FEE_RATE)
        self.assertEqual(recorded["start_date"], config.START_DATE.isoformat())
        self.assertEqual(recorded["end_date"], config.END_DATE.isoformat())
        self.assertEqual(
            tuple(self.baseline["rebalance_dates"]),
            tuple(day.isoformat() for day in config.REBALANCE_DATES),
        )

    def test_input_fingerprints_match_baseline(self) -> None:
        expected = self.baseline["inputs"]
        self.assertEqual(set(expected), set(INPUT_FILES))
        for name, digest in expected.items():
            with self.subTest(input=name):
                actual = hashlib.sha256((INPUT_DIR / name).read_bytes()).hexdigest()
                self.assertEqual(actual, digest)

    def test_every_rebalance_holding_matches_reference(self) -> None:
        expected_dates = config.REBALANCE_DATES
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
        self.assertEqual(self.config.metric_convention, config.METRIC_CONVENTION)
        self.assertEqual(self.baseline["schema"], "reference-baseline/v1")


if __name__ == "__main__":
    unittest.main()

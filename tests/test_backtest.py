from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

import fundbacktesting.__main__ as backtest_entrypoint
from fundbacktesting.adapter import load_market_data
from fundbacktesting.contracts import (
    BacktestConfig,
    BacktestContext,
    BacktestResult,
    MarketData,
    NavPoint,
)
from fundbacktesting.engine import run_backtest
from fundbacktesting.evaluation import evaluate
from fundbacktesting.runner import run_from_files
from fundbacktesting.strategy_loader import load_strategy


class FixedTargetStrategy:
    def __init__(self, target):
        self.target = target

    def decide(self, context: BacktestContext):
        return self.target


class CountingStrategy(FixedTargetStrategy):
    def __init__(self, target):
        super().__init__(target)
        self.call_dates = []

    def decide(self, context: BacktestContext):
        self.call_dates.append(context.current_date)
        return super().decide(context)


def load_equal_weight_strategy():
    strategy_path = (
        Path(__file__).resolve().parents[1] / "strategies" / "equal_weight.py"
    )
    return load_strategy(strategy_path)[0]


class BacktestTests(unittest.TestCase):
    def setUp(self):
        self.market_data = MarketData(
            dates=(
                date(2024, 1, 2),
                date(2024, 1, 3),
                date(2024, 2, 1),
            ),
            prices={
                date(2024, 1, 2): {"A": 1.0, "B": 2.0},
                date(2024, 1, 3): {"A": 1.1, "B": 1.9},
                date(2024, 2, 1): {"A": 1.2, "B": 2.0},
            },
            fund_names={"A": "Fund A", "B": "Fund B"},
            source_sha256="test-hash",
        )

    def test_equal_weight_rebalances_and_charges_costs(self):
        result = run_backtest(
            self.market_data,
            load_equal_weight_strategy(),
            BacktestConfig(initial_capital=100_000, fee_rate=0.001),
        )

        self.assertEqual(len(result.nav_series), 3)
        self.assertEqual(result.config_snapshot["rebalance_count"], 2)
        self.assertGreater(sum(item.cost for item in result.cost_log), 0)
        self.assertAlmostEqual(
            sum(result.holdings_series[0].weights.values())
            + result.holdings_series[0].cash_weight,
            1.0,
        )
        self.assertGreater(evaluate(result)["total_return"], 0)

    def test_evaluation_uses_monthly_ratios_and_daily_tail_risk(self):
        nav_values = (1.0, 1.1, 0.99, 1.089, 0.98)
        dates = (
            date(2025, 4, 1),
            date(2025, 4, 2),
            date(2025, 5, 2),
            date(2025, 5, 3),
            date(2025, 6, 1),
        )
        result = BacktestResult(
            nav_series=tuple(
                NavPoint(trading_date, nav)
                for trading_date, nav in zip(dates, nav_values)
            ),
            holdings_series=(),
            trade_log=(),
            cost_log=(),
            config_snapshot={
                "initial_capital": 1_000_000.0,
                "rebalance_count": 0,
                "annual_risk_free_rate": 0.02,
                "annual_mar": 0.0,
                "var_confidence_level": 0.95,
            },
        )

        metrics = evaluate(result)
        monthly_returns = (0.1, 1.089 / 1.1 - 1.0, 0.98 / 1.089 - 1.0)
        mean_return = sum(monthly_returns) / len(monthly_returns)
        monthly_rf = 1.02 ** (1 / 12) - 1
        monthly_downside_deviation = (
            (sum(min(value, 0.0) ** 2 for value in monthly_returns) / 3) ** 0.5
        )

        self.assertAlmostEqual(metrics["annual_risk_free_rate"], 0.02)
        self.assertAlmostEqual(
            metrics["annualized_volatility"],
            (
                sum((value - mean_return) ** 2 for value in monthly_returns) / 2
            )
            ** 0.5
            * 12**0.5,
        )
        self.assertAlmostEqual(
            metrics["sharpe_ratio"],
            (mean_return - monthly_rf)
            / (
                sum((value - mean_return) ** 2 for value in monthly_returns) / 2
            )
            ** 0.5
            * 12**0.5,
        )
        self.assertAlmostEqual(
            metrics["downside_deviation"],
            monthly_downside_deviation * 12**0.5,
        )
        self.assertAlmostEqual(
            metrics["sortino_ratio"],
            mean_return / monthly_downside_deviation * 12**0.5,
        )
        self.assertAlmostEqual(
            metrics["omega_ratio"],
            0.1 / sum(abs(value) for value in monthly_returns if value < 0),
        )
        self.assertAlmostEqual(metrics["max_drawdown"], 0.98 / 1.1 - 1.0)
        self.assertEqual(metrics["max_drawdown_duration_trading_days"], 3)
        self.assertAlmostEqual(metrics["historical_var_1d"], 1 - 0.98 / 1.089)
        self.assertAlmostEqual(
            metrics["historical_cvar_1d"], metrics["historical_var_1d"]
        )
        self.assertEqual(metrics["var_confidence_level"], 0.95)

    def test_invalid_strategy_target_is_rejected(self):
        strategy = FixedTargetStrategy({"A": 0.6, "B": 0.5})
        with self.assertRaisesRegex(ValueError, "at most 1"):
            run_backtest(self.market_data, strategy, BacktestConfig())

    def test_strategy_can_leave_target_weight_as_cash(self):
        strategy = FixedTargetStrategy({"A": 0.99})
        result = run_backtest(
            self.market_data,
            strategy,
            BacktestConfig(fee_rate=0, rebalance_frequency="monthly"),
        )

        self.assertAlmostEqual(result.holdings_series[0].weights["A"], 0.99)
        self.assertAlmostEqual(result.holdings_series[0].cash_weight, 0.01)

    def test_no_rebalance_only_decides_and_trades_on_first_day(self):
        strategy = CountingStrategy({"A": 0.5, "B": 0.5})
        result = run_backtest(
            self.market_data,
            strategy,
            BacktestConfig(
                initial_capital=100_000,
                fee_rate=0,
                rebalance_frequency="none",
            ),
        )

        self.assertEqual(strategy.call_dates, [date(2024, 1, 2)])
        self.assertEqual(result.config_snapshot["rebalance_count"], 1)
        self.assertEqual(len(result.trade_log), 2)
        self.assertNotAlmostEqual(
            result.holdings_series[0].weights["A"],
            result.holdings_series[1].weights["A"],
        )
        self.assertEqual(
            result.holdings_series[1].units,
            result.holdings_series[2].units,
        )

    def test_requested_portfolio_strategy_allocations(self):
        repository_root = Path(backtest_entrypoint.__file__).resolve().parents[1]
        context = BacktestContext(
            current_date=date(2025, 4, 1),
            history={},
            holdings={},
            active_funds=(
                "BZ22167.OF",
                "BZ22106.OF",
                "BZ23085.OF",
                "BZ20342.OF",
                "BZ22121.OF",
            ),
        )
        first, _ = load_strategy(
            repository_root / "strategies" / "portfolio1_four_long_bonds.py"
        )
        second, _ = load_strategy(
            repository_root / "strategies" / "portfolio2_four_long_short_cash.py"
        )

        self.assertEqual(set(first.decide(context).values()), {0.25})
        second_weights = second.decide(context)
        self.assertEqual(sum(second_weights.values()), 0.99)
        self.assertEqual(second_weights["BZ22121.OF"], 0.03)

    def test_strategy_cannot_select_unavailable_fund(self):
        strategy = FixedTargetStrategy({"A": 0.5, "MISSING": 0.5})
        with self.assertRaisesRegex(ValueError, "unavailable fund"):
            run_backtest(self.market_data, strategy, BacktestConfig())

    def test_adapter_reads_sample_workbook_layout(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            workbook_path = Path(temporary_directory) / "sample.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(["开始日期", None, None])
            sheet.append(["截止日期", None, None])
            sheet.append(["复权单位净值", "NAV_adj", None])
            sheet.append(["日期", "基金甲", "基金乙"])
            sheet.append(["Date", "FUND_A", "FUND_B"])
            sheet.append([date(2024, 1, 2), 1.0, None])
            sheet.append([date(2024, 1, 3), 1.1, 2.0])
            workbook.save(workbook_path)

            loaded = load_market_data(workbook_path)

        self.assertEqual(loaded.fund_names, {"FUND_A": "基金甲", "FUND_B": "基金乙"})
        self.assertEqual(loaded.dates, (date(2024, 1, 2), date(2024, 1, 3)))
        self.assertEqual(loaded.prices[date(2024, 1, 2)], {"FUND_A": 1.0})

    def test_runner_writes_run_artifacts(self):
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
                BacktestConfig(fee_rate=0),
                load_equal_weight_strategy(),
                "unit_test",
            )

            self.assertTrue(all(path.is_file() for path in paths.values()))
            self.assertEqual(paths["nav"].parent, root / "results" / "unit_test")
            self.assertEqual(paths["nav"].name, "unit_test_nav.csv")
            self.assertEqual(
                paths["metrics_summary"], root / "results" / "metrics_summary.csv"
            )
            self.assertIn("total_return", paths["metrics"].read_text(encoding="utf-8"))
            summary_path = paths["metrics_summary"]
            run_from_files(
                workbook_path,
                root / "results",
                BacktestConfig(fee_rate=0),
                load_equal_weight_strategy(),
                "unit_test",
            )
            updated_summary = summary_path.read_text(encoding="utf-8")
            self.assertIn("historical_var_1d", updated_summary)
            self.assertEqual(updated_summary.count("unit_test,"), 1)

    def test_strategy_loader_loads_strategy_from_file(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            strategy_path = Path(temporary_directory) / "fixed_mix.py"
            strategy_path.write_text(
                "STRATEGY_NAME = 'fixed_mix'\n"
                "class FixedMix:\n"
                "    def decide(self, context):\n"
                "        return {context.active_funds[0]: 1.0}\n"
                "def create_strategy():\n"
                "    return FixedMix()\n",
                encoding="utf-8",
            )

            strategy, strategy_name = load_strategy(strategy_path)

        self.assertEqual(strategy_name, "fixed_mix")
        self.assertTrue(callable(strategy.decide))

    def test_default_strategy_is_loaded_from_repository_root(self):
        repository_root = Path(backtest_entrypoint.__file__).resolve().parents[1]
        strategy_path = repository_root / "strategies" / "equal_weight.py"

        strategy, strategy_name = load_strategy(strategy_path)

        self.assertEqual(strategy_name, "equal_weight")
        self.assertTrue(callable(strategy.decide))
        self.assertFalse((repository_root / "fundbacktesting" / "strategies").exists())

    def test_entrypoint_uses_editable_settings_and_results_directory(self):
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
            workbook.save(workbook_path)
            results_path = root / "results"

            with (
                patch.object(backtest_entrypoint, "DATA_FILE", workbook_path),
                patch.object(backtest_entrypoint, "OUTPUT_DIR", results_path),
                patch.object(backtest_entrypoint, "RESULT_NAME", "configured_result"),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                backtest_entrypoint.main()

            run_results_path = results_path / "configured_result"
            self.assertTrue((run_results_path / "configured_result_nav.csv").is_file())
            config = (
                run_results_path / "configured_result_config.json"
            ).read_text(encoding="utf-8")
            self.assertIn("source_sha256", config)


if __name__ == "__main__":
    unittest.main()

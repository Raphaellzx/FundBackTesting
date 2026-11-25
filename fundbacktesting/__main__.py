from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from .contracts import BacktestConfig
from .runner import run_from_files
from .strategy_loader import load_strategy

# 在此修改本次回测参数；日期区间的起止日期均包含在内。
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

# 数据文件夹；可按需改为其他目录。
DATA_DIR = REPOSITORY_ROOT / "data"

# 输入的 Excel 行情文件；选择 data/ 中的数据文件。
DATA_FILE = DATA_DIR / "Data.xlsx"

# 回测输出根目录；每次运行会创建独立子文件夹。
OUTPUT_DIR = REPOSITORY_ROOT / "results"

# 策略文件路径；改为 strategies/ 中其他策略即可切换。
STRATEGY_FILE = REPOSITORY_ROOT / "strategies" / "equal_weight.py"

# 初始资金；必须大于 0。
INITIAL_CAPITAL = 1_000_000.0

# 单边交易费率；取值范围 [0, 1)，例如 0.0005 表示 0.05%。
FEE_RATE = 0.0005

# 调仓频率："none" 仅首日建仓，或 "daily"、"weekly"、"monthly"。
REBALANCE_FREQUENCY = "monthly"

# 回测起始日，格式 date(年, 月, 日)；None 表示使用数据最早日期。
START_DATE: date | None = None

# 回测结束日，格式 date(年, 月, 日)；None 表示使用数据最晚日期。
END_DATE: date | None = None

# 结果文件夹及文件名前缀；None 自动使用策略名和当前时间。
RESULT_NAME: str | None = None

# 年化无风险利率，用于夏普比率；例如 0.02 表示 2%，需大于 -1。
ANNUAL_RISK_FREE_RATE = 0.02

# 年化最低可接受收益率，用于 Sortino/下行偏差/Omega；需大于 -1。
ANNUAL_MAR = 0.0

# 历史模拟 VaR/CVaR 置信度；必须在 (0, 1)，常用 0.95 或 0.99。
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

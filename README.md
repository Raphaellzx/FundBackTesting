# fundbacktesting

`fundbacktesting` 是一套契约驱动的通用基金回测框架。框架只提供回测机制：数据标准化、调仓日调度、目标权重校验、交易与费用执行、净值轨迹记录、绩效计算和审计输出。具体投资规则放在仓库根目录的 `strategies/` 中，通过 `decide(context)` 插槽接入；框架不认识任何具体研究或策略。

## 快速开始

需要 Python 3.10+ 与 `openpyxl`。

```powershell
py -3.13 -m pip install -r requirements.txt
py -3.13 -m fundbacktesting
py -3.13 -m unittest discover -s tests -v
```

入口位于 `fundbacktesting/__main__.py`。默认读取 `data/Data.xlsx`，使用 `strategies/equal_weight.py`，结果写入 `results/<run_id>/`。修改入口顶部的常量可切换数据文件、策略文件、日期区间、资金、费率、调仓频率、无风险利率与风险指标参数。

Excel 输入布局：第 4 行为基金名称，第 5 行为基金代码，第 6 行起为日期和复权单位净值；空白净值表示该基金当日无可用数据，不插值、不回填上市前净值。

## 当前实现

| 模块 | 职责 |
|---|---|
| `fundbacktesting/contracts.py` | 冻结数据类与策略协议：`MarketData`、`FundMeta`、`BacktestConfig`、`BacktestContext`、`BacktestResult`、`EvaluationResult` |
| `fundbacktesting/adapter.py` | Excel 适配器与 CSV 读取器；把外部数据组装为 `MarketData` |
| `fundbacktesting/engine.py` | 交易日循环、调仓调度、目标校验、成交与费用、净值和调仓诊断记录 |
| `fundbacktesting/evaluation.py` | 从结果对象计算绩效；支持可配置的指标计算约定 |
| `fundbacktesting/reference.py` | 外部回归基准比对，只生成 pass/fail 报告，不参与计算 |
| `fundbacktesting/runner.py` | 组装一次运行，写出净值、持仓、交易、费用、指标、配置和运行清单 |
| `fundbacktesting/strategy_loader.py` | 从 Python 文件加载 `create_strategy()` 和 `STRATEGY_NAME` |

依赖方向保持单向：适配层、策略层和评价层依赖契约；引擎只依赖契约，不导入具体数据源、策略或评价指标实现。编排层是唯一同时组装各层的模块。

## 策略插槽

策略文件放在仓库根目录 `strategies/`。每个文件提供 `create_strategy()`，返回带 `decide(context)` 方法的对象；可选定义 `STRATEGY_NAME`。

```python
from fundbacktesting.contracts import BacktestContext

STRATEGY_NAME = "my_strategy"

class MyStrategy:
    def decide(self, context: BacktestContext) -> dict[str, float]:
        # 返回可用基金的目标权重，合计不超过 1；剩余部分为现金
        return {context.active_funds[0]: 1.0}

def create_strategy() -> MyStrategy:
    return MyStrategy()
```

策略决定“目标是什么”，引擎决定“如何成交、花多少费用、如何更新持仓与现金”。策略可以持有跨期内部状态，但不得直接修改引擎状态；组合状态、交易记录和成本记录由引擎维护。

`strategies/equal_weight.py`、`strategies/portfolio1_four_long_bonds.py` 和 `strategies/portfolio2_four_long_short_cash.py` 是简单示例。`strategies/study_v17.py` 是一个用例级普通策略：它把一套具体研究规则封装在自己的 `decide()` 中，框架仅把它当作返回目标权重的策略对象。

## 通用回测机制

### 调仓计划

`BacktestConfig.rebalance_frequency` 支持 `none`、`daily`、`weekly`、`monthly`。`none` 只在回测首日建仓一次，后续持仓自然漂移。

需要非频率型日期序列时使用 `rebalance_dates: tuple[date, ...]`。显式日期优先于频率设置，并与回测区间取交集；交集为空会报错。

`decision_lag` 用于分离可见数据日与成交日：

- `decision_lag=0`：`context.current_date` 和 `context.decision_date` 都是成交日；
- `decision_lag>0`：`context.current_date` 向前移动 lag 个交易日，`context.decision_date` 仍是成交日。

`context.history` 保留旧语义，包含截至 `current_date` 的数据；需要严格时点数据时使用 `context.history_before_current`，它只包含 `current_date` 之前的数据。

### 时点数据与可选上下文

`MarketData` 除日期、净值和名称外，还可携带：

- `risk_free_rates: Mapping[date, float]`：逐日无风险利率；
- `fund_meta: Mapping[str, FundMeta]`：产品类型、成立日、准入起始日等可选属性；
- `source_fingerprints: Mapping[str, str]`：多份输入的哈希指纹。

框架不解释这些字段，只把它们传给策略或评价层。CSV 读取器：

```python
from fundbacktesting.adapter import (
    load_nav_csv,
    load_risk_free_csv,
    load_instrument_meta_csv,
    with_market_context,
)
```

`load_nav_csv()` 读取宽表净值，缺失值跳过；`load_risk_free_csv()` 默认把百分数转为小数；`load_instrument_meta_csv()` 读取可选元数据；`with_market_context()` 返回附加上下文的新 `MarketData`，不修改原对象。

### 初始建仓成本

`charge_initial_cost=True`（默认）保持原行为。`charge_initial_cost=False` 时，首个再平衡日仍建仓并记录交易，但不记费用成本，也不把首日建仓计入换手诊断；后续调仓正常计费。

### 绩效计算约定

`evaluate(result, convention=None)` 返回 `EvaluationResult`。未显式指定时使用 `result.config_snapshot["metric_convention"]`；可用约定由 `available_conventions()` 查询。

- `framework`：月末净值计算月收益；按月复合年化；月收益样本标准差乘 √12；固定年化无风险利率；回撤自 1.0 起算；双边成交额换手；含 Sortino、Omega、历史 VaR/CVaR。
- `daily_365`：365 自然日年化；日收益样本标准差乘 √252；逐日无风险利率，缺失日按 0.016；回撤自区间首日起算；基于调仓前后权重差的单边/双边换手；输出 `rebalance_count_sum`。

`EvaluationResult.as_dict()` 提供旧调用方需要的指标字典视图，同时保留 `convention`、`units` 与 `convention_notes`，避免不同口径的指标被混用。

### 回归基准与运行清单

`reference.py` 接收外置基准文件和计算结果，返回逐项 pass/fail。基准只用于验收，不进入数据、策略、引擎或评价计算。

`run_from_files()` 每次写出 `{run_id}_nav.csv`、`{run_id}_holdings.csv`、`{run_id}_trades.csv`、`{run_id}_costs.csv`、`{run_id}_metrics.json`、`{run_id}_config.json` 和 `{run_id}_manifest.json`，并更新 `results/metrics_summary.csv`。运行清单记录代码版本、Python 与平台、指标约定、输入指纹、源文件哈希和配置快照。

## 用例与回归测试

`cases/study_v17/data.py` 把三份外部 CSV 组装成框架通用的 `MarketData`。`strategies/study_v17.py` 是普通策略实现，具体规则只存在于该策略中。

`tests/test_study_v17_reference.py` 在 `data/` 下三份输入 CSV 与 `tests/reference/study-baseline.json` 存在时运行：它用外置基准检查输入哈希、17 期逐期持仓和七项主指标。该测试是验收层，不会改变框架计算；缺少输入或基准时自动跳过。原研究包目录已删除，逐期结果与参数已固化在基准文件中。

`tests/test_generic_backtest.py` 覆盖显式再平衡日、时点数据、决策滞后、初始成本开关、两套绩效约定、基准比对、CSV 适配器和运行清单。

## 边界与不做事项

- 不新增插件系统；策略插槽沿用 `create_strategy()` 文件加载约定。
- 不把具体研究的规则、参数或专有名词写入框架核心。
- 不模拟基金申赎确认日差；`decision_lag` 只控制可见数据日与成交日的关系。
- 不提供基准组合、信息比率、跟踪误差、业绩归因或实盘下单。
- 不自动消除候选集合的存续偏差；样本内参数和结果必须显式标注。

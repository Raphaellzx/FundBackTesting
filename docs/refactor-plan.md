# fundbacktesting 通用化改造方案

## 1. 目标与边界

把 `fundbacktesting` 改造成一套**通用基金回测机制**：框架本身不认识任何具体研究，只提供可组合的机制；现有“基金回测”研究作为该框架上的一个**普通策略实现**运行，通过已有的 `create_strategy()` 插槽接入，不新增插件机制、不内置专用工作流。

**在范围内**

- 四项通用能力增强：数据时点可见性、显式再平衡计划、初始建仓成本开关、绩效计算约定可配置。
- 契约、引擎、适配器、运行器、评价层的向后兼容扩展。
- 一份研究策略实现示例，证明通用机制能完成原研究的测试。
- 回归基准比对、运行清单、打包声明与测试补齐。

**不在范围内**

- 把原研究的领域规则写进框架核心；框架代码与文档中不出现任何具体研究的专有名词。
- 新增插件系统或扩展点注册机制；策略插槽沿用现有 `create_strategy()` 约定。
- 基准比较、业绩归因、实盘下单、实时行情、图形界面。
- 消除候选集合在单一时点导出带来的存续偏差；这不是回测机制能解决的问题。

## 2. 术语约定

框架层面统一使用下列术语，不使用来源项目的内部表述。

| 框架术语 | 含义 |
|---|---|
| 时点数据（point-in-time data） | 每个观测值带生效日，策略只能读取决策日此前已可获得的数据 |
| 再平衡计划（rebalancing schedule） | 一组显式再平衡日期，或由频率规则生成 |
| 目标权重（target weights） | 策略在再平衡日给出的基金权重，未分配部分为现金 |
| 交易成本模型 | 买入费率、卖出费率、是否对初始建仓计费 |
| 现金收益率 | 未投资现金按约定利率计息 |
| 交易确认滞后 | 决策日与成交日之间的间隔，默认 0 |
| 绩效计算约定 | 年化基准、波动率缩放因子、无风险利率来源、回撤起算方式、换手率定义 |
| 回归基准 | 一组已确认的参考指标值与容差，用于比对而非参与计算 |
| 运行清单 | 代码版本、配置、输入指纹、运行环境的可审计记录 |
| 前视偏差（look-ahead bias） | 决策时使用了当时尚不可得的数据 |

## 3. 现状评估（基于当前源码）

| 文件 | 现状 | 改造判断 |
|---|---|---|
| `contracts.py` | 冻结数据类；`BacktestConfig` 仅四种频率；`MarketData` 只有净值、名称、单一 sha256；`BacktestContext` 只有日期、含当日的 history、持仓、活跃基金 | 扩展，全部新增字段带默认值 |
| `engine.py` | 单一主函数；首日建仓即计费；成本按买卖双边累计并用迭代求预算；`history` 包含决策日当日 | 三处核心改动 |
| `evaluation.py` | 月频年化、固定年化无风险利率、历史最高点起算回撤、双边换手；返回裸 dict | 参数化计算约定，默认口径不变 |
| `adapter.py` | 只读 Excel 第 4/5/6 行起的固定布局；单一源文件 sha256 | 新增独立读取器，不改现有函数 |
| `runner.py` | 校验 run_id、串联各层、写四份 CSV 与两份 JSON、更新汇总表 | 增加运行清单与约定记录 |
| `strategy_loader.py` | 从 `.py` 加载 `create_strategy()` 与 `STRATEGY_NAME`，失败回滚 `sys.modules` | 不改 |
| `__init__.py` | 导出 5 个公共名字 | 按需导出新增类型 |
| `tests/test_backtest.py` | 13 个行为测试，覆盖调仓、成本、校验、适配与输出 | 全部保留，另增测试 |

工作区存在未提交改动：`fundbacktesting/__main__.py` 已修改、`compare/` 下三个文件已删除、`基金回测/` 未跟踪。改造前先建分支并备份，不覆盖这些改动。

## 4. 四项通用能力增强

### 4.1 数据时点可见性

**问题**：`MarketData` 无逐日无风险利率、无基金属性；`BacktestContext.history` 包含决策日当日，策略若按当日数据决策即产生前视偏差。

**改法**

- `MarketData` 新增可选字段：`risk_free_rates: Mapping[date, float] | None`、`fund_meta: Mapping[str, FundMeta] | None`、`source_fingerprints: Mapping[str, str] | None`。
- 新增 `FundMeta` 冻结数据类：`product_type`、`inception_date`、`eligibility_start`、`name`。均可选。
- `BacktestContext` 新增 `history_before_current`：截至决策日**不含当日**的净值历史；原 `history` 保留含当日语义以兼容。
- 新增 `loads_on_or_before()` 或等价辅助函数，由策略自行决定使用哪一口径。
- 无风险利率缺失时由使用的计算约定显式报错，不静默回退默认值。

### 4.2 显式再平衡计划

**问题**：`rebalance_frequency` 只支持 `none/daily/weekly/monthly`，无法表达非频率型日期序列。

**改法**

- `BacktestConfig` 新增 `rebalance_dates: tuple[date, ...] | None = None`。
- `_rebalance_dates()` 优先使用显式日期（与回测区间取交集），否则按频率生成。
- 校验：显式日期不得与频率同时给出；落在区间外的日期直接丢弃；为空则报错。
- 决策滞后由配置提供，默认 0；不为 0 时，决策使用前一交易日可见数据、成交在决策日执行。

### 4.3 初始建仓成本开关

**问题**：引擎首日建仓即计费，而多数研究把首日建仓视为组合起点、不计成本；不加开关会系统性压低净值。

**改法**

- `BacktestConfig` 新增 `charge_initial_cost: bool = True`，默认保持现有行为。
- 当为 `False` 时，首个再平衡日仍执行交易并建立持仓，但 `estimated_cost` 固定为 0，`cost_log` 不记该日费用。
- 不影响后续再平衡日的成本计算。

### 4.4 绩效计算约定可配置

**问题**：当前评价层固定月频口径，无法表达日频口径的研究结果；两者年化因子、波动率因子、无风险利率来源、回撤起算点、换手率定义均不同，不能混用。

**改法**

- `evaluate(result, convention="framework")` 新增约定参数，默认保持现有 13 个测试口径。
- 约定以枚举或注册表实现，至少提供两套：`framework`（现状）与 `daily_365`（日频口径）。
- `daily_365` 约定：按自然日年化、日收益按 √252 缩放、逐日无风险利率、回撤自区间首日起算、换手率按单边口径。
- 结果由裸 dict 改为 `EvaluationResult` 冻结数据类；为兼容可继续提供 dict 视图，但字段、单位与口径必须可查。
- 换手率字段明确命名：`turnover_two_way` 与 `turnover_one_way`，不再用含糊的 `annual_turnover`。

## 5. 文件级改造清单

| 文件 | 改动 |
|---|---|
| `fundbacktesting/contracts.py` | 新增 `FundMeta`、`EvaluationResult`；扩展 `BacktestConfig`、`MarketData`、`BacktestContext`、约束字段；全部可选带默认值 |
| `fundbacktesting/engine.py` | 改 `_validate_config`、`_rebalance_dates`、`run_backtest` 三处；不通往任何具体研究规则 |
| `fundbacktesting/evaluation.py` | 参数化计算约定；新增日频约定；返回结构化结果并提供兼容视图 |
| `fundbacktesting/adapter.py` | 新增 `load_nav_csv()`、`load_risk_free_csv()`、`load_instrument_meta()`；保留 `load_market_data()` 不变 |
| `fundbacktesting/runner.py` | 写运行清单；记录约定与全部输入指纹；输出文件格式保持 |
| `fundbacktesting/reference.py`（新增） | 回归基准比对：接收指标、基准值与容差，返回逐项 OK/差异；基准外置，不参与计算 |
| `fundbacktesting/__init__.py` | 导出新增公共类型与函数 |
| `strategies/<study>.py`（新增） | 研究策略实现，使用上述通用机制；策略内部规则不进入框架 |
| `pyproject.toml`（新增） | setuptools 打包声明，依赖 openpyxl |
| `tests/` | 保留 13 个原测试；新增显式再平衡日、初始成本、两种约定、适配器、前视偏差与基准比对测试 |
| `README.md`、`.github/copilot-instructions.md` | 拆分为“现状”与“目标”；删除未实现能力的宣称；统一术语 |

## 6. 通用机制如何完成原有测试

原研究要求的能力，逐项由框架通用机制承接，框架不增加专用接口：

| 研究要求 | 使用的通用机制 |
|---|---|
| 决策只使用历史窗口 | `history_before_current` 与时点数据 |
| 逐日无风险利率 | `MarketData.risk_free_rates` 与上下文传递 |
| 非频率型决策日 | `rebalance_dates` 显式计划 |
| 首日建仓不计成本 | `charge_initial_cost=False` |
| 日频绩效指标 | `evaluate(..., convention="daily_365")` |
| 与既有结果核对 | `reference.py` 的回归基准比对 + 外置基准文件 |
| 多份输入可追溯 | `source_fingerprints` 与运行清单 |

上述每一项对任意策略成立。策略实现内部的具体规则不属于框架，不写入框架代码、配置 schema 或文档。

## 7. 兼容性策略

- 所有新增配置字段带默认值，现有构造调用无需修改。
- `evaluate()` 默认约定保持当前数值结果；现有测试断言逐字不改。
- `load_market_data()` 行为不变；新数据由新函数加载后组装 `MarketData`。
- 输出 CSV/JSON 文件名与字段保持，新增内容只增加文件或列。
- `__main__.py` 的用户改动保留，仅追加新配置项。

## 8. 验收标准

1. `python -m unittest discover -s tests` 全部通过，原 13 项断言不变。
2. 新增测试覆盖四项通用能力与回归基准比对。
3. 研究策略实现跑出的六项主指标落入既有容差。
4. 无前视偏差测试通过：决策日修改当日净值不改变该日目标权重。
5. 每次运行生成运行清单，输入指纹与配置可复核。
6. 框架代码与文档中不出现具体研究的专有名词。
7. 全部改动稳定后只调用一次 `aoci maintain`，保持索引对齐。

## 9. 工作量与顺序

按一名熟悉两边代码的 Python 开发者、复用现有算法估算，不含产品化：

| 阶段 | 内容 | 人日 |
|---|---|---|
| 0 | 建分支、备份、冻结回归基准 | 0.5 |
| 1 | 契约扩展与四项通用能力 | 4–6 |
| 2 | 适配器与运行清单 | 2–3 |
| 3 | 评价层约定与结构化结果 | 2–3 |
| 4 | 研究策略实现与基准比对 | 3–5 |
| 5 | 测试补齐与文档治理 | 2–3 |

合计约 **13.5–20.5 人日**。顺序：阶段 0 → 1 → 2 → 3 → 4 → 5；每阶段结束跑一次全量测试，阶段 4 完成后做指标对齐。

## 10. 明确不做的事

- 不把任何具体研究的规则、参数或专有名词写入框架。
- 不新增插件机制或动态注册系统。
- 不提供基准组合、信息比率、跟踪误差与业绩归因。
- 不模拟基金申赎确认日差；交易确认滞后仅提供参数位，默认 0。
- 不自动消除候选集合的存续偏差；样本内参数寻优结果必须标注为样本内。
- 不在改动进行期间反复维护 AOCI 索引。

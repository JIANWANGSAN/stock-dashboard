# 第三方代码归属

本目录下的**回测引擎**移植自 [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading)，
MIT 协议，`Copyright (c) 2026 Vibe-Trading Contributors`。协议全文见 `LICENSE.upstream`。

移植基线：上游 `main @ 002373ef32800d4f7d70b418eb4e3bf9d030a5bc`（2026-08-26）的 `agent/backtest/`。

## 上游来源文件（下表是归属清单，不代表当前逻辑原样未改）

| 文件 | 内容 |
|---|---|
| `models.py` | 持仓 / 成交 / 交易 / 权益的不可变数据类 |
| `constraints.py` | 组合约束 |
| `metrics.py` | 指标（夏普 / 卡玛 / 索提诺 / 最大回撤 / 胜率 / 盈亏比 / 换手 / 相对基准） |
| `validation.py` `rebalance_notes.py` `risk_xray.py` `run_card.py` | 校验、调仓说明、风险 X 光、run card |
| `engines/base.py` | 逐 bar 撮合内核 |
| `engines/china_a.py` | A股规则：T+1 / 涨跌停 / 100 股整手 / 印花税 |
| `engines/global_equity.py` | 美股与港股规则；加拿大分支保留，产品闸口未开放 |
| `engines/_market_hooks.py` | 代码 → 市场分类、币种 |

## 初次迁入的依赖调整（历史记录）

1. **`engines/base.py`** — 事件（RSSHub）与基本面（Tushare）两路增强改成**惰性 import**。
   这两路没有随本产品发行（前者要自建服务、后者要付费 key，而本产品的数据一律走自己的取数层）。
   `config` 没点名就永远不 import；点名了就报清楚的错，**不静默跳过**。
2. **`benchmark.py`** — 删掉 yfinance 兜底（同上），并让原本静默的取数失败出声。

## 本产品新写的（不属于上游）

| 文件 | 内容 |
|---|---|
| `loader.py` | 把本产品的取数端点接到引擎上 —— 整个移植里唯一的新数据代码 |
| `gate.py` | 回测闸口：判要回测什么 → 需要什么 → 限制是什么 |
| `run.py` | 接线 + 运行期守卫 + 结果呈现 |
| `strategies.py` | 内置策略（也是"策略长什么样"的模板） |
| `tests/` | 回归测试；数量以各阶段实际测试记录为准 |

## 没有搬的

`loaders/`（25+ 数据源）· `optimizers/`（组合优化）· 期货 / 外汇 / 加密 / 印度 / 韩国 / 越南 /
期权 / composite 引擎 · `regime.py` `correlation.py` `factor_costs.py` 等。
数据一律走本产品自己的取数层；其余引擎等对应市场接进来时再说。

## Vibe AStock 迁入适配（2026-09-08）

本目录从 Vibe-Research 的已验证版本迁入 AStock。撮合、指标及市场规则沿用原实现。
AStock 将 loader 的 FETCH_SCRIPT 指向本仓库 research_data/fetch_backtest.py，保留原始日线落盘、标的核对及复权口径。
交互和报告归档由 review_agent/backtesting.py 与 page-chats 的既有持久任务处理。
Research 专用 datasources.health 的测试不在本产品范围，未带入；其余引擎回归保留。

## 2026-09-09 本地维护边界

后继已修改上游来源文件的算法与边界处理：约束优化失败拒绝、日期索引对齐、期末成交限制、零波动指标、成交费用与持仓统计、买入持有调仓方式，以及港股执行规则选择。不能继续称“仅改依赖、不动算法”。变化说明见本仓库 CHANGELOG.md，回归测试位于本目录 tests/；原 MIT 归属和许可证保留。

网页只开放三种日线规则及 A/H/US 市场。benchmark、validation、其它市场钩子保留供上游对照，当前网页不开放指数基准、Monte Carlo 验证、组合优化或加拿大等其它市场，不把存在的源码等同已接通功能。旧港股报告保留原计算结果，界面提示按原条件重跑；不得用旧费用模型的结果为新规则背书。

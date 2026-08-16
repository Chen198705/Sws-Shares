# 沈万三 A股 AI 量化交易系统 · Agent 交接说明

> 版本：2026-08-16（v0.3 收尾状态）
> 用途：给其他 Agent 快速建立项目上下文。所有结论均以仓库当前代码与 Studio 运行状态为准，
> 文档描述会过时，动手前先读代码和 `verify_landing.py` 的落地断言。

## 1. 项目一句话

在 Mac Studio 上运行一套 A 股量化交易模拟系统「沈万三」：
研究层用统计学方法验证因子，信号层用技术指标 + 规则 + oMLX 大模型合成买卖信号，
执行层走模拟券商并做 T+1、止损止盈、仓位和行业风控，全链路通过只读契约衔接并留痕归因。

## 2. 系统架构（三层闭环）

```
研究层 Research ──只读契约──> 信号层 Signal ──订单──> 执行层 Execution
     ▲                                                       │
     └────────── 成交/归因/参数效果反馈（迭代闭环）────────────┘
```

- 研究层：`research/`，负责数据管线、因子库、回测、regime、拥挤度、实验留痕；
  产出 `strategy_params.json` 等契约，不下单、不实时盯盘。
- 信号层：`trading_bot.py` 主循环 + `market_scanner.py`（全市场打分）+
  `analyzer.py` / `rule_engine.py`（规则兜底）+ `ai_client.py`（oMLX 大模型）。
- 执行层：`broker_adapter.py` + `simulation_broker.py`（默认模拟），
  `server.py` 提供 Web UI + REST API（端口 5168），`stock_report.py` 发飞书盘后汇报。

## 3. 当前运行环境

- 主机：Mac Studio，路径 `/Users/chenjianhui/AI/Sws-Shares`（运行目录，非 git 仓库）；
  本机同步副本 `/Users/chenjianhui/AI/Sws-Shares`（git 仓库）。
- Git 仓库：`github.com/Chen198705/Sws-Shares`。
- 三个 LaunchAgent（`~/Library/LaunchAgents/`，仓库副本在 `docker/`）：
  - `com.shenwansan.api`：uvicorn `server:app`，监听 `0.0.0.0:5168`，常驻。
  - `com.shenwansan.trading`：`trading_bot.py` 常驻主循环。
  - `com.shenwansan.research`：工作日 15:35 触发 `research/daily_refresh.py`，非常驻。
- oMLX 模型服务：`http://127.0.0.1:8000`（OpenAI 兼容 `/v1`），API Key 在 `.env`，
  不在代码/文档中明文保存。
- 端口约定：系统只用 5168；5173/5174/8501 属于历史废弃端口，禁止再启用；
  oMLX 是 8000；Cloudflare 隧道配置不要改动。
- 券商：`BROKER_MODE=simulation`，模拟仓初始资金 100 万，SQLite 持久化；
  真盘（QMT/聚宽/Webhook）接口已预留但未启用。

## 4. 已应用的数学模型 / 算法

### 研究层

| 模型/算法 | 说明 | 状态 |
|---|---|---|
| 因子构造 | 动量（12-1/6-1/1月）、已实现波动率、ATR、换手率、Amihud 非流动性、60 日最大回撤、EP/BP/DP、log 市值、ROE、毛利率、涨停计数 | 16 因子在库 |
| IC 检验 | 每月因子值与未来 1 个月收益的 Spearman 秩相关，逐月序列做 t 检验 | 生产使用 |
| 截面 OLS | 逐月截面回归 + 聚合系数 + Newey-West t | 生产使用 |
| Ridge / LASSO | 闭式解 / 坐标下降，因子选择与稳健性交叉验证 | EXP-007 通过 |
| 事件研究 CAR | 市场模型估计 AR，AAR 聚合 CAR，t = CAR/(std/√n)，BH 多重检验校正 | EXP-011 使用 |
| GARCH(1,1) / GJR | 次日波动率预测，与历史滚动波动率对比 | 已被否决 |
| Regime | 规则法（MA20/MA60 + 波动率阈值）+ 2 状态高斯 HMM（k-means 初始化 + 前向-后向 EM） | 两者并用 |
| 回测引擎 | 月度再平衡、T+1、涨跌停拦截、佣金/印花税/滑点/过户费、NAV | 生产使用 |
| 压力测试 | 2015 股灾 / 2018 慢熊 / 2020 疫情 / 流动性枯竭情景，40% 回撤强制降仓 | 生产使用 |
| 拥挤度监控 | 因子收益夏普、波动、相关性突变 | 每日刷新 |

### 信号层与执行层

- 技术指标：MA5/MA20、RSI、MACD、KDJ、量比、换手率（`market_data.py`）。
- 规则引擎：RSI 超买超卖、MACD 红绿柱、均线排列等规则兜底（`rule_engine.py`）。
- 全市场打分：加权累加 + 研究层因子约束（低换手加分、追涨停减分、PB 低估值加分等）。
- LLM 决策：把行情、指标、regime、研究层因子、止损止盈参数组装 prompt，
  由 oMLX（Qwen）输出方向/仓位/理由，再用规则解析动作与周期。
- 仓位：`min(总资产×目标仓位, 可新增上限, 单票上限)`，向下取整到 100 股。
- 风控：按周期止损止盈、回撤止盈、T+1 可卖量校验、账户熔断、行业集中度 ≤30%。
- 归因/迭代：买入写归因，卖出 FIFO 关闭归因；满 5 笔平仓触发 LLM 复盘并写回参数。

## 5. 操盘工作流（交易日）

1. 每 5 分钟检查：非交易日休眠到下一交易日 09:30；非交易时段休眠。
2. 检查持仓：按周期触发止损/止盈/回撤止盈，卖出走 T+1 校验。
3. 检查账户熔断：总资产回撤 -20% 暂停 30 天，-30% 暂停 90 天。
4. 每 30 分钟全市场扫描，输出推荐 TOP10。
5. 对未持仓候选逐只分析：实时行情 + 60 日 K 线 + 指标 + regime 周期分配，
   调用 oMLX 给出方向与仓位，执行买入/卖出。
6. 11:30 / 15:05 生成盘后报告推飞书；15:35 研究层日更。

## 6. 当前契约与关键参数（2026-08-16.1）

- 契约文件：`research/export/strategy_params.json`，只读注入信号层。
- confidence：L1；regime：震荡市（规则法与 HMM 一致）。
- factor_constraints：16 项；`value_bp` weight=0.10、status=L1 通过
  （EXP-009 OOS IC t=2.85、Newey-West t=2.04；EXP-010 行业内 t=5.09）。
- policy_factors：6 项；`policy_industry_plan_car5` 为 L1 候选但 weight=0，
  启用需 Claude 决策；`policy_rrr_cut_car5`、`policy_lpr_cut_car5` 已拒入。
- risk_limits（当前震荡市生效）：max_position_pct=0.7、single_stock_pct=0.05、min_cash_pct=0.3。
- horizon_weights：short=0.5、medium=0.3、long=0.2（信号层另有按 regime 轮转分配）。
- 止损止盈：短线 -3%/+8%、中线 -5%/+15%、长线 -10%/+25%；
  回撤止盈：短线 +5% 启动/3% 落袋，中线 +6% 启动/3% 落袋，长线不启用。
- 模型：API 端 `OLLAMA_MODEL`（当前 Qwen3.5-9B-MLX-4bit）；
  机器人端 `stock-ai/api/bot_config.json`（当前 Qwen3.6-35B-A3B-4bit）。
  Dashboard 切换模型只影响前端问答，不同步机器人模型。

## 7. 已完成实验与结论（EXP-001 ~ EXP-012）

| 实验 | 结论 |
|---|---|
| EXP-001~003 | 动量不成立；低波动/低换手/低回撤有效；追涨停样本外负 alpha |
| EXP-004 | GARCH 不优于历史波动率（H6 否决） |
| EXP-005 / 007 | OLS、Ridge、LASSO 与 rank IC 交叉验证一致 |
| EXP-008 | 行业中性化降低样本外 R²（H4 否决，保留原始因子） |
| EXP-009 | value_bp L1 通过；EP/质量不显著；规模边际负向 |
| EXP-010 | value_bp 分层复验通过（行业/市值全显著，无 IC 衰减，容量 1 亿无损） |
| EXP-011 | 政策事件 CAR 研究；产业规划为唯一 L1 候选（方向一致 80%、BH 显著率 80%） |
| EXP-012 | 免费源无历史月度一致预期，覆盖度 12.6% 未达标，R6 搁置 |

## 8. 关键文件地图

- 研究层：`research/daily_refresh.py`、`research/verify_landing.py`、
  `research/export/`（契约）、`research/regime/`、`research/models/`、
  `research/backtest/engine.py`、`research/monitor/crowding.py`、
  `research/robustness/stress_test.py`、`research/attribution/aggregate.py`。
- 信号/执行层：`stock-ai/api/trading_bot.py`、`server.py`、`market_scanner.py`、
  `analyzer.py`、`rule_engine.py`、`ai_client.py`、`strategy_store.py`、
  `iteration_engine.py`、`broker_adapter.py`、`simulation_broker.py`、
  `stock_report.py`、`bot_config.json`、`.env`（不提交）。
- 前端：`stock-ai/front/`（React/Vite，构建产物 `dist/` 被 gitignore）。
- 部署：`docker/`（Dockerfile、LaunchAgent plist、`docker-compose.yml`）、
  `docs/ARCHITECTURE.md`、`docs/PROJECT_BRIEFING.md`。
- Claude 规范：`/Users/chenjianhui/Claude/Projects/Stocks/`
  （TASK / RESEARCH / PHILOSOPHY / POLICY_EVENTS / DECISIONS / ITERATION 等）。

## 9. 纪律红线（不要违反）

- 永不杠杆；不用 LSTM/Transformer/强化学习。
- 舆情、新闻、论坛、大 V、雪球关注度等外信息禁入；政策事件是唯一外信息维度，
  必须走 CAR 事件研究 + BH 校正入模。
- 实验先预注册再跑，禁止事后补事件凑显著；`policy_events.csv` v1 冻结，
  修正只能新建 v2。
- 研究层不连券商、不下单；跨层只走只读契约。
- 回测必须处理涨跌停、T+1、真实成本，禁止偷看测试集。
- 端口只用 5168；不要启 5173/5174/8501；不要改 Cloudflare 配置。
- 当前只有模拟盘，真盘尚未接入，不要伪造真实交易结论。

## 10. 已知缺口与建议

- 交易日历只有“周一至周五”判断，没有法定节假日/调休表；
  节假日休市日机器人可能仍会扫描，建议补交易所日历。
- 政策因子 `policy_industry_plan_car5` 权重 0，待 Claude 决策是否启用。
- R6 分析师预期因子搁置（免费源无历史月度一致预期）。
- Docker Compose 只有 api + bot；research 依赖主机数据缓存，用 LaunchAgent 部署。
- 前端切换模型不影响机器人模型（有意为之）。

## 11. 验证与诊断命令

```bash
# 三层落地校验（在仓库根目录）
PYTHONPATH=stock-ai/api/.venv/lib/python3.9/site-packages:$PWD \
  stock-ai/api/.venv/bin/python research/verify_landing.py

# 健康与状态
curl -s http://127.0.0.1:5168/api/health
curl -s http://127.0.0.1:5168/api/research/status
curl -s http://127.0.0.1:5168/api/portfolio

# 手动触发研究层日更
launchctl kickstart -k gui/$(id -u)/com.shenwansan.research

# 服务状态
launchctl print gui/$(id -u)/com.shenwansan.api
launchctl print gui/$(id -u)/com.shenwansan.trading
launchctl print gui/$(id -u)/com.shenwansan.research
```

日志位置：`stock-ai/api/logs/shenwansan_{api,trading}.log`、
`research/logs/daily_refresh.log`、`research/logs/launchd_daily_refresh.log`。

## 12. 维护注意事项

- Studio 是运行目录，不是 git 仓库；本机是 git 仓库。改代码先在本地改、编译，
  再同步到 Studio，最后推 GitHub。
- 密钥（oMLX API Key、JQData、飞书 Webhook、SSH）都在 `.env` / 用户环境，
  不要在代码、文档、GitHub 里明文保存。
- 新增实验/因子必须同步更新 Claude 规范目录（TASK/RESEARCH/ITERATION/DECISIONS），
  并补 `verify_landing.py` 断言。

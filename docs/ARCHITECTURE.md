# 沈万三 · 三层量化架构（研究层 → 信号层 → 执行层）

> 版本：v0.2（设计稿）
> 目标：把 Claude 研究规范（`/Users/chenjianhui/Claude/Projects/Stocks/`）与当前运行的
> Sws-Shares 系统（API 5168 / trading_bot / 券商适配）统一成一套可演进、可审计的架构。

## 1. 架构总览

```
┌──────────────────────────── 研究层 Research ───────────────────────────┐
│ Claude 规范: PHILOSOPHY / BACKTEST / FEATURE_LIBRARY / KNOWLEDGE_BASE │
│ 未来落地: research/ 数据管线、因子库、回测引擎、regime、实验日志       │
│ 产出: 因子有效性报告(L0-L4)、RegimeState、策略参数建议                 │
└─────────────────────────────────┬──────────────────────────────────────┘
                                  │ 因子约束 + regime + 参数（只读）
                                  ▼
┌──────────────────────────── 信号层 Signal ─────────────────────────────┐
│ trading_bot（主循环）→ market_scanner（全市场扫描）→ analyzer          │
│ → trader（信号生成）→ rule_engine（规则兜底）→ ai_client（oMLX LLM）    │
│ 反馈: strategy_store（参数） / iteration_engine（闭环迭代）            │
│ 产出: SignalDecision（动作/周期/置信度/止损止盈/理由）                  │
└─────────────────────────────────┬──────────────────────────────────────┘
                                  │ 订单（只走受信执行通道）
                                  ▼
┌──────────────────────────── 执行层 Execution ──────────────────────────┐
│ broker_adapter → simulation_broker / joinquant / qmt / webhook        │
│ server(API 5168) + front(Dashboard) → 下单/持仓/订单/对账             │
│ scheduler → 盘后报告（飞书）                                          │
│ 产出: OrderResult / Position / AccountBalance / 已实现盈亏             │
└─────────────────────────────────┬──────────────────────────────────────┘
                                  │ 成交记录 + 归因 + 参数效果
                                  ▼
                       研究层知识库/失败库/迭代引擎（闭环）
```

## 2. 分层职责与边界

| 层 | 职责 | 绝对不做 |
|---|---|---|
| 研究层 | 发现/验证因子、回测、regime、知识库、实验留痕 | 不下单、不实时盯盘、不把 L0-L2 当"已知有效" |
| 信号层 | 把研究层结论 + 实时行情 + LLM 判断合成买卖信号 | 不自己发明因子有效性、不绕过研究层验证 |
| 执行层 | 订单路由、券商适配、T+1/涨跌停/成本、仓位风控、对账报告 | 不做主观判断、不承担策略决策 |

## 2.1 现有模块 → 层映射

| 现有模块 | 所属层 | 说明 |
|---|---|---|
| `stock-ai/api/market_scanner.py` | 信号层 | 全市场量价/资金流扫描打分，输出候选 TOP10 |
| `stock-ai/api/analyzer.py` / `rule_engine.py` | 信号层 | 行情指标 + 规则引擎，作为 LLM 的兜底与修正 |
| `stock-ai/api/ai_client.py` | 信号层 | oMLX LLM 客户端，负责最终分析与信号抽取 |
| `stock-ai/api/trading_bot.py` | 信号层 | 主循环：持仓检查 / 全市场扫描 / 候选分析 / 决策执行 |
| `stock-ai/api/trader.py` | 信号层→执行层 | 把 AI 文本解析成结构化订单信号 |
| `stock-ai/api/strategy_store.py` | 信号层 | 参数存储与归因表，研究层只读注入点 |
| `stock-ai/api/iteration_engine.py` | 信号层→研究层 | 平仓复盘闭环，写回止损止盈参数 |
| `stock-ai/api/broker_adapter.py` + 各 broker | 执行层 | 券商路由：simulation / joinquant / qmt / webhook |
| `stock-ai/api/server.py` + `front/` | 执行层 | Web UI / REST API（5168），持仓订单对账展示 |
| `stock-ai/api/scheduler.py` | 执行层 | 定时盘后报告（飞书） |

## 3. 跨层数据契约（接口定义）

### 3.1 研究层 → 信号层（只读注入）

```jsonc
// strategy_params / factor_constraints（建议由 research 模块生成，信号层只读）
{
  "confidence_level": "L2",              // L0-L4
  "regime": {"state": "震荡", "vol": "高"},
  "factor_constraints": [
    {"id": "mom_6_1", "valid": true, "weight": 0.3},
    {"id": "vol_60d_realized", "valid": true, "weight": 0.2}
  ],
  "risk_limits": {
    "max_position_pct": 0.70,
    "single_stock_pct": 0.05,
    "min_cash_pct": 0.30
  }
}
```

### 3.2 信号层 → 执行层（订单）

```jsonc
{
  "code": "603052",
  "action": "buy",                       // buy / sell / hold
  "horizon": "short",                    // short / medium / long
  "confidence": 65,
  "stop_loss": 55.5,
  "take_profit": 60.0,
  "reason": "...",
  "source": "trading_bot"
}
```

### 3.3 执行层 → 研究层（归因/反馈）

```jsonc
{
  "trade_id": 123,
  "code": "603052",
  "direction": "sell",
  "pnl": 3560.0,
  "closed_reason": "止盈",
  "horizon": "short",
  "entry_at": "...",
  "exit_at": "...",
  "factor_snapshot": {"mom_6_1": 0.8, "vol_60d_realized": 0.22}
}
```

## 4. 反馈回路（闭环是灵魂）

1. **执行→研究归因**：每笔平仓写入 `trading_log` 的归因字段，研究层按月聚合：赚钱来自因子贡献还是 LLM 判断？
2. **迭代引擎**：`iteration_engine` 按已平仓交易复盘，把短线/中线/长线止损止盈写回 `strategy_store`（当前已实现）。
3. **因子失效监控**：KNOWLEDGE_BASE 每 6 个月过期标记；regime 切换后 1 个月内强制复评。
4. **失败库**：任何"我以为有效但失效"的因子进入 `FAILURES.md`，不删除、不洗白。

## 5. 验证门禁（从假设到实盘的关卡）

| 关卡 | 通过条件 | 对应规范 |
|---|---|---|
| G0 假设 | 有明确可证伪条件 + 预注册 | RESEARCH.md / EXPERIMENTS.md |
| G1 样本内 | 跑赢 BL1-BL4 + 训练集指标 | BACKTEST.md |
| G2 样本外 | 一次性测试集 + 子周期稳健 + 参数敏感性 | BACKTEST.md |
| G3 实盘门 | L2/L3 + 仓位上限 + 压力测试 | RISK.md |

**现状**：信号层已越过 G3 运行（模拟盘）；研究层已完成 P0 管线验证与
全市场 L1 复验（EXP-001~003），模型库加入 GARCH（EXP-004，不采用）与
截面 OLS 审计（EXP-005，与 rank IC 交叉一致），压力测试纳入月度门禁。
所有结论都诚实登记到 `research/experiments/` 三件套。任何"新结论"仍必须从 G0 重新走。

## 6. 当前差距与实施路线

### P0（研究层启动，不改现有信号层）
- ✅ 搭 `research/` 数据管线：AKShare 全 A 日线 + 质量校验
- ✅ 跑 BL1-BL4（随机游走/历史均值/等权/简单动量）
- ✅ 落地第一个因子 `mom_6_1` 的 EXP 实验记录（EXP-20260815-001）
- ⏳ 全市场 + 前复权重跑（EXP-20260815-002）

### P1（研究层→信号层注入）
- ✅ 因子库 16 个（动量/低波动/流动性/价值/规模/质量/涨停计数/60d 回撤）
- ✅ Regime 规则法 + HMM 标记 2014-2024
- ✅ 生成 `strategy_params` 注入 trading_bot（保持参数可回滚）
- ✅ 执行层风控：单票 ≤5%、账户回撤熔断（-20% 暂停 30 天 / -30% 暂停 90 天）、研究层因子约束
- ✅ 行业集中度：单一行业持仓 ≤ 总仓位 30%（`industry_map.py` + 东财业绩报表行业快照）
- ✅ 平仓归因聚合：FIFO 对账口径已修通（`reconcile_closed_trades`），历史卖单进入归因统计

### P2（归因闭环）
- ✅ 平仓归因聚合：因子贡献 vs LLM 贡献（统计口径已落地）
- ✅ 因子拥挤度监控（`research/monitor/crowding.py`）
- ✅ 研究层结论自动进入 dashboard（`/api/research/status` 只读展示）

## 7. 红线

- 不推倒现有运行系统；架构演进保持 API/端口/LaunchAgent 不变
- 研究层不直接连券商，不写入订单
- 任何跨层结论必须带置信度等级
- 杠杆永久禁止（CLAUDE 规范 D-003）

## 8. 研究层目标目录蓝图（P0 落地时创建）

```
research/
├── data/
│   ├── raw/          # 原始数据（只读）
│   ├── clean/        # 清洗后数据
│   └── meta/         # 快照版本、字段说明
├── factors/          # 因子定义与计算（mom_6_1 等）
├── backtest/         # engine / data_loader / metrics / report
├── regime/           # 规则法 + HMM 识别
├── experiments/      # EXP-YYYYMMDD-NNN 实验目录
├── configs/          # 可复现的 config.yaml
└── knowledge/        # 因子报告导出（对接 KNOWLEDGE_BASE）
```

## 8.1 落地状态（2026-08-15）

- 代码仓库：GitHub `Chen198705/Sws-Shares`，MacBook 与 Studio 双侧同步
- Studio 路径：`/Users/chenjianhui/AI/Sws-Shares/research/`
- Studio 运行环境：`/Users/chenjianhui/AI/Sws-Shares/stock-ai/api/.venv/bin/python3`
  （已补 `pyyaml`、`scipy`）
- 数据缓存：`research/data/cache/`（已同步到 Studio，git 忽略）
- 首次正式实验：`EXP-20260815-001`，结果存于
  `research/experiments/EXP-20260815-001/results/`

## 8.2 落地状态（2026-08-16）

- 模型库：`research/models/garch.py`（GARCH/GJR，H6 不采用）、
  `research/models/ols_factor.py`（逐月截面 OLS + Newey-West t）、
  `research/models/regularized.py`（Ridge 闭式解 + LASSO 坐标下降，M2）
- 因子库：新增 Tier1 价值/规模/质量 6 因子（`value_ep` / `value_bp` / `value_dp` /
  `size_logcap` / `quality_roe` / `quality_gross_margin`），共 16 因子
- 基本面管线：`research/data/fundamental.py` 生成全市场快照
  （5543 只，估值 + 业绩 + 行业映射），行业映射落到 `stock-ai/api/data/industry_map.json`
- 历史基本面管线：`research/data/fundamental_history.py` 断点续传
  `stock_value_em`（2018 至今 PE(TTM)/PB/市值）与
  `stock_financial_analysis_indicator`（季度 ROE/毛利率，按披露时限后移防前视），
  支持多线程拉取；`value_dp` 因免费源无历史股息率保持 L0
- 行业中性化：`research/factors/neutralize.py` 逐截面日有效子集重建行业哑变量并
  剔除零方差列，修复秩亏溢出；`research/tests/test_neutralize.py` 合成测试通过
- 回测铁律：买入以 entry 开盘价判涨跌停拦截；卖出跌停开盘顺延至多 10 个交易日；
  成本含最低佣金 5 元、印花税、滑点、过户费；修复首期 NAV 吞收益 bug
- 实验：EXP-20260816-008（行业中性化审计，H4）、EXP-20260816-009
  （历史估值/质量因子 L1 截面审计），结果登记于 `research/experiments/`
- 稳健性：`research/robustness/stress_test.py` 已支持自动读取模拟账户，
  真实持仓 68.1% 敞口下四类压力测试最大回撤 -20.0%（2015 股灾口径），
  未触发 40% 强制降仓，输出 `robustness/scenarios/latest_stress_test.json`
- 实验：EXP-20260815-004（GARCH，不通过）、EXP-20260815-005（OLS，交叉验证通过）、
  EXP-20260816-006（Tier1 因子 L0 截面审计）、EXP-20260816-007（Ridge/LASSO，交叉验证通过）
- 执行层：`strategy_store.py` 账户峰值/熔断 + 研究层风险上限映射
  （`max_total_position` 按 regime 自适应：牛 100% / 震荡 70% / 熊 50% / 不明 40%），
  `trading_bot.py` 单票 5% 上限、回撤熔断、行业集中度 ≤30% 买入拦截，
  `market_scanner.py` 消费研究层因子约束
- 运行环境：Studio venv 已补 `arch`、`statsmodels`；API/trading_bot 已重启生效

## 9. 演进原则

1. 研究层先跑通 BL1-BL4 和 `mom_6_1`，再谈注入信号层。
2. 研究层输出只读、带版本、可回滚；信号层缺省参数行为不因研究层故障改变。
3. 每跨一层必须有记录（DECISIONS.md / EXPERIMENTS.md），不口头约定。
4. 架构文档随实现同步更新，README 只放入口。

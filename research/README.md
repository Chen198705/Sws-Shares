# 研究层 Research

研究层独立于沈万三信号/执行层运行，只做数据、因子、回测与实验留痕，不下单。
与信号层的唯一契约是 `research/export/strategy_params.json`（只读注入）。

## 运行

```bash
cd research
pip install -r requirements.txt
python3 run_experiment.py --config configs/EXP-20260815-001.yaml
```

每日收盘后自动刷新（regime + 拥挤度 + 契约 + 归因）：

```bash
cd research
PYTHONPATH=/Users/chenjianhui/AI/Sws-Shares/stock-ai/api/.venv/lib/python3.9/site-packages:/Users/chenjianhui/AI/Sws-Shares \
  /Users/chenjianhui/AI/Sws-Shares/stock-ai/api/bin/shenwansan-research daily_refresh.py
```

全市场前复权日线首次拉取（断点续传，约 5000+ 只，1-2 小时）：

```bash
cd research
python3 data/fetch_full_market.py --workers 6
```

烟雾测试：

```bash
python3 run_experiment.py --config configs/_smoke.yaml
```

## 目录

| 模块 | 说明 |
|---|---|
| `data/` | AKShare 数据管线（东财+新浪兜底）、全市场 qfq 拉取、本地缓存、质量校验、universe |
| `factors/` | 10 个因子：动量/波动率/流动性/A 股特色（涨停计数、60d 最大回撤） |
| `backtest/` | 月度再平衡、T+1、涨跌停不可成交、交易成本、绩效/IC 指标 |
| `baselines/` | BL1 随机游走 / BL2 历史均值 / BL3 等权 / BL4 简单动量 |
| `regime/` | 规则法 + 2 状态高斯 HMM 市场状态识别，输出历史 regime |
| `monitor/` | 因子拥挤度监控（夏普衰减/波动放大/收益转负） |
| `attribution/` | 从 trading_log.db 只读聚合平仓归因 |
| `robustness/` | 参数敏感性检验（top_quantile / min_listed_days ±20%） |
| `daily_refresh.py` | 收盘后一键刷新：regime → crowding → 契约 → 归因 |
| `configs/` | 实验配置 |
| `experiments/` | 实验报告与记录（`metrics.json` / `report.md` / 净值） |

## 已知限制

- EXP-001/002 用不复权日线（已知限制）；EXP-003 起已切换到全市场前复权 qfq。
- AKShare 东财接口偶发 `RemoteDisconnected`，已自动切换新浪接口；仍失败的股票写入 `data/cache/_fetch_failures.json` 并跳过。
- 研究层结论只进入信号层的 `strategy_params`，不直接驱动下单。

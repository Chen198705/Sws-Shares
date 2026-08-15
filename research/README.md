# 研究层 Research

研究层独立于沈万三信号/执行层运行，只做数据、因子、回测与实验留痕，不下单。

## 运行

```bash
cd research
pip install -r requirements.txt
python3 run_experiment.py --config configs/EXP-20260815-001.yaml
```

烟雾测试：

```bash
python3 run_experiment.py --config configs/_smoke.yaml
```

## 目录

| 模块 | 说明 |
|---|---|
| `data/` | AKShare 数据管线（东财+新浪兜底）、本地缓存、质量校验、universe |
| `factors/` | 动量因子 `mom_12_1` / `mom_6_1` / `mom_1` |
| `backtest/` | 月度再平衡、T+1、涨跌停不可成交、交易成本、绩效/IC 指标 |
| `baselines/` | BL1 随机游走 / BL2 历史均值 / BL3 等权 / BL4 简单动量 |
| `regime/` | 规则法市场状态识别 |
| `configs/` | 实验配置 |
| `experiments/` | 实验报告与记录（`metrics.json` / `report.md` / 净值） |

## 已知限制

- 当前实验用不复权日线，除权除息日的跳空会被质量校验标出，后续切换到前复权。
- AKShare 东财接口偶发 `RemoteDisconnected`，已自动切换新浪接口；仍失败的股票写入 `data/cache/_fetch_failures.json` 并跳过。
- 研究层结论只进入信号层的 `strategy_params`，不直接驱动下单。

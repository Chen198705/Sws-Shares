# Vol-Aware Trading · Audit (2026-09-24)

> 目标：止损/止盈线由市场波动率自动划定，不由人手设红线；迭代引擎自适应调参。

## Plan B · Vol-Aware 自动红线（已落地）

### 代码路径
- `strategy_store.py::get_volatility_adjusted_stop_take()` line 436
  - `stop_loss = clamp(-vol_stop_k * atr_pct, vol_max_stop, vol_min_stop)`
  - `take_profit = max(vol_take_k * atr_pct, 0)`
- `trading_bot.py` line 452/542/632 — 三处使用 vol-aware 覆盖固定红线
- 持仓检查时打印 `vol红线 X% / Y%` tag（line 484-508）

### 仓位自适应
- `strategy_store.py::get_volatility_position_size()` line 463
  - `size = clip(vol_position_k / atr_pct*100, floor, ceiling)`
  - 日振幅 2% → 仓位 15%；4% → 7.5%

### 默认参数（DB 实测）
| key | value | 说明 |
|---|---|---|
| vol_stop_k | 2.5 | 止损距离 = 2.5× ATR% |
| vol_take_k | 4.0 | 止盈距离 = 4× ATR% |
| vol_min_stop | -0.04 | 止损不能比 -4% 更紧 |
| vol_max_stop | -0.12 | 止损不能比 -12% 更宽 |
| vol_position_k | 0.30 | 风险预算常数 |
| vol_position_floor | 0.03 | 仓位下限 |
| vol_position_ceiling | 0.20 | 仓位上限 |

### Banner vs 运行时
- Banner 显示的是 DB 里的固定值（短 -4% / 中 -8% / 长 -14%，已被第 8 次复核从默认 -3%/-5%/-10% 微调到 -4%/-8%/-14%）
- 持仓检查时 vol-aware 会覆盖固定值并打 `vol红线 X%` tag —— 这才是真正生效的红线

### Trailing Peak 持久化
- `trading_bot.py` line 64：`CREATE TABLE trailing_peaks (code PRIMARY KEY, peak_pnl, strategy_type, updated_at)`
- 每次持仓检查更新 peak（line 329），bot 重启不丢峰值
- 解决了"中青旅峰值 +7.6% 跌回 -0.84% 未触发"的根本问题

## Plan C · 迭代引擎自适应调参（已落地）

### 自动触发
- `trading_bot.py::trigger_iteration()` line 271
- 三处调用：line 510（持仓检查触发卖出）/ line 659（AI 买入成交）/ line 680（AI 卖出成交）
- 每次交易完成后检查 `should_iterate()`，达阈值则 `subprocess.Popen` 启动独立 `iteration_engine.py`

### 阈值
- `observation_trades_threshold = 3` —— 3 笔新增平仓触发观察（只观察不调参）
- `adjust_trades_threshold = 20` —— 20 笔新增平仓触发复核（AI 调参）
- 实际推进：每次交易后自动推进，无需人工触发

### 限幅
- `iteration_engine.py::_apply_damped()` —— `_DAMP_LIMITS` 单次改动上限：
  - short_stop_loss: ±2%
  - mid_stop_loss: ±3%
  - long_stop_loss: ±4%
  - mid_take_profit: ±3%
  - min_confidence: ±5（区间 [40, 90]）

### 隔离机制
- `iteration.lock` 文件 + `os.kill(pid, 0)` 探活 —— 防止并发迭代
- subprocess + 显式 PYTHONPATH + venv site-packages —— 避免线程继承环境
- 主线程不阻塞（`subprocess.Popen` 异步）

### 历史运行
| iteration | ts | closed | stage | 备注 |
|---|---|---|---|---|
| 1 | 2026-08-17 | 5 | review | 5 笔全部止损，触发首次复核 |
| 2-7 | 8/21-9/11 | 8-23 | observation | 仅观察，未调参 |
| 8 | 2026-09-11 | 25 | review | 触发复核，25 笔复盘 |
| 9 | 2026-09-15 | 28 | observation | 短线止损过紧 → 假突破 |
| 10 | 2026-09-16 | 31 | observation | 确认短线止损应放宽 |

### 当前水位（2026-09-24 收盘后）
- `last_iterated_sell_id = 82`（观察水位）
- `last_reviewed_sell_id = 65`（复核水位）
- `max_sell_id = 89`（当前实际卖单 ID）
- 待观察 = 89 - 82 = **7 笔**（超 3 阈值）
- 待复核 = 89 - 65 = **24 笔**（超 20 阈值）
- **下次任何交易后会自动触发复核调参**

## 中青旅案例（600138）根因复盘

| 时间 | 价格 | 浮盈 | 峰值 |
|---|---|---|---|
| 8/13 买入 | ¥7.11 | - | - |
| 中段 | ¥7.55 | +6.2% | 激活线 |
| 峰值 | ¥7.65 | +7.6% | ¥19,926 浮盈 |
| 当前 | ¥7.05 | -0.84% | -¥2,214 |

### 失败链路
1. `_trailing_peak` 是**进程内存 dict**（旧版）—— bot 期间重启 13 次，每次清零
2. 期间 +6.2% → +7.6% 多次触达激活线 → 重启后丢失，从未真正进 trailing 监控
3. 跌回 -0.84% 时，新版 trailing_peaks 表才能持久化峰值

### 修复
- `trailing_peaks` 表 + 每次持仓检查 INSERT OR REPLACE
- 进程重启不丢峰值，回撤止盈真正生效
- 配合 vol-aware，止损线也会按 ATR% 自动放宽，避免"被洗出"

## 待验证（开盘后）
1. `tail -f shenwansan_trading.log | grep vol红线` —— 持仓日志出现 `vol红线 X%` tag
2. `sqlite3 trading_log.db "SELECT * FROM trailing_peaks"` —— 中青旅等峰值持久化
3. 下次交易后下次 observation / review 自动触发（应见到 `iteration_run.log` 新增）
4. 中青旅若回撤到 vol_stop_k*ATR%，应自动清仓

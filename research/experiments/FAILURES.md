# FAILURES.md — 失败库

失败不删除、不洗白，只补证据链。

### 2026-08-15: mom_6_1 首次样本外回测失败

- 实验：EXP-20260815-001
- 现象：样本外年化 -15.08%，跑输 BL1 等权（-1.67%）；rank IC -0.042
- 原因分析：动量因子在本随机样本+不复权数据上不成立；数据含首发日与除权跳空
- 教训：结论先用全市场前复权数据复核，不能把 L0 管线结果当最终判断
- 下一步：EXP-20260815-002（全市场/前复权）

### 2026-08-15: liq_20d_amt 高流动性方向失败

- 实验：EXP-20260815-002
- 现象：高成交额组合样本外 -22.79%，远跑输等权
- 原因分析：高流动性方向在随机样本上无 alpha，且与低换手结论方向相反
- 下一步：低流动性方向已由 liq_20d_turnover 承接，进入 L1 复验

### 2026-08-15: 全市场 qfq 拉取 468 只失败

- 实验：EXP-20260815-003
- 现象：5337 只目标中 468 只接口返回空数据，全部 reason=empty，写入 `data/cache/_fetch_failures_qfq.json`，回测跳过（实际加载 4869 只）
- 原因分析：新股/北交所/长期停牌标的在 AKShare 前复权接口无返回
- 教训：全市场结论需标注排除名单，不能声称覆盖全部 A 股
- 下一步：重试失败名单或标记为长期不可得

### 2026-08-15: astock_limit_up_5d 涨停计数方向失效

- 实验：EXP-20260815-003
- 现象：5 日涨停计数高分位组合样本外 -64.90%，rank IC -0.058（t=-11.26），全市场前复权下大幅跑输等权
- 原因分析：追涨停在 A 股样本外呈强反转，涨停后买入承接的是高位风险
- 教训：禁止把涨停计数作为沈万三选股依据
- 下一步：如需纳入短线因子，应改为涨停后回调企稳方向而非追涨

### 2026-08-15: 敏感性检验 top_quantile 变体被 max_holdings 截断

- 实验：EXP-20260815-003
- 现象：全市场下 `top_quantile` ±20% 变体结果与 baseline 完全相同，初版报告误读为“参数稳健”
- 原因分析：`n_sel = min(int(len(scores)*quantile), max_holdings)`，全市场候选 4000+ 只时 ±20% 的候选数都超过 20，被 `max_holdings=20` 截断，变体失效
- 教训：稳健性检验必须检查变体是否真正改变了决策面；`top_quantile` 变体需与 `max_holdings` 联动
- 修复：`sensitivity.py` 已让 `top_quantile` 与 `max_holdings` 同比例变化，EXP-003 敏感性 v2 重跑

### 2026-08-16: EXP-004 GARCH 比较基准对齐 bug

- 实验：EXP-20260815-004
- 现象：初版 `compare_vol_models` 用 `fc.join(hist.shift(1))` 把 `pred_hist` 与 `actual` 放到同一天，MSE 几乎为 0、方向准确率 100%，结果失真；且 GARCH 预测未年化，与已实现波动率（×sqrt(252)）单位不一致
- 原因分析：对比基准必须是“预测日前一日已实现波动率”对“预测日当天已实现波动率”，且两边单位必须一致
- 教训：预测-实际对齐和单位换算要单独写断言/检查，不能只比较数字
- 修复：`actual=hist(预测日)`、`pred_hist=hist(预测日前一日)`，`next_vol_forecast` 与 `last_cond_vol` 年化；重跑后 H6 诚实结论为不通过

### 2026-08-16: EXP-005 截面 OLS 列冲突与 MultiIndex 参数名 bug

- 实验：EXP-20260815-005
- 现象：初版用 `factor_mats[f].join(..., lsuffix=...)` 按行拼接，列全是股票代码导致 `columns overlap but no suffix specified`；改为 MultiIndex 后 `res.params` 键变成元组，`params.get(factor)` 取不到系数
- 原因分析：因子矩阵是 (日期 × 股票)，跨因子拼接必须带 keys 构造 MultiIndex；回归前应把横截面转成 (股票 × 因子) 普通列
- 教训：涉及 MultiIndex 列的统计计算，先做最小合成数据单测，再上全市场
- 修复：`pd.concat(..., keys=factor_mats.keys())` 构造 MultiIndex，`cross_sectional_ols` 内转为 `(stock × factor)` DataFrame；本地合成 3 因子测试通过后重跑

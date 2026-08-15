# EXP-20260815-003 实验结果

| 策略 | 样本内 | 样本外 | rank IC | 成交占比 | 期数 |
|---|---|---|---|---|---|
| BL1_random_walk | 年化 2.14% / 波动 29.59% / 夏普 0.07 / 最大回撤 -61.70% | 年化 0.90% / 波动 34.31% / 夏普 0.03 / 最大回撤 -29.47% | - | 98.62% | 124 |
| BL2_historical_mean | 年化 -35.97% / 波动 38.38% / 夏普 -0.94 / 最大回撤 -99.46% | 年化 -52.35% / 波动 39.51% / 夏普 -1.32 / 最大回撤 -77.22% | -0.069 (t=-4.90) | 98.24% | 124 |
| BL3_equal_weight | 年化 2.14% / 波动 29.59% / 夏普 0.07 / 最大回撤 -61.70% | 年化 0.90% / 波动 34.31% / 夏普 0.03 / 最大回撤 -29.47% | - | 98.62% | 124 |
| BL4_simple_momentum | 年化 -16.10% / 波动 37.49% / 夏普 -0.43 / 最大回撤 -87.36% | 年化 -28.00% / 波动 43.47% / 夏普 -0.64 / 最大回撤 -55.39% | -0.020 (t=-1.46) | 98.26% | 117 |
| FACTOR_mom_6_1 | 年化 -17.58% / 波动 39.12% / 夏普 -0.45 / 最大回撤 -94.83% | 年化 -33.65% / 波动 40.07% / 夏普 -0.84 / 最大回撤 -63.74% | -0.011 (t=-0.93) | 97.90% | 123 |
| FACTOR_mom_12_1 | 年化 -16.10% / 波动 37.49% / 夏普 -0.43 / 最大回撤 -87.36% | 年化 -28.00% / 波动 43.47% / 夏普 -0.64 / 最大回撤 -55.39% | -0.020 (t=-1.46) | 98.26% | 117 |
| FACTOR_mom_1 | 年化 -48.90% / 波动 40.72% / 夏普 -1.20 / 最大回撤 -99.95% | 年化 -63.23% / 波动 42.22% / 夏普 -1.50 / 最大回撤 -88.49% | -0.064 (t=-4.73) | 98.64% | 124 |
| FACTOR_vol_60d_realized | 年化 7.44% / 波动 19.37% / 夏普 0.38 / 最大回撤 -37.60% | 年化 18.44% / 波动 24.44% / 夏普 0.75 / 最大回撤 -10.58% | -0.084 (t=-4.89) | 99.48% | 124 |
| FACTOR_vol_20d_atr | 年化 6.90% / 波动 19.98% / 夏普 0.35 / 最大回撤 -40.02% | 年化 15.48% / 波动 22.82% / 夏普 0.68 / 最大回撤 -9.67% | -0.083 (t=-4.98) | 99.44% | 124 |
| FACTOR_liq_20d_turnover | 年化 10.26% / 波动 18.96% / 夏普 0.54 / 最大回撤 -28.43% | 年化 19.21% / 波动 20.68% / 夏普 0.93 / 最大回撤 -13.02% | -0.082 (t=-4.94) | 99.56% | 124 |
| FACTOR_liq_20d_amt | 年化 -11.80% / 波动 35.03% / 夏普 -0.34 / 最大回撤 -88.38% | 年化 -12.80% / 波动 35.50% / 夏普 -0.36 / 最大回撤 -41.44% | -0.104 (t=-7.41) | 99.08% | 124 |
| FACTOR_liq_amihud_20d | 年化 -1.20% / 波动 28.33% / 夏普 -0.04 / 最大回撤 -62.56% | 年化 2.66% / 波动 32.12% / 夏普 0.08 / 最大回撤 -28.08% | 0.066 (t=4.55) | 99.32% | 124 |
| FACTOR_astock_limit_up_5d | 年化 -48.39% / 波动 34.99% / 夏普 -1.38 / 最大回撤 -99.94% | 年化 -64.90% / 波动 34.04% / 夏普 -1.91 / 最大回撤 -87.07% | -0.058 (t=-11.26) | 98.48% | 124 |
| FACTOR_astock_maxdd_60d | 年化 1.97% / 波动 22.33% / 夏普 0.09 / 最大回撤 -55.67% | 年化 14.72% / 波动 20.99% / 夏普 0.70 / 最大回撤 -14.98% | -0.020 (t=-1.25) | 98.68% | 124 |

## 样本外 regime 分层

| 策略 | Regime | 期数 | 累计收益 |
|---|---|---|---|
| BL1_random_walk | ❓ 转换期 | 1 | 38.59% |
| BL1_random_walk | 🌊 震荡市 | 20 | -4.43% |
| BL1_random_walk | 🐂 牛市 (高波动) | 2 | -4.19% |
| BL1_random_walk | 🐻 熊市 | 1 | -19.78% |
| BL2_historical_mean | ❓ 转换期 | 1 | 24.48% |
| BL2_historical_mean | 🌊 震荡市 | 20 | -64.17% |
| BL2_historical_mean | 🐂 牛市 (高波动) | 2 | -28.39% |
| BL2_historical_mean | 🐻 熊市 | 1 | -28.90% |
| BL3_equal_weight | ❓ 转换期 | 1 | 38.59% |
| BL3_equal_weight | 🌊 震荡市 | 20 | -4.43% |
| BL3_equal_weight | 🐂 牛市 (高波动) | 2 | -4.19% |
| BL3_equal_weight | 🐻 熊市 | 1 | -19.78% |
| BL4_simple_momentum | ❓ 转换期 | 1 | 34.41% |
| BL4_simple_momentum | 🌊 震荡市 | 20 | -42.86% |
| BL4_simple_momentum | 🐂 牛市 (高波动) | 2 | -12.33% |
| BL4_simple_momentum | 🐻 熊市 | 1 | -23.02% |
| FACTOR_mom_6_1 | ❓ 转换期 | 1 | 31.42% |
| FACTOR_mom_6_1 | 🌊 震荡市 | 20 | -52.33% |
| FACTOR_mom_6_1 | 🐂 牛市 (高波动) | 2 | -4.64% |
| FACTOR_mom_6_1 | 🐻 熊市 | 1 | -26.31% |
| FACTOR_mom_12_1 | ❓ 转换期 | 1 | 34.41% |
| FACTOR_mom_12_1 | 🌊 震荡市 | 20 | -42.86% |
| FACTOR_mom_12_1 | 🐂 牛市 (高波动) | 2 | -12.33% |
| FACTOR_mom_12_1 | 🐻 熊市 | 1 | -23.02% |
| FACTOR_mom_1 | ❓ 转换期 | 1 | 17.07% |
| FACTOR_mom_1 | 🌊 震荡市 | 20 | -74.89% |
| FACTOR_mom_1 | 🐂 牛市 (高波动) | 2 | -34.49% |
| FACTOR_mom_1 | 🐻 熊市 | 1 | -29.77% |
| FACTOR_vol_60d_realized | ❓ 转换期 | 1 | 30.56% |
| FACTOR_vol_60d_realized | 🌊 震荡市 | 20 | 14.75% |
| FACTOR_vol_60d_realized | 🐂 牛市 (高波动) | 2 | -7.54% |
| FACTOR_vol_60d_realized | 🐻 熊市 | 1 | 1.26% |
| FACTOR_vol_20d_atr | ❓ 转换期 | 1 | 27.36% |
| FACTOR_vol_20d_atr | 🌊 震荡市 | 20 | 8.57% |
| FACTOR_vol_20d_atr | 🐂 牛市 (高波动) | 2 | -4.96% |
| FACTOR_vol_20d_atr | 🐻 熊市 | 1 | 1.47% |
| FACTOR_liq_20d_turnover | ❓ 转换期 | 1 | 23.60% |
| FACTOR_liq_20d_turnover | 🌊 震荡市 | 20 | 25.96% |
| FACTOR_liq_20d_turnover | 🐂 牛市 (高波动) | 2 | -9.84% |
| FACTOR_liq_20d_turnover | 🐻 熊市 | 1 | 1.25% |
| FACTOR_liq_20d_amt | ❓ 转换期 | 1 | 29.39% |
| FACTOR_liq_20d_amt | 🌊 震荡市 | 20 | -14.15% |
| FACTOR_liq_20d_amt | 🐂 牛市 (高波动) | 2 | -14.78% |
| FACTOR_liq_20d_amt | 🐻 熊市 | 1 | -19.68% |
| FACTOR_liq_amihud_20d | ❓ 转换期 | 1 | 37.70% |
| FACTOR_liq_amihud_20d | 🌊 震荡市 | 20 | -8.58% |
| FACTOR_liq_amihud_20d | 🐂 牛市 (高波动) | 2 | -12.13% |
| FACTOR_liq_amihud_20d | 🐻 熊市 | 1 | -4.71% |
| FACTOR_astock_limit_up_5d | ❓ 转换期 | 1 | 18.85% |
| FACTOR_astock_limit_up_5d | 🌊 震荡市 | 20 | -77.13% |
| FACTOR_astock_limit_up_5d | 🐂 牛市 (高波动) | 2 | -31.65% |
| FACTOR_astock_limit_up_5d | 🐻 熊市 | 1 | -33.68% |
| FACTOR_astock_maxdd_60d | ❓ 转换期 | 1 | 20.74% |
| FACTOR_astock_maxdd_60d | 🌊 震荡市 | 20 | 30.75% |
| FACTOR_astock_maxdd_60d | 🐂 牛市 (高波动) | 2 | -14.52% |
| FACTOR_astock_maxdd_60d | 🐻 熊市 | 1 | -2.47% |

## 样本外子周期

| 策略 | 子周期 | 期数 | 累计收益 |
|---|---|---|---|
| BL1_random_walk | 2023H1 | 5 | 6.28% |
| BL1_random_walk | 2023H2 | 6 | -0.90% |
| BL1_random_walk | 2024H1 | 6 | -14.76% |
| BL1_random_walk | 2024H2 | 7 | 13.38% |
| BL2_historical_mean | 2023H1 | 5 | -0.31% |
| BL2_historical_mean | 2023H2 | 6 | -29.89% |
| BL2_historical_mean | 2024H1 | 6 | -43.50% |
| BL2_historical_mean | 2024H2 | 7 | -42.49% |
| BL3_equal_weight | 2023H1 | 5 | 6.28% |
| BL3_equal_weight | 2023H2 | 6 | -0.90% |
| BL3_equal_weight | 2024H1 | 6 | -14.76% |
| BL3_equal_weight | 2024H2 | 7 | 13.38% |
| BL4_simple_momentum | 2023H1 | 5 | -11.45% |
| BL4_simple_momentum | 2023H2 | 6 | -28.41% |
| BL4_simple_momentum | 2024H1 | 6 | -13.13% |
| BL4_simple_momentum | 2024H2 | 7 | -5.89% |
| FACTOR_mom_6_1 | 2023H1 | 5 | -2.74% |
| FACTOR_mom_6_1 | 2023H2 | 6 | -29.81% |
| FACTOR_mom_6_1 | 2024H1 | 6 | -31.08% |
| FACTOR_mom_6_1 | 2024H2 | 7 | -6.43% |
| FACTOR_mom_12_1 | 2023H1 | 5 | -11.45% |
| FACTOR_mom_12_1 | 2023H2 | 6 | -28.41% |
| FACTOR_mom_12_1 | 2024H1 | 6 | -13.13% |
| FACTOR_mom_12_1 | 2024H2 | 7 | -5.89% |
| FACTOR_mom_1 | 2023H1 | 5 | 17.49% |
| FACTOR_mom_1 | 2023H2 | 6 | -48.38% |
| FACTOR_mom_1 | 2024H1 | 6 | -45.24% |
| FACTOR_mom_1 | 2024H2 | 7 | -59.28% |
| FACTOR_vol_60d_realized | 2023H1 | 5 | 10.52% |
| FACTOR_vol_60d_realized | 2023H2 | 6 | -1.12% |
| FACTOR_vol_60d_realized | 2024H1 | 6 | 6.31% |
| FACTOR_vol_60d_realized | 2024H2 | 7 | 20.73% |
| FACTOR_vol_20d_atr | 2023H1 | 5 | 6.11% |
| FACTOR_vol_20d_atr | 2023H2 | 6 | -2.94% |
| FACTOR_vol_20d_atr | 2024H1 | 6 | 5.47% |
| FACTOR_vol_20d_atr | 2024H2 | 7 | 22.75% |
| FACTOR_liq_20d_turnover | 2023H1 | 5 | 9.80% |
| FACTOR_liq_20d_turnover | 2023H2 | 6 | 0.09% |
| FACTOR_liq_20d_turnover | 2024H1 | 6 | 10.37% |
| FACTOR_liq_20d_turnover | 2024H2 | 7 | 17.17% |
| FACTOR_liq_20d_amt | 2023H1 | 5 | 10.71% |
| FACTOR_liq_20d_amt | 2023H2 | 6 | -21.85% |
| FACTOR_liq_20d_amt | 2024H1 | 6 | -12.85% |
| FACTOR_liq_20d_amt | 2024H2 | 7 | 0.85% |
| FACTOR_liq_amihud_20d | 2023H1 | 5 | -4.92% |
| FACTOR_liq_amihud_20d | 2023H2 | 6 | -12.18% |
| FACTOR_liq_amihud_20d | 2024H1 | 6 | 4.35% |
| FACTOR_liq_amihud_20d | 2024H2 | 7 | 20.97% |
| FACTOR_astock_limit_up_5d | 2023H1 | 5 | -7.87% |
| FACTOR_astock_limit_up_5d | 2023H2 | 6 | -33.29% |
| FACTOR_astock_limit_up_5d | 2024H1 | 6 | -58.98% |
| FACTOR_astock_limit_up_5d | 2024H2 | 7 | -51.14% |
| FACTOR_astock_maxdd_60d | 2023H1 | 5 | 23.77% |
| FACTOR_astock_maxdd_60d | 2023H2 | 6 | 0.48% |
| FACTOR_astock_maxdd_60d | 2024H1 | 6 | 8.91% |
| FACTOR_astock_maxdd_60d | 2024H2 | 7 | -2.83% |

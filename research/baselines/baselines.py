"""BL1 随机游走 / BL2 历史均值 / BL3 等权 / BL4 简单动量。"""

from research.backtest.engine import monthly_rebalance


def bl1_random_walk(panel, costs, cfg):
    """随机游走：预测收益为 0，等价于全 universe 等权持有。"""
    import pandas as pd
    codes = list(panel.keys())
    all_dates = sorted(set().union(*[set(df["date"]) for df in panel.values()]))
    factor = pd.DataFrame(0.0, index=pd.DatetimeIndex(all_dates), columns=codes)
    return monthly_rebalance(
        panel, factor, costs,
        top_quantile=1.0,
        max_holdings=10000,
        min_listed_days=cfg.get("min_listed_days", 120),
        min_price=cfg.get("min_price", 3.0),
        max_price=cfg.get("max_price", 500.0),
    )


def bl2_historical_mean(panel, costs, cfg, lookback_days=60):
    """历史均值：用过去 60 日收益均值预测下一期，取高分位。"""
    import pandas as pd
    closes = {}
    for code, df in panel.items():
        df = df.sort_values("date")
        closes[code] = df.set_index("date")["close"]
    close_df = pd.DataFrame(closes).sort_index()
    factor = close_df.pct_change(lookback_days, fill_method=None).shift(1)  # 用 T-1 信息
    factor = factor.loc[factor.index >= close_df.index.min() + pd.Timedelta(days=lookback_days)]
    return monthly_rebalance(
        panel, factor, costs,
        top_quantile=cfg.get("top_quantile", 0.2),
        max_holdings=cfg.get("max_holdings", 20),
        min_listed_days=cfg.get("min_listed_days", 120),
        min_price=cfg.get("min_price", 3.0),
        max_price=cfg.get("max_price", 500.0),
    )


def bl3_equal_weight(panel, costs, cfg):
    """等权组合：整个 universe 等权买入持有。"""
    return bl1_random_walk(panel, costs, cfg)


def bl4_simple_momentum(panel, costs, cfg):
    """简单动量：12-1 月动量取高分位。"""
    from research.factors.momentum import compute_factor_panel
    factor = compute_factor_panel(panel, "mom_12_1")
    return monthly_rebalance(
        panel, factor, costs,
        top_quantile=cfg.get("top_quantile", 0.2),
        max_holdings=cfg.get("max_holdings", 20),
        min_listed_days=cfg.get("min_listed_days", 120),
        min_price=cfg.get("min_price", 3.0),
        max_price=cfg.get("max_price", 500.0),
    )

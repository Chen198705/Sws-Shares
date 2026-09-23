import sys
sys.path.insert(0, '.')
import market_data, strategy_store
import pandas as pd
import numpy as np
np.random.seed(42)
dates = pd.date_range('2026-01-01', periods=60)
close = 10 + np.cumsum(np.random.randn(60) * 0.1)
df = pd.DataFrame({
    'date': dates,
    'open': close * 0.99,
    'high': close * 1.02,
    'low': close * 0.98,
    'close': close,
    'volume': np.random.randint(1000000, 5000000, 60),
})
prof = market_data.calc_volatility_profile(df)
print(f"atr_20={prof['atr_20']:.4f} atr_pct={prof['atr_pct']*100:.2f}% vol_rank={prof['vol_rank']}")
params = strategy_store.load_params()
print(f"params: mid_sl={params.mid_stop_loss} vol_stop_k={params.vol_stop_k}")
for st in ['短线', '中线', '长线']:
    for atr in [0.005, 0.015, 0.025, 0.04]:
        sl, tp = strategy_store.get_volatility_adjusted_stop_take(st, params, atr)
        ps = strategy_store.get_volatility_position_size(params, atr)
        print(f"{st} ATR%={atr*100:.1f}% → sl={sl*100:.1f}% tp={tp*100:.1f}% pos≤{ps*100:.1f}%")

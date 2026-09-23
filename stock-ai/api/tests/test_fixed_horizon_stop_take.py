"""回归：周期止损止盈必须按固定参数执行，ATR 不得覆盖执行阈值。"""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategy_store import StrategyParams


class _Position:
    stock_code = "000001"
    avg_cost = 10.0
    current_price = 9.6
    volume = 100
    horizon = "short"


class _FilledOrder:
    status = "filled"
    filled_price = 9.6


class _Broker:
    def __init__(self):
        self.sell_calls = []

    def sellable_volume(self, code):
        return 100

    def sell(self, code, volume, price):
        self.sell_calls.append((code, volume, price))
        return _FilledOrder()


def test_short_stop_uses_minus_three_percent_even_with_wide_atr():
    tb = importlib.import_module("trading_bot")
    tb.get_effective_params = lambda: StrategyParams(
        short_stop_loss=-0.03,
        short_take_profit=0.08,
    )
    tb.get_trading_status = lambda: {"positions": [_Position()]}
    tb.get_stock_history = lambda code, days=30: object()
    tb.calc_volatility_profile = lambda history: {"atr_pct": 0.04}
    tb.rebalance_oversized_positions = lambda *args, **kwargs: False
    tb.trigger_iteration = lambda: None
    tb.close_attribution_for_code = lambda *args, **kwargs: None
    tb._load_trailing_peak = lambda code: None
    tb._save_trailing_peak = lambda *args, **kwargs: None
    tb._trailing_peak.clear()

    logged = []
    tb.log_trade = lambda *args, **kwargs: logged.append((args, kwargs))
    broker = _Broker()

    tb.check_positions(object(), broker)

    assert len(broker.sell_calls) == 1, broker.sell_calls
    assert logged, "固定止损成交后必须记录交易"
    reason = logged[0][0][5]
    assert "触发止损" in reason
    assert "[短线]" in reason


if __name__ == "__main__":
    test_short_stop_uses_minus_three_percent_even_with_wide_atr()
    print("PASS test_short_stop_uses_minus_three_percent_even_with_wide_atr")

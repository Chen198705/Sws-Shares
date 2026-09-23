"""AI 复评反噪声：连续 threshold 次 sell 才真正平仓。

我们不复用 trading_bot.check_positions 的全长逻辑，而是把 streak 决策
抽到本模块中独立验证，保持纯函数 + 内存 dict，易于单测。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def decide(decision: str, current_streak: int, threshold: int) -> tuple[str, int]:
    """复现 trading_bot 的 streak 逻辑，便于独立验证。"""
    cur = int(current_streak)
    if decision == "sell":
        cur += 1
    elif cur:
        cur = 0
    if decision == "sell" and cur >= threshold:
        return "execute_sell", cur
    return "skip_sell", cur


def _run(seq, threshold=2):
    streak = 0
    actions = []
    for d in seq:
        act, streak = decide(d, streak, threshold)
        actions.append(act)
    return actions, streak


def test_single_sell_skips():
    acts, streak = _run(["sell"])
    assert acts == ["skip_sell"]
    assert streak == 1


def test_two_consecutive_sells_executes():
    acts, streak = _run(["sell", "sell"])
    assert acts == ["skip_sell", "execute_sell"]
    assert streak == 2


def test_hold_resets_streak():
    acts, streak = _run(["sell", "hold"])
    assert acts == ["skip_sell", "skip_sell"]
    assert streak == 0


def test_skip_resets_streak():
    acts, streak = _run(["sell", "skip", "sell"])
    assert acts == ["skip_sell", "skip_sell", "skip_sell"]
    assert streak == 1


def test_threshold_3():
    acts, _ = _run(["sell", "sell", "sell"], threshold=3)
    assert acts[-1] == "execute_sell"
    acts2, _ = _run(["sell", "sell"], threshold=3)
    assert acts2[-1] == "skip_sell"


if __name__ == "__main__":
    for fn in [test_single_sell_skips,
               test_two_consecutive_sells_executes,
               test_hold_resets_streak,
               test_skip_resets_streak,
               test_threshold_3]:
        fn()
        print("PASS", fn.__name__)

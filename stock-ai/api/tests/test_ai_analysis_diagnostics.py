"""模型失败回退规则引擎时，必须保留可诊断的失败原因。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ai_client


class FailingClient:
    def chat(self, messages, **kwargs):
        raise RuntimeError("model unavailable")


def test_analyze_fallback_reports_ai_error():
    original_get_client = ai_client.get_client
    ai_client.get_client = lambda: FailingClient()
    diagnostics = {}
    try:
        text, action, used_ai, horizon = ai_client.analyze_with_fallback(
            {"代码": "600519", "最新价": 1400, "涨跌幅": 0},
            {"最新收盘": 1400, "MA5": 1390, "MA20": 1380},
            0,
            diagnostics=diagnostics,
        )
    finally:
        ai_client.get_client = original_get_client

    assert text
    assert action in {"buy", "sell", "hold"}
    assert used_ai is False
    assert horizon == "medium"
    assert "model unavailable" in diagnostics["ai_error"]


if __name__ == "__main__":
    test_analyze_fallback_reports_ai_error()
    print("test_ai_analysis_diagnostics: ok")

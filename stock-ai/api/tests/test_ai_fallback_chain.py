"""oMLX fallback 回归：成功回退后必须保留在后续请求的候选链中。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_client import OllamaClient


def test_successful_fallback_persists_across_calls():
    client = OllamaClient(base_url="http://example.invalid", api_key="test", model="primary")
    client.fallback_models = ["fallback"]
    calls = []

    def fake_call(model_name, messages, temperature, max_tokens, timeout=120):
        calls.append(model_name)
        if model_name == "primary":
            raise RuntimeError("primary HTTP 500: upstream unavailable")
        return "ok"

    client._call = fake_call
    messages = [{"role": "user", "content": "test"}]

    assert client.chat(messages) == "ok"
    assert client.chat(messages) == "ok"
    assert calls == ["primary", "fallback", "fallback"]
    assert "fallback" in client.fallback_models


def test_disabled_model_falls_back_to_next_model():
    client = OllamaClient(base_url="http://example.invalid", api_key="test", model="primary")
    client.fallback_models = ["fallback"]
    calls = []

    def fake_call(model_name, messages, temperature, max_tokens, timeout=120):
        calls.append(model_name)
        if model_name == "primary":
            raise RuntimeError(
                "primary HTTP 403: {\"error\":{\"type\":\"model_disabled\"}}"
            )
        return "ok"

    client._call = fake_call

    assert client.chat([{"role": "user", "content": "test"}]) == "ok"
    assert calls == ["primary", "fallback"]


if __name__ == "__main__":
    test_successful_fallback_persists_across_calls()
    test_disabled_model_falls_back_to_next_model()
    print("PASS test_successful_fallback_persists_across_calls")

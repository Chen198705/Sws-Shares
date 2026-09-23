"""oMLX fallback 回归：成功回退后必须保留在后续请求的候选链中。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_client
from ai_client import OllamaClient, _RETRY_ON_TRANSIENT


def _no_backoff():
    """测试里把退避清零，避免回归用例真的睡 14 秒。"""
    ai_client._RETRY_BACKOFF = 0.0
    ai_client._RETRY_BACKOFF_MAX = 0.0


def _restore_backoff():
    ai_client._RETRY_BACKOFF = _ORIG_BACKOFF
    ai_client._RETRY_BACKOFF_MAX = _ORIG_BACKOFF_MAX


_ORIG_BACKOFF = ai_client._RETRY_BACKOFF
_ORIG_BACKOFF_MAX = ai_client._RETRY_BACKOFF_MAX


def test_successful_fallback_persists_across_calls():
    _no_backoff()
    try:
        _run_successful_fallback_persists_across_calls()
    finally:
        _restore_backoff()


def _run_successful_fallback_persists_across_calls():
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
    # primary 的 500 是瞬时故障，先原地退避重试若干次；仍失败才回退。
    # 第二个请求已记住 fallback，所以直接从 fallback 开始。
    assert calls == ["primary"] * (_RETRY_ON_TRANSIENT + 1) + ["fallback", "fallback"]
    assert "fallback" in client.fallback_models


def test_transient_error_retries_same_model_before_fallback():
    client = OllamaClient(base_url="http://example.invalid", api_key="test", model="primary")
    client.fallback_models = ["fallback"]
    calls = []

    def fake_call(model_name, messages, temperature, max_tokens, timeout=120):
        calls.append(model_name)
        if len(calls) == 1:
            raise RuntimeError("primary HTTP 502: upstream unreachable")
        return "recovered"

    client._call = fake_call

    assert client.chat([{"role": "user", "content": "test"}]) == "recovered"
    assert calls == ["primary", "primary"]


def test_cold_load_failures_do_not_trip_long_circuit_breaker():
    """冷加载期间的 5xx 不该把模型拉黑 90 秒，否则后续分析直接失败。"""
    _no_backoff()
    try:
        _run_cold_load_failures_do_not_trip_long_circuit_breaker()
    finally:
        _restore_backoff()


def _run_cold_load_failures_do_not_trip_long_circuit_breaker():
    client = OllamaClient(base_url="http://example.invalid", api_key="test", model="primary")
    client.fallback_models = ["fallback"]

    def fake_call(model_name, messages, temperature, max_tokens, timeout=120):
        raise RuntimeError(f"{model_name} HTTP 502: upstream unreachable")

    client._call = fake_call
    for _ in range(2):
        try:
            client.chat([{"role": "user", "content": "test"}])
        except RuntimeError:
            pass

    # 两次完整失败（每次内部重试若干回）仍不该触发 90 秒长熔断
    assert client._bad_models.get("primary", 0) <= time.time()


def test_hard_failure_still_trips_circuit_breaker():
    _no_backoff()
    try:
        _run_hard_failure_still_trips_circuit_breaker()
    finally:
        _restore_backoff()


def _run_hard_failure_still_trips_circuit_breaker():
    client = OllamaClient(base_url="http://example.invalid", api_key="test", model="primary")
    client.fallback_models = ["fallback"]

    def fake_call(model_name, messages, temperature, max_tokens, timeout=120):
        if model_name == "primary":
            raise RuntimeError("primary HTTP 500: boom")
        return "ok"

    client._call = fake_call
    for _ in range(4):
        client.chat([{"role": "user", "content": "test"}])

    assert client._bad_models.get("fallback", 0) == 0


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
    for _name in (
        "test_successful_fallback_persists_across_calls",
        "test_transient_error_retries_same_model_before_fallback",
        "test_cold_load_failures_do_not_trip_long_circuit_breaker",
        "test_hard_failure_still_trips_circuit_breaker",
        "test_disabled_model_falls_back_to_next_model",
    ):
        globals()[_name]()
        print("PASS", _name)

"""健康检查必须立即返回，模型探活不得阻塞首屏。"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server
import ai_client


class SlowClient:
    model = "test-model"

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def is_alive(self):
        self.started.set()
        self.release.wait(2)
        return True


class FakeResponse:
    status_code = 200

    def json(self):
        return {"data": [{"id": "primary"}]}


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, timeout):
        self.calls.append((url, timeout))
        return FakeResponse()


def test_model_probe_uses_lightweight_models_endpoint():
    client = ai_client.OMLXClient(
        base_url="http://example.invalid",
        api_key="test",
        model="primary",
    )
    session = FakeSession()
    client.session = session

    assert client.is_alive() is True
    assert session.calls == [("http://example.invalid/v1/models", ai_client._PROBE_TIMEOUT)]


def test_health_returns_before_model_probe_finishes():
    original_client = server.get_client
    original_payload = server._health_cache["payload"]
    original_expires = server._health_cache["expires"]
    original_inflight = server._health_refresh_inflight
    client = SlowClient()
    server.get_client = lambda: client
    server._health_cache["payload"] = None
    server._health_cache["expires"] = 0.0
    server._health_refresh_inflight = False

    try:
        started = time.perf_counter()
        payload = server._health_payload_sync()
        elapsed = time.perf_counter() - started

        assert elapsed < 0.1, f"health blocked for {elapsed:.3f}s"
        assert payload["ai"] is None
        assert client.started.wait(0.5)
    finally:
        client.release.set()
        server.get_client = original_client
        server._health_cache["payload"] = original_payload
        server._health_cache["expires"] = original_expires
        server._health_refresh_inflight = original_inflight


if __name__ == "__main__":
    test_model_probe_uses_lightweight_models_endpoint()
    test_health_returns_before_model_probe_finishes()
    print("test_health_nonblocking: ok")

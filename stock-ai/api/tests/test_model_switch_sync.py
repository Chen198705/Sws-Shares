"""模型切换一致性 + 加载顺序回归。

覆盖三件事：
  1. /api/model/switch 与 /api/bot-model/set 走同一份状态（配置 + API 客户端）
  2. 沈万三进程每轮读 bot_config.json，改配置即热切换，不需要重启
  3. 模型列表真 single-flight，并发只打一次 oMLX；失败回落磁盘缓存
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server


class _FakeClient:
    def __init__(self, model="initial"):
        self.model = model
        self.switches = []

    def set_model(self, model):
        self.switches.append(model)
        self.model = model

    def mark_alive(self, alive=True):
        self.marked = alive


def test_apply_model_switch_writes_config_and_updates_client():
    fake = _FakeClient()
    orig_path = server.BOT_CONFIG_PATH
    orig_client = server.get_client
    with tempfile.TemporaryDirectory() as d:
        cfg = Path(d) / "bot_config.json"
        cfg.write_text(json.dumps({"model": "old-model", "keep": 1}))
        server.BOT_CONFIG_PATH = cfg
        server.get_client = lambda: fake
        try:
            result = server.apply_model_switch("new-model")
            assert result == {"ok": True, "model": "new-model"}, result
            saved = json.loads(cfg.read_text())
            assert saved["model"] == "new-model"
            assert saved["keep"] == 1, "切换模型不能丢掉配置里的其它字段"
            assert fake.switches == ["new-model"]
            assert list(Path(d).glob("*.tmp")) == [], "原子写不能留临时文件"
        finally:
            server.BOT_CONFIG_PATH = orig_path
            server.get_client = orig_client


def test_models_list_current_follows_persisted_config():
    fake = _FakeClient(model="client-side-model")
    orig_path = server.BOT_CONFIG_PATH
    orig_client = server.get_client
    with tempfile.TemporaryDirectory() as d:
        cfg = Path(d) / "bot_config.json"
        cfg.write_text(json.dumps({"model": "persisted-model"}))
        server.BOT_CONFIG_PATH = cfg
        server.get_client = lambda: fake
        try:
            models, current = server._build_model_list(["a", "persisted-model"])
            assert current == "persisted-model", current
            assert "persisted-model" in models
        finally:
            server.BOT_CONFIG_PATH = orig_path
            server.get_client = orig_client


def test_models_fetch_is_single_flight_and_cached_to_disk():
    calls = []

    def slow_fetch():
        calls.append(time.monotonic())
        time.sleep(0.3)
        return ["m1", "m2"]

    orig_fetch = server._fetch_remote_models_sync
    orig_file = server._MODELS_CACHE_FILE
    orig_client = server.get_client
    with tempfile.TemporaryDirectory() as d:
        server._MODELS_CACHE_FILE = Path(d) / "models_cache.json"
        server._fetch_remote_models_sync = slow_fetch
        server.get_client = lambda: _FakeClient()
        try:
            server._models_cache["models"] = None
            server._models_cache["expires"] = 0.0
            server._models_fetch_inflight = False
            server._models_fetch_done = None

            results = []
            threads = [
                threading.Thread(target=lambda: results.append(server._fetch_remote_models(force=True)))
                for _ in range(4)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(calls) == 1, f"并发 4 次只应打 1 次 oMLX，实际 {len(calls)} 次"
            assert all(r == ["m1", "m2"] for r in results), results
            assert json.loads((Path(d) / "models_cache.json").read_text())["models"] == ["m1", "m2"]
        finally:
            server._fetch_remote_models_sync = orig_fetch
            server._MODELS_CACHE_FILE = orig_file
            server.get_client = orig_client
            server._models_cache["models"] = None
            server._models_cache["expires"] = 0.0


def test_models_fetch_falls_back_to_stale_cache_when_omlx_down():
    orig_fetch = server._fetch_remote_models_sync
    orig_file = server._MODELS_CACHE_FILE
    orig_client = server.get_client
    with tempfile.TemporaryDirectory() as d:
        server._MODELS_CACHE_FILE = Path(d) / "models_cache.json"
        server._MODELS_CACHE_FILE.write_text(json.dumps({"models": ["stale-model"]}))
        def boom():
            raise RuntimeError("connection refused")
        server._fetch_remote_models_sync = boom
        server.get_client = lambda: _FakeClient()
        try:
            server._models_cache["models"] = None
            server._models_cache["expires"] = 0.0
            server._models_fetch_inflight = False
            server._models_fetch_done = None
            assert server._fetch_remote_models(force=True) == ["stale-model"]
        finally:
            server._fetch_remote_models_sync = orig_fetch
            server._MODELS_CACHE_FILE = orig_file
            server.get_client = orig_client
            server._models_cache["models"] = None
            server._models_cache["expires"] = 0.0


def test_bot_hot_switches_when_config_changes():
    """沈万三进程每轮读 bot_config.json；改配置即热切换，无需重启。"""
    import trading_bot

    fake = _FakeClient()
    orig_path = trading_bot._BOT_CONFIG_PATH
    with tempfile.TemporaryDirectory() as d:
        cfg = Path(d) / "bot_config.json"
        cfg.write_text(json.dumps({"model": "model-a"}))
        trading_bot._BOT_CONFIG_PATH = cfg
        try:
            assert trading_bot._get_bot_model() == "model-a"
            # 配置没变：不动客户端
            assert trading_bot.sync_bot_model(fake, "model-a") == "model-a"
            assert fake.switches == []
            # Dashboard 切换模型：下一轮必须热切换
            cfg.write_text(json.dumps({"model": "model-b"}))
            assert trading_bot.sync_bot_model(fake, "model-a") == "model-b"
            assert fake.switches == ["model-b"]
            # 坏 JSON / 缺失字段回落到默认值，不能崩
            cfg.write_text("{ broken")
            assert trading_bot._get_bot_model() == trading_bot._DEFAULT_MODEL
        finally:
            trading_bot._BOT_CONFIG_PATH = orig_path


if __name__ == "__main__":
    test_apply_model_switch_writes_config_and_updates_client()
    print("PASS test_apply_model_switch_writes_config_and_updates_client")
    test_models_list_current_follows_persisted_config()
    print("PASS test_models_list_current_follows_persisted_config")
    test_models_fetch_is_single_flight_and_cached_to_disk()
    print("PASS test_models_fetch_is_single_flight_and_cached_to_disk")
    test_models_fetch_falls_back_to_stale_cache_when_omlx_down()
    print("PASS test_models_fetch_falls_back_to_stale_cache_when_omlx_down")
    test_bot_hot_switches_when_config_changes()
    print("PASS test_bot_hot_switches_when_config_changes")

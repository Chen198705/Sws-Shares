"""验证耗时分析在线程池运行，不会阻塞事件循环上的其他接口。"""
import asyncio
import sys
import threading
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server


def test_analyze_does_not_block_market_status():
    original_prepare = server._prepare_analysis_payload_sync
    original_analyze = server._analyze_prepared_payload_sync

    def slow_prepare(code):
        time.sleep(0.5)
        return {"stock": {"代码": code, "最新价": 10.0}, "indicators": {}, "index_pct": 0.0}, 200

    def slow_analyze(prepared, diagnostics):
        return {
            "analysis": "test",
            "action": "hold",
            "used_ai": False,
            "horizon": "medium",
            "stock": prepared["stock"],
            "indicators": prepared["indicators"],
        }, 200

    server._prepare_analysis_payload_sync = slow_prepare
    server._analyze_prepared_payload_sync = slow_analyze

    async def scenario():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            analysis = asyncio.create_task(client.post("/api/analyze", json={"code": "600519"}))
            await asyncio.sleep(0.05)
            started = time.perf_counter()
            status = await client.get("/api/market-status")
            latency = time.perf_counter() - started
            analyzed = await analysis
            return status, analyzed, latency

    try:
        status, analyzed, latency = asyncio.run(scenario())
    finally:
        server._prepare_analysis_payload_sync = original_prepare
        server._analyze_prepared_payload_sync = original_analyze

    assert status.status_code == 200
    assert analyzed.status_code == 200
    assert analyzed.json()["analysis"] == "test"
    assert latency < 0.25, f"market-status blocked for {latency:.3f}s"


def test_analysis_preparation_runs_concurrently():
    original_prepare = server._prepare_analysis_payload_sync
    original_analyze = server._analyze_prepared_payload_sync
    original_semaphore = server._analyze_semaphore
    lock = threading.Lock()
    active_prepare = 0
    max_active_prepare = 0

    def slow_prepare(code):
        nonlocal active_prepare, max_active_prepare
        with lock:
            active_prepare += 1
            max_active_prepare = max(max_active_prepare, active_prepare)
        time.sleep(0.25)
        with lock:
            active_prepare -= 1
        return {"stock": {"代码": code}, "indicators": {}, "index_pct": 0.0}, 200

    def slow_analyze(prepared, diagnostics):
        time.sleep(0.25)
        return {
            "analysis": prepared["stock"]["代码"],
            "action": "hold",
            "used_ai": False,
            "horizon": "medium",
            "stock": prepared["stock"],
            "indicators": {},
        }, 200

    server._prepare_analysis_payload_sync = slow_prepare
    server._analyze_prepared_payload_sync = slow_analyze

    async def scenario():
        server._analyze_semaphore = asyncio.Semaphore(2)
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            started = time.perf_counter()
            responses = await asyncio.gather(*[
                client.post("/api/analyze", json={"code": code})
                for code in ("000001", "600036", "600519")
            ])
            return responses, time.perf_counter() - started

    try:
        responses, elapsed = asyncio.run(scenario())
    finally:
        server._prepare_analysis_payload_sync = original_prepare
        server._analyze_prepared_payload_sync = original_analyze
        server._analyze_semaphore = original_semaphore

    assert all(response.status_code == 200 for response in responses)
    assert max_active_prepare == 3, f"preparation was serialized: max={max_active_prepare}"
    assert elapsed < 1.25, f"three analyses took {elapsed:.3f}s"


if __name__ == "__main__":
    test_analyze_does_not_block_market_status()
    test_analysis_preparation_runs_concurrently()
    print("test_server_event_loop: ok")

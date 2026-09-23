"""验证耗时分析在线程池运行，不会阻塞事件循环上的其他接口。"""
import asyncio
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server


def test_analyze_does_not_block_market_status():
    original = server._analyze_payload_sync

    def slow_analyze(code):
        time.sleep(0.5)
        return {
            "analysis": "test",
            "action": "hold",
            "used_ai": False,
            "horizon": "medium",
            "stock": {"代码": code, "最新价": 10.0},
            "indicators": {},
        }, 200

    server._analyze_payload_sync = slow_analyze

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
        server._analyze_payload_sync = original

    assert status.status_code == 200
    assert analyzed.status_code == 200
    assert analyzed.json()["analysis"] == "test"
    assert latency < 0.25, f"market-status blocked for {latency:.3f}s"


if __name__ == "__main__":
    test_analyze_does_not_block_market_status()
    print("test_server_event_loop: ok")

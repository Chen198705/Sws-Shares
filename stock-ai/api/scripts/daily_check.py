#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""沈万三 例行巡检脚本（开盘 09:35 / 收盘 15:45）。

行为约定：
- 只读调用 API 与本地日志；不修改 Cloudflare/oMLX/端口。
- 异常时通过飞书 webhook 推送；正常时仅写日志，保持安静。
- 休市日不推送；phase=open 在非交易日静默退出；phase=close 在交易日 15:00 后照常跑。
"""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API_BASE = os.environ.get("SWS_API_BASE", "http://127.0.0.1:5168")
FEISHU_WEBHOOK = os.environ.get("SWS_FEISHU_WEBHOOK", "").strip()
LOG_DIR = Path("/Users/chenjianhui/AI/Sws-Shares/stock-ai/api/logs")
REPORT_LOG = LOG_DIR / "report.log"
ITER_LOG = LOG_DIR / "iteration_run.log"

EXPECTED = {
    "short_stop_loss": -0.03, "short_take_profit": 0.08,
    "mid_stop_loss": -0.08, "mid_take_profit": 0.15,
    "long_stop_loss": -0.14, "long_take_profit": 0.24,
}

WEEKDAYS = {0, 1, 2, 3, 4}


def http_get(path, timeout=8):
    url = f"{API_BASE}{path}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def feishu_post(text):
    if not FEISHU_WEBHOOK:
        print("[feishu] webhook 未配置，跳过推送", flush=True)
        return False
    payload = {"msg_type": "text", "content": {"text": text}}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        FEISHU_WEBHOOK, data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            body = r.read().decode("utf-8", "ignore")
        return "ok" in body or '"StatusCode":0' in body
    except urllib.error.URLError as e:
        print(f"[feishu] post failed: {e}", flush=True)
        return False


def log(line, logfile: Path):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = f"[{ts}] {line}\n"
    print(msg, end="", flush=True)
    with logfile.open("a", encoding="utf-8") as f:
        f.write(msg)


def tail_lines(path: Path, n=200, since=None):
    if not path.exists():
        return []
    text = path.read_text(errors="ignore").splitlines()
    if since:
        text = [l for l in text if l >= since]
    return text[-n:]


def parse_market(msg: str):
    msg = msg or ""
    if "休市中" in msg:
        return True, False
    if "已收盘" in msg:
        return False, False
    if "开盘" in msg or msg.startswith("交易中"):
        return False, True
    return False, False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True, choices=["open", "close"])
    parser.add_argument("--always-notify", action="store_true")
    args = parser.parse_args()

    logfile = LOG_DIR / f"daily_check_{args.phase}.log"
    log(f"=== start phase={args.phase} ===", logfile)

    today = dt.date.today()
    if today.weekday() not in WEEKDAYS:
        log("周末，非交易日，跳过。", logfile)
        log("=== done quiet ===", logfile)
        return

    anomalies = []
    note = []

    try:
        market = http_get("/api/market-status")
    except Exception as e:
        anomalies.append(f"market-status 请求失败: {e}")
        market = {"message": ""}

    is_holiday, market_open = parse_market(market.get("message", ""))
    note.append(f"市场: {market.get('message', '?')}")

    if args.phase == "open" and not market_open:
        log(f"phase=open 但市场未开盘（{'节假日' if is_holiday else '未知'}），跳过。", logfile)
        log("=== done quiet ===", logfile)
        return
    if args.phase == "close" and is_holiday:
        log("phase=close 处于法定节假日，跳过。", logfile)
        log("=== done quiet ===", logfile)
        return

    try:
        health = http_get("/api/health")
        if health.get("ai") is not True:
            anomalies.append(f"AI 离线: {health}")
    except Exception as e:
        anomalies.append(f"health 请求失败: {e}")

    try:
        bot = http_get("/api/bot-model")
        note.append(f"沈万三模型: {bot.get('model')}")
    except Exception as e:
        anomalies.append(f"bot-model 失败: {e}")

    try:
        params = http_get("/api/strategy-params")
        drift = []
        for k, v in EXPECTED.items():
            cur = params.get(k)
            if cur is None:
                drift.append(f"{k}=缺失")
            elif abs(cur - v) > 1e-6:
                drift.append(f"{k}={cur} (既定 {v})")
        if drift:
            anomalies.append("阈值漂移: " + "; ".join(drift))
    except Exception as e:
        anomalies.append(f"strategy-params 失败: {e}")

    try:
        reconcile = http_get("/api/reconcile")
        if not reconcile.get("identity", {}).get("consistent", True):
            anomalies.append(
                f"账本不一致: diff={reconcile.get('identity', {}).get('diff')}"
            )
    except Exception as e:
        anomalies.append(f"reconcile 失败: {e}")

    try:
        orders_data = http_get("/api/orders")
        today_str = today.isoformat()
        today_fills = [
            o for o in orders_data.get("orders", [])
            if o.get("status") == "filled" and o.get("time", "").startswith(today_str)
        ]
        note.append(f"今日成交 {len(today_fills)} 笔")
    except Exception as e:
        anomalies.append(f"orders 失败: {e}")

    if args.phase == "close":
        tail = tail_lines(REPORT_LOG, n=400, since=today_str)
        am_push = any("上午盘汇报" in l for l in tail)
        pm_push = any("下午盘汇报" in l for l in tail)
        if not am_push:
            anomalies.append("今日 11:30 飞书汇报缺失")
        if not pm_push:
            anomalies.append("今日 15:05 飞书汇报缺失")
        note.append(f"汇报: 上午{'Y' if am_push else 'N'} 下午{'Y' if pm_push else 'N'}")

        iter_tail = tail_lines(ITER_LOG, n=80, since=today_str)
        if not iter_tail:
            note.append("今日无迭代/复核记录")

    if anomalies:
        text = (
            f"沈万三 {'开盘' if args.phase=='open' else '收盘'} 巡检 "
            f"{dt.datetime.now():%Y-%m-%d %H:%M}\n"
            + " | ".join(note) + "\n异常:\n- " + "\n- ".join(anomalies)
        )
        log("ANOMALY: " + text.replace("\n", " | "), logfile)
        ok = feishu_post(text)
        log(f"feishu push={ok}", logfile)
    elif args.always_notify:
        text = (
            f"沈万三 {'开盘' if args.phase=='open' else '收盘'} 巡检 "
            f"{dt.datetime.now():%Y-%m-%d %H:%M}\n"
            + " | ".join(note)
        )
        feishu_post(text)
        log("notified (always)", logfile)
    else:
        log("无异常，保持安静。", logfile)

    log("=== done ===", logfile)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with (LOG_DIR / "daily_check_error.log").open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] FATAL: {e}\n")
        sys.exit(1)

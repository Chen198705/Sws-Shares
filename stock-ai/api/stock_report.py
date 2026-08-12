#!/usr/bin/env python3
import json
import os
import urllib.request
from datetime import datetime

API = "http://127.0.0.1:5168"
FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")
DRY_RUN = os.environ.get("DRY_RUN") == "1"


def api_get(path):
    req = urllib.request.Request(f"{API}{path}", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_feishu(msg):
    if not FEISHU_WEBHOOK:
        print("  [飞书] 未配置 webhook，跳过")
        return
    try:
        payload = {"msg_type": "text", "content": {"text": msg}}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            FEISHU_WEBHOOK,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
        print("  [飞书] 推送成功")
    except Exception as e:
        print(f"  [飞书] 推送失败: {e}")


def fmt_money(v):
    return f"{float(v):,.2f}"


def fmt_pct(v):
    return f"{float(v):+.2f}%"


def direction_cn(direction):
    return "买入" if direction == "buy" else "卖出"


def horizon_cn(horizon):
    return {"short": "短线", "medium": "中线", "long": "长线"}.get(horizon, horizon or "—")


def report():
    now = datetime.now()
    period = "上午盘" if now.hour < 12 else "下午盘"
    today = now.strftime("%Y-%m-%d")
    header = f"沈万三 {period}汇报 {now:%Y-%m-%d %H:%M}"

    try:
        portfolio = api_get("/api/portfolio")
        orders = api_get("/api/orders").get("orders", [])
    except Exception as e:
        msg = f"{header}\n\nAPI 获取失败: {e}"
        print(msg)
        send_feishu(msg)
        return

    balance = portfolio.get("balance", {})
    positions = portfolio.get("positions", [])
    today_orders = [o for o in orders if o.get("time", "").startswith(today)]

    lines = [header, ""]
    lines.append(
        f"总资产 ¥{fmt_money(balance.get('total_assets', 0))} | "
        f"现金 ¥{fmt_money(balance.get('cash', 0))} | "
        f"持仓 ¥{fmt_money(balance.get('market_value', 0))}"
    )
    lines.append("")

    if positions:
        lines.append("持仓:")
        for p in positions:
            lines.append(
                f"  {p.get('stock_code')} {p.get('stock_name', '')} "
                f"{p.get('volume', 0)}股 成本¥{fmt_money(p.get('avg_cost', 0))} "
                f"现价¥{fmt_money(p.get('current_price', 0))} "
                f"{fmt_pct(p.get('pnl_ratio', 0))} [{horizon_cn(p.get('horizon'))}]"
            )
    else:
        lines.append("持仓: 暂无")

    lines.append("")
    lines.append(f"今日成交 ({len(today_orders)}笔):")
    if today_orders:
        for o in today_orders:
            t = o.get("time", "")[11:16]
            lines.append(
                f"  {t} {direction_cn(o.get('direction'))} "
                f"{o.get('code')} {o.get('volume', 0)}股 "
                f"¥{fmt_money(o.get('filled_price', o.get('price', 0)))} "
                f"[{o.get('status', '')}]"
            )
    else:
        lines.append("  无成交")

    msg = "\n".join(lines)
    print(msg)
    print("=" * 30)
    if DRY_RUN:
        print("  [dry-run] 跳过飞书推送")
    else:
        send_feishu(msg)


if __name__ == "__main__":
    report()

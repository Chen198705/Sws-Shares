#!/usr/bin/env python3
"""
A 股 AI 分析 + 交易系统
用法：
  python main.py analyze-market          # 大盘综合分析
  python main.py analyze-stock <代码>    # 个股分析
  python main.py trade <代码>            # 分析 + 生成交易信号
  python main.py auto-trade <代码>       # 分析 + 信号 + 自动模拟下单
  python main.py portfolio               # 查看当前持仓和账户
  python main.py check-ai                # 检查 AI 连接状态
"""
import sys
import os
import json
from datetime import datetime

from config import REPORT_DIR, ENABLE_AUTO_TRADE
from analyzer import analyze_market, analyze_stock
from ai_client import OllamaClient
from market_data import get_all_indices
from trader import generate_trade_signal, execute_trade, get_trading_status


def save_report(title: str, content: str):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORT_DIR / f"{ts}_{title}.md"
    path.write_text(content, encoding="utf-8")
    print(f"报告已保存: {path}")


def cmd_analyze_market():
    print("=" * 60)
    print("大盘综合分析")
    print("=" * 60)
    result = analyze_market()
    if result["status"] == "error":
        print(f"错误: {result['message']}")
        return
    report = f"""# A股大盘分析报告
生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

{result['analysis']}
"""
    print(result["analysis"])
    save_report("market_analysis", report)


def cmd_analyze_stock(code: str):
    print("=" * 60)
    print(f"个股分析: {code}")
    print("=" * 60)
    result = analyze_stock(code)
    if result["status"] == "error":
        print(f"错误: {result['message']}")
        return
    report = f"""# 个股分析报告
股票: {result['stock']['股票名']}（{result['stock']['代码']}）
时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 实时行情
- 最新价: {result['stock']['最新价']}
- 涨跌幅: {result['stock']['涨跌幅']:+.2f}%

## AI 分析
{result['analysis']}
"""
    print(result["analysis"])
    save_report(f"stock_{code}", report)


def cmd_trade(code: str):
    print("=" * 60)
    print(f"交易分析: {code}")
    print("=" * 60)
    result = analyze_stock(code)
    if result["status"] == "error":
        print(f"错误: {result['message']}")
        return
    print(result["analysis"])
    print("\n" + "=" * 40)
    signal = generate_trade_signal(result)
    print("交易信号:")
    print(json.dumps(signal, ensure_ascii=False, indent=2))
    execute_trade(signal, code)


def cmd_auto_trade(code: str):
    print("=" * 60)
    print(f"自动交易: {code}")
    print("=" * 60)
    result = analyze_stock(code)
    if result["status"] == "error":
        print(f"错误: {result['message']}")
        return
    print(result["analysis"])
    print("\n" + "=" * 40)
    signal = generate_trade_signal(result)
    print("交易信号:")
    print(json.dumps(signal, ensure_ascii=False, indent=2))
    exec_result = execute_trade(signal, code)
    print("\n执行结果:")
    print(json.dumps(exec_result, ensure_ascii=False, indent=2, default=str))
    if exec_result.get("executed"):
        print(f"\n✅ 订单已提交: {exec_result['order'].order_id}")
        print(f"   {signal['action'].upper()} {code} × {exec_result['order'].volume}股")
    elif exec_result.get("reason") == "auto_trade_disabled":
        print("\n提示: 开启自动交易请设置环境变量 ENABLE_AUTO_TRADE=true")
    else:
        print(f"\n⚠️ 未执行: {exec_result.get('reason', 'unknown')}")


def cmd_portfolio():
    print("=" * 60)
    print("当前持仓 & 账户")
    print("=" * 60)
    status = get_trading_status()
    bal = status["balance"]
    print(f"\n账户资金")
    print(f"  现金:       {bal['cash']:,.2f} 元")
    print(f"  市值:       {bal['market_value']:,.2f} 元")
    print(f"  总资产:     {bal['total_assets']:,.2f} 元")

    positions = status["positions"]
    if positions:
        print(f"\n持仓 ({len(positions)} 只)")
        print(f"  {'代码':<8} {'名称':<10} {'数量':>6} {'成本':>10} {'现价':>10} {'盈亏':>12} {'收益率':>8}")
        print(f"  {'-'*64}")
        for p in positions:
            pnl_str = f"{p.unrealized_pnl:+,.2f}"
            pnl_ratio_str = f"{p.pnl_ratio:+.2f}%"
            print(f"  {p.stock_code:<8} {p.stock_name:<10} {p.volume:>6} {p.avg_cost:>10.2f} {p.current_price:>10.2f} {pnl_str:>12} {pnl_ratio_str:>8}")
    else:
        print("\n暂无持仓")

    orders = status["recent_orders"]
    if orders:
        print(f"\n近期订单 (最近 {len(orders)} 条)")
        for o in orders[:10]:
            dir_zh = "买入" if o.direction == "buy" else "卖出"
            status_zh = {"pending": "挂单", "filled": "成交", "cancelled": "已撤", "rejected": "拒绝"}
            ts = o.created_at[:19].replace("T", " ")
            print(f"  {ts}  {dir_zh} {o.stock_code} × {o.volume} @{o.price:.2f} [{status_zh.get(o.status, o.status)}]")


def cmd_check_ai():
    client = OllamaClient()
    alive = client.is_alive()
    if alive:
        print("AI 服务状态: 在线")
        print(f"API 地址: {client.base_url}")
        print(f"模型: {client.model}")
    else:
        print("AI 服务状态: 离线")
        print(f"请确认 Mac Studio 上的 oMLX 服务是否已启动")
        print(f"尝试的地址: {client.base_url}")


def cmd_show_indices():
    print("=" * 60)
    print("大盘实时行情")
    print("=" * 60)
    indices = get_all_indices()
    for name, data in indices.items():
        if "错误" in data:
            print(f"  {name}: 数据获取失败")
        else:
            pct = data["涨跌幅"]
            arrow = "▲" if pct >= 0 else "▼"
            print(
                f"  {name}（{data['代码']}）: "
                f"{arrow}{abs(pct):.2f}%  "
                f"现价={data['最新价']}  成交={data['成交额']/1e8:.2f}亿"
            )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "analyze-market":
        cmd_analyze_market()
    elif cmd == "analyze-stock" and len(sys.argv) >= 3:
        cmd_analyze_stock(sys.argv[2])
    elif cmd == "trade" and len(sys.argv) >= 3:
        cmd_trade(sys.argv[2])
    elif cmd == "auto-trade" and len(sys.argv) >= 3:
        cmd_auto_trade(sys.argv[2])
    elif cmd == "portfolio":
        cmd_portfolio()
    elif cmd == "check-ai":
        cmd_check_ai()
    elif cmd == "show-indices":
        cmd_show_indices()
    else:
        print(__doc__)
        sys.exit(1)

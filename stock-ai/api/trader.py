"""
交易层
- 信号生成：基于 AI 分析结果 + 规则引擎
- 执行层：通过 BrokerAdapter 接入券商
"""
import os
from config import ENABLE_AUTO_TRADE, TRADING_PLAN
from broker_adapter import get_broker, format_order_summary


# ── 全局券商实例 ────────────────────────────────────────────────
_broker = None


def _get_broker():
    global _broker
    if _broker is None:
        mode = os.getenv("BROKER_MODE", "simulation")
        _broker = get_broker(mode)
    return _broker


# ── 信号生成 ────────────────────────────────────────────────────

def generate_trade_signal(analysis_result: dict) -> dict:
    """
    根据 AI 分析结果生成结构化交易信号
    """
    if analysis_result.get("status") != "success":
        return {"action": "hold", "reason": "AI分析未成功"}

    analysis_text = analysis_result.get("analysis", "")
    stock = analysis_result.get("stock", {})
    indicators = analysis_result.get("indicators", {})
    text_lower = analysis_text.lower()

    confidence = 50
    action = "hold"
    position_ratio = 0.0
    stop_loss = None
    entry_price = None

    # 从AI文本中提取信号
    if any(kw in text_lower for kw in ["强烈买入", "强烈建议买入", "积极买入", "★★★★", "五星", "5星"]):
        action = "buy"
        confidence = 85
        position_ratio = 0.5 if TRADING_PLAN == "aggressive" else 0.3
    elif any(kw in text_lower for kw in ["买入", "建议买入", "逢低买入", "★★★", "四星", "4星"]):
        action = "buy"
        confidence = 65
        position_ratio = 0.2
    elif any(kw in text_lower for kw in ["卖出", "减仓", "清仓", "建议卖出", "★★", "二星", "2星"]):
        action = "sell"
        confidence = 75
    elif any(kw in text_lower for kw in ["观望", "等待", "不建议操作", "不买入", "不建议"]):
        action = "hold"
        confidence = 40

    # 从指标补充判断
    if indicators:
        rsi = indicators.get("RSI(14)", 50)
        macd_gold = indicators.get("MACD金叉", False)
        ma_bullish = indicators.get("均线多头", False)

        # 规则引擎补充信号修正
        if action == "hold" and rsi < 35 and macd_gold and ma_bullish:
            action = "buy"
            confidence = max(confidence, 55)
            position_ratio = 0.2

    # 提取关键价位
    price = stock.get("最新价", 0.0)
    if price > 0:
        # 从分析文本找止损位（简单关键词匹配）
        import re
        stop_match = re.search(r"止损[位]?[:：]?\s*(\d+\.?\d*)", analysis_text)
        if stop_match:
            stop_loss = float(stop_match.group(1))
        else:
            stop_loss = round(price * 0.97, 2)  # 默认止损 -3%
        entry_price = price

    return {
        "action": action,
        "confidence": confidence,
        "position_ratio": position_ratio,
        "stop_loss": stop_loss,
        "entry_price": entry_price,
        "signal_source": "ai_analysis",
        "auto_trade_enabled": ENABLE_AUTO_TRADE,
    }


# ── 交易执行 ────────────────────────────────────────────────────

def execute_trade(signal: dict, stock_code: str) -> dict:
    """
    执行交易信号
    返回执行结果 dict
    """
    action = signal["action"]
    broker = _get_broker()

    if not ENABLE_AUTO_TRADE:
        print(f"[交易信号 - 待人工确认] {action.upper()} {stock_code}")
        print(f"  置信度: {signal['confidence']}%")
        print(f"  建议仓位: {signal['position_ratio']*100:.0f}%")
        print(f"  止损位: {signal.get('stop_loss', 'N/A')}")
        if signal.get("entry_price"):
            print(f"  参考买入价: {signal['entry_price']}")
        print(f"  提示: 设置 ENABLE_AUTO_TRADE=true 开启自动下单")
        return {"executed": False, "reason": "auto_trade_disabled", "signal": signal}

    # 计算买入数量（按1手=100股，预算分配）
    if action == "buy":
        balance = broker.get_balance()
        available_cash = balance["cash"]
        budget = available_cash * signal["position_ratio"]
        entry_price = signal.get("entry_price") or signal.get("entry_price")
        if not entry_price or entry_price <= 0:
            return {"executed": False, "reason": "no_valid_price", "signal": signal}
        volume = int(budget / entry_price / 100) * 100  # 取整到百股
        if volume < 100:
            return {"executed": False, "reason": "insufficient_cash", "signal": signal}

        order = broker.buy(stock_code, volume, entry_price)
        summary = format_order_summary(order)
        print(f"[自动交易] {summary}")
        return {"executed": order.status == "filled", "order": order, "signal": signal}

    elif action == "sell":
        positions = broker.get_positions()
        pos = next((p for p in positions if p.stock_code == stock_code), None)
        if not pos:
            return {"executed": False, "reason": "no_position", "signal": signal}

        # 全部清仓或按比例卖出
        volume = pos.volume
        order = broker.sell(stock_code, volume, signal.get("entry_price"))
        summary = format_order_summary(order)
        print(f"[自动交易] {summary}")
        return {"executed": order.status == "filled", "order": order, "signal": signal}

    else:
        print(f"[自动交易] 信号={action}，无操作")
        return {"executed": False, "reason": "hold_signal", "signal": signal}


def get_trading_status() -> dict:
    """获取当前交易状态（持仓+账户+近期订单）"""
    broker = _get_broker()
    return {
        "balance": broker.get_balance(),
        "positions": broker.get_positions(),
        "recent_orders": broker.get_orders(20),
    }

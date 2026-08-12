"""
规则化技术分析引擎 - Ollama离线时的备选方案
基于经典技术指标给出量化评分和操作建议
"""
import pandas as pd
import numpy as np


def analyze(stock_data: dict, indicators: dict, index_pct: float = 0.0) -> tuple[str, str]:
    """
    返回 (analysis_text, action)
    基于技术指标量化打分
    """
    if not indicators:
        return "数据不足，无法分析", "hold"

    score = 0  # -10 ~ +10
    signals = []

    # ── RSI ──────────────────────────────────────────────────
    rsi = indicators.get("RSI(14)", 50)
    if rsi < 30:
        score += 3
        signals.append(f"RSI超卖({rsi:.1f})，关注反弹机会")
    elif rsi > 70:
        score -= 3
        signals.append(f"RSI超买({rsi:.1f})，注意回调风险")
    elif rsi < 40:
        score += 1
        signals.append(f"RSI偏低({rsi:.1f})")
    elif rsi > 60:
        score -= 1
        signals.append(f"RSI偏高({rsi:.1f})")

    # ── MACD ────────────────────────────────────────────────
    macd_hist = indicators.get("MACD_Hist", 0)
    if macd_hist > 0:
        score += 2
        signals.append("MACD红柱（多方动能）")
    else:
        score -= 2
        signals.append("MACD绿柱（空方动能）")

    # ── 均线 ────────────────────────────────────────────────
    if indicators.get("均线多头") == "是":
        score += 2
        signals.append("5日均线在20日均线上方（多头排列）")
    else:
        score -= 2
        signals.append("均线空头排列")

    # ── 价格与均线位置 ─────────────────────────────────────
    close = indicators.get("最新收盘", 0)
    ma5 = indicators.get("MA5", 0)
    ma20 = indicators.get("MA20", 0)
    if close > ma5:
        score += 1
        signals.append("价格位于MA5上方（短期强势）")
    else:
        score -= 1
        signals.append("价格跌破MA5（短期弱势）")
    if close > ma20:
        score += 1
        signals.append("价格位于MA20上方（中期强势）")
    else:
        score -= 1
        signals.append("价格跌破MA20（中期弱势）")

    # ── 大盘联动 ───────────────────────────────────────────
    stock_pct = stock_data.get("涨跌幅", 0)
    rel_str = stock_pct - index_pct
    if rel_str > 1.0:
        score += 2
        signals.append(f"相对大盘强势（跑赢{rel_str:.2f}%）")
    elif rel_str < -1.0:
        score -= 2
        signals.append(f"相对大盘弱势（跑输{abs(rel_str):.2f}%）")

    # ── 成交量 ─────────────────────────────────────────────
    vol = stock_data.get("成交量", 0)
    if vol > 1e8:
        signals.append(f"成交活跃（{vol/1e8:.1f}亿）")

    # ── 涨跌幅度 ───────────────────────────────────────────
    pct = abs(stock_pct)
    if stock_pct > 0 and pct < 2:
        score += 1
        signals.append("温和上涨，筹码稳定")
    elif stock_pct > 5:
        score -= 1
        signals.append("涨幅过大，追高风险高")
    elif stock_pct < -5:
        score += 1
        signals.append("大幅下跌，可能超卖")

    # ── 综合判断 ───────────────────────────────────────────
    if score >= 6:
        action, level = "buy", "建议买入"
        confidence = min(90, 60 + score * 3)
        position = 0.4
        stop_loss = close * 0.97
    elif score >= 3:
        action, level = "buy", "轻仓买入"
        confidence = min(75, 50 + score * 4)
        position = 0.2
        stop_loss = close * 0.95
    elif score >= -2:
        action, level = "hold", "观望"
        confidence = 50
        position = 0
        stop_loss = None
    elif score >= -5:
        action, level = "sell", "建议减仓"
        confidence = 70
        position = 0
        stop_loss = None
    else:
        action, level = "sell", "清仓回避"
        confidence = 80
        position = 0
        stop_loss = None

    stop_str = f"\n- 止损位：{stop_loss:.2f}（{stop_loss/close*100-100:+.1f}%）" if stop_loss else "\n- 止损位：N/A"
    position_str = f"\n- 建议仓位：{position*100:.0f}%" if position > 0 else f"\n- 建议仓位：0%（空仓观望）"

    text = f"""## 技术分析报告

### 综合评分：{score}（范围-10~+10，{level}）

### 各项信号
"""
    for s in signals:
        text += f"- {s}\n"

    text += f"""
### 操作建议
- **信号强度**：{confidence:.0f}%（{level}）{position_str}{stop_str}
- **风险等级**：{'低' if abs(score) <= 3 else '中' if abs(score) <= 6 else '高'}

### 分析说明
综合评分基于RSI、MACD、均线、量价关系、大盘联动五个维度量化得出。
评分{score}分{'偏向积极，适合关注买入机会' if score > 0 else '偏向谨慎，建议观望或减仓' if score < 0 else '中性，保持观望'}。
以上仅为技术分析参考，不构成投资建议。
"""
    return text, action

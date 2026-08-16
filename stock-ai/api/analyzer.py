"""
AI 驱动的市场分析器
"""
import json
from ai_client import OllamaClient, _policy_overlay_text
from market_data import (
    get_all_indices,
    get_stock_realtime,
    get_stock_history,
    calc_indicators,
)
try:
    from market_data import get_hot_concepts
except ImportError:
    get_hot_concepts = lambda: []
from research_snapshot import value_bp_metric
from strategy_store import get_research_overlay


SYSTEM_PROMPT = """你是一位专业A股投资顾问，有10年以上量化交易经验。
职责：
1. 分析大盘指数（上证、深证、创业板）的技术面和情绪面
2. 识别市场热点板块和概念
3. 给出个股具体操作建议（买入/卖出/观望）
4. 每次建议必须包含：信号强度（1-5星）、仓位建议、止损位
5. 标注风险等级（低/中/高）

要求：
- 用专业但易懂的语言，适合有2-3年投资经验的散户理解
- 用简洁Markdown输出
- 每个建议附置信度百分比
- 不给具体投资组合，只分析单只股票
"""


def _format_indices(indices: dict) -> str:
    lines = ["## 今日大盘行情\n"]
    for name, data in indices.items():
        if "错误" in data:
            lines.append(f"- {name}: 数据获取失败\n")
        else:
            pct = data["涨跌幅"]
            arrow = "▲" if pct >= 0 else "▼"
            lines.append(
                f"- {name}（{data['代码']}）: {arrow}{abs(pct):.2f}% "
                f"现价={data['最新价']}  成交={data['成交额']/1e8:.2f}亿 "
                f"最高={data['最高']} 最低={data['最低']}\n"
            )
    return "".join(lines)


def _format_stock(code: str, stock: dict, indicators: dict) -> str:
    lines = [
        f"- 股票名：{stock['股票名']}（{code}）\n",
        f"- 最新价：{stock['最新价']}  涨跌幅：{stock['涨跌幅']:+.2f}%\n",
        f"- 今日区间：今开={stock['今开']}  区间={stock['最低']}~{stock['最高']}\n",
        f"- 昨收：{stock['昨收']}  成交额：{stock['成交额']/1e8:.2f}亿\n",
    ]
    if indicators:
        lines.append(f"\n### 技术指标\n")
        lines.append(f"- MA5={indicators['MA5']:.2f}  MA20={indicators['MA20']:.2f}\n")
        lines.append(f"- RSI(14)={indicators['RSI(14)']:.1f}（{indicators['RSI状态']}）\n")
        lines.append(f"- MACD={indicators['MACD']:.4f}  MACD信号线={indicators['MACD_Signal']:.4f}\n")
        lines.append(f"- 均线多头排列：{indicators['均线多头']}  MACD金叉：{indicators['MACD金叉']}\n")
    return "".join(lines)


def _value_factor_line(code: str) -> str:
    """研究层 value_bp 约束注入：L1 通过时给出 PB 估值倾斜。"""
    try:
        fc = {f.get("id"): f for f in get_research_overlay().get("factor_constraints") or []}
        status = (fc.get("value_bp") or {}).get("status", "")
        if "通过" not in status and "有效" not in status:
            return ""
        pb, bp = value_bp_metric(code)
        if not pb or not bp:
            return ""
        tilt = "低估值偏好" if bp >= 0.75 else "高估值警惕" if bp < 0.25 else "估值中性"
        return f"- 研究层价值因子：PB={pb:.2f}（账面市值比 {bp:.2f}，{tilt}）\n"
    except Exception:
        return ""


def analyze_market() -> dict:
    """大盘综合分析"""
    client = OllamaClient()
    indices = get_all_indices()
    concepts = get_hot_concepts()
    market_str = _format_indices(indices)

    if concepts:
        lines = ["\n## 涨幅前列概念板块\n"]
        for c in concepts:
            arrow = "▲" if c["涨跌幅"] >= 0 else "▼"
            lines.append(f"- {arrow}{abs(c['涨跌幅']):.2f}%  {c['名称']}\n")
        market_str += "".join(lines)

    user_prompt = f"""{market_str}

请对当前大盘进行综合分析：
1. 三大指数联动情况（是否共振）
2. 市场情绪评估（恐慌/贪婪/中性）
3. 短期（1-3天）走势预判
4. 操作策略（轻仓/半仓/重仓/空仓）
5. 值得关注的板块和风险提示
"""
    try:
        analysis = client.chat(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=2048,
        )
        return {"status": "success", "market": market_str, "analysis": analysis}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def analyze_stock(stock_code: str) -> dict:
    """个股分析"""
    client = OllamaClient()
    stock = get_stock_realtime(stock_code)
    if "错误" in stock:
        return {"status": "error", "message": stock["错误"]}

    # 计算技术指标（用前60天日K）
    hist = get_stock_history(stock_code, days=60, adjust="")
    indicators = calc_indicators(hist)

    # 大盘背景
    indices = get_all_indices()
    market_str = _format_indices(indices)

    stock_str = _format_stock(stock_code, stock, indicators)

    user_prompt = f"""{market_str}

## 待分析个股

{stock_str}

## 研究层因子
{_value_factor_line(stock_code)}
{_policy_overlay_text()}

请分析这只股票，给出：
1. 技术面分析（均线、MACD、RSI解读）
2. 与大盘联动判断
3. 操作建议（买入/卖出/观望 + 仓位 + 止损位）
4. 风险提示
"""
    try:
        analysis = client.chat(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.4,
            max_tokens=2048,
        )
        return {
            "status": "success",
            "stock": stock,
            "indicators": indicators,
            "analysis": analysis,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

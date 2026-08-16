"""
AI 客户端 - oMLX在线时调用本地模型，离线时降级到规则引擎
"""
import os
import csv
from datetime import datetime, timedelta
from pathlib import Path
import requests
from config import OLLAMA_BASE_URL, OLLAMA_API_KEY, OLLAMA_MODEL
from rule_engine import analyze as rule_analyze
from research_snapshot import value_bp_metric
from strategy_store import get_research_overlay


_POLICY_WINDOW_DAYS = 5


def _recent_policy_types(days: int = _POLICY_WINDOW_DAYS) -> set:
    """返回最近 days 个自然日内有事件的 policy_type（v2 为最新事件登记）。"""
    base = Path(__file__).resolve().parents[2] / "research" / "data"
    p = base / "policy_events_v2.csv"
    if not p.exists():
        p = base / "policy_events.csv"
    cutoff = datetime.now() - timedelta(days=days)
    recent = set()
    try:
        with p.open(encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                try:
                    d = datetime.strptime(row["event_date"].strip(), "%Y-%m-%d")
                except (ValueError, TypeError):
                    continue
                if d >= cutoff:
                    recent.add(row["policy_type"].strip())
    except Exception:
        pass
    return recent


def _policy_overlay_text() -> str:
    """研究层政策因子叠加提示：仅当权重>0 且对应事件在窗口内才注入（低频、不替代）。"""
    try:
        factors = get_research_overlay().get("policy_factors") or []
        active = [f for f in factors if float(f.get("weight") or 0) > 0]
        if not active:
            return ""
        recent = _recent_policy_types()
        lines = []
        for f in active:
            ptype = f.get("policy_type") or ""
            if ptype in recent:
                car = float(f.get("mean_car") or 0)
                lines.append(
                    f"研究层政策因子 {f.get('id')}：{f.get('status')}，"
                    f"CAR {car*100:+.2f}%，样本 {f.get('n_events')}，窗口内有 {ptype} 事件"
                )
        return "\n".join(lines)
    except Exception:
        return ""


class OllamaClient:
    def __init__(self, base_url=None, api_key=None, model=None):
        self.base_url = (base_url or OLLAMA_BASE_URL).rstrip("/")
        self.api_key = api_key or OLLAMA_API_KEY
        self.model = model or OLLAMA_MODEL
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {self.api_key}"})
    def set_model(self, model: str):
        """切换当前使用的模型"""
        self.model = model
    def reset_session(self):
        """重置session以应用新的认证信息"""
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {self.api_key}"})


    def is_alive(self) -> bool:
        """oMLX /v1/models 不返回200，改用 /v1/chat/completions 探测"""
        try:
            r = self.session.post(
                f"{self.base_url}/v1/chat/completions",
                json={"model": self.model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
                timeout=10,
            )
            return r.status_code == 200
        except Exception:
            return False

    def chat(self, messages, temperature=0.7, max_tokens=2048) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        resp = self.session.post(
            f"{self.base_url}/v1/chat/completions",
            json=payload,
            timeout=300,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


def _parse_horizon(text: str) -> str:
    """从AI回复中解析持仓周期"""
    t = text.lower()
    if "[周期]" in text:
        seg = text.split("[周期]")[1].split("[")[0].lower()
        if "short" in seg or "短线" in seg or "5" in seg:
            return "short"
        elif "long" in seg or "长线" in seg or "3个月" in seg:
            return "long"
        else:
            return "medium"
    elif "短线" in text or "short" in t:
        return "short"
    elif "长线" in text or "long" in t:
        return "long"
    return "medium"


# 全局 AI 客户端实例（支持模型切换）
_client = None

def get_client() -> OllamaClient:
    global _client
    if _client is None:
        _client = OllamaClient()
    return _client


def _parse_action(text: str) -> str:
    """优先信任模型明确输出的 [信号]，避免正文里的止损/止盈把结论带偏。"""
    def has_advice(phrase: str) -> bool:
        return phrase in text and f"不{phrase}" not in text and f"不要{phrase}" not in text

    if "[信号]" in text:
        seg = text.split("[信号]", 1)[1].split("[", 1)[0].strip()
        first = seg.splitlines()[0].strip().lstrip("：: ").rstrip("，,。；;")
        if "持有" in first or "观望" in first or "等待" in first:
            return "hold"
        if any(k in first for k in ("卖出", "清仓", "减仓", "离场")):
            return "sell"
        if any(k in first for k in ("买入", "加仓", "低吸", "补仓")):
            return "buy"
        return "hold"
    if has_advice("建议卖出") or any(has_advice(k) for k in ("卖出信号", "建议清仓", "清仓回避", "建议减仓", "止盈离场", "止盈卖出")):
        return "sell"
    if has_advice("建议买入") or any(has_advice(k) for k in ("买入信号", "建议加仓", "建议低吸", "轻仓买入", "逢低买入")):
        return "buy"
    return "hold"


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
        return f"研究层价值因子：PB={pb:.2f}（账面市值比 {bp:.2f}，{tilt}）"
    except Exception:
        return ""


def analyze_with_fallback(stock_data: dict, indicators: dict, index_pct: float = 0.0) -> tuple[str, str, bool, str]:
    """
    返回 (analysis_text, action, used_ai, horizon)
    horizon: short | medium | long
    """
    client = get_client()

    def build_horizon_hint(ind):
        if not ind:
            return ""
        rsi = ind.get("RSI(14)", 50)
        kdj_k = ind.get("K", 50)
        ma5 = ind.get("MA5", 0)
        ma20 = ind.get("MA20", 0)
        close = ind.get("最新收盘", 0)
        if rsi and close:
            if rsi < 35 or (kdj_k and kdj_k < 30):
                return "（技术提示：RSI/KDJ超卖，可能存在短线反弹机会，建议关注持仓周期）"
            elif rsi > 65 or (kdj_k and kdj_k > 70):
                return "（技术提示：RSI/KDJ超买，短期注意回调风险）"
            elif ma5 and ma20 and close > ma5 > ma20:
                return "（技术提示：均线多头排列，趋势完好，中线机会较好）"
        return "（建议按中线持仓1-3个月操作）"

    if client.is_alive():
        try:
            horizon_hint = build_horizon_hint(indicators)
            value_hint = _value_factor_line(str(stock_data.get("代码", "")))
            messages = [
                {"role": "system", "content": "【沈万三】你是一个专业的A股量化交易分析师。请对给定的股票数据进行全面技术分析，并按以下格式输出：\n[信号] 买入/持有/卖出/观望（结合量价时空给出明确判断）\n[周期] short/medium/long（根据信号强度和股票特性判断）\n[分析]\n1. 趋势判断：当前价格与均线的位置关系，5/20日均线排列\n2. 动能分析：MACD金叉/死叉、RSI所处区间（超买超卖）\n3. 量价配合：成交量是否放大、量价背离情况\n4. 支撑压力：关键支撑位与压力位\n5. 风险提示：主要风险因素\n[操作建议] 具体入场价位、止损位、目标位（如有）"},
                {"role": "user", "content": f"股票数据：{stock_data}\n技术指标：{indicators}\n大盘涨跌：{index_pct}%{horizon_hint}\n{value_hint}"}
            ]
            text = client.chat(messages)
            action = _parse_action(text)
            horizon = _parse_horizon(text)
            return text, action, True, horizon
        except Exception as e:
            pass
    # 降级到规则引擎
    text, action = rule_analyze(stock_data, indicators, index_pct)
    return text, action, False, "medium"

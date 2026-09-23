"""
AI 客户端 - oMLX在线时调用本地模型，离线时降级到规则引擎
"""
import os
import csv
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path
import requests
from config import OLLAMA_BASE_URL, OLLAMA_API_KEY, OLLAMA_MODEL
from rule_engine import analyze as rule_analyze
from research_snapshot import value_bp_metric
from strategy_store import get_research_overlay


_POLICY_WINDOW_DAYS = 5

# 主模型不可用时的 fallback 链（按顺序尝试）。
# 优先选择：与主模型同系列但更小（响应快）→ 不同家族的中等模型。
# 实际生效顺序：env OLLAMA_FALLBACK_MODELS 优先（逗号分隔），否则用此默认值。
_DEFAULT_FALLBACK_MODELS = (
    "Qwen3.5-9B-MLX-4bit,"
    "Qwen3.6-27B-Fable-Fusion-711-MTPLX-8bit,"
    "Qwen3.6-35B-A3B-4bit"
)

# ── 加载性能调优：探测缓存 + 故障熔断 ────────────────────────────
# is_alive() 探测非常昂贵（每次都打 oMLX）；分析热路径每调用一次会成倍放大。
# 这里把探测结果缓存到 _PROBE_TTL 秒，避免前端轮询 / 健康检查反复打到 oMLX。
# 同时对持续 5xx / 连接失败 / 上游错误的模型做短期熔断，
# 让热门 fallback（如 Qwen3.5-9B-MLX-4bit）成为首选，跳过对已知坏模型的重复探测。
_PROBE_TTL = 30.0           # is_alive() 结果缓存秒数
_PROBE_TIMEOUT = 8          # 单模型探测超时（秒），自 15 缩到 8，减少探测链路
_BAD_MODEL_COOLDOWN = 90.0  # 模型失败后多少秒内跳过（熔断时长）
_BAD_MODEL_FAILURE_THRESHOLD = 2  # 连续失败次数才熔断，避免单次抖动误杀


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
        # 初始化 fallback 链（primary_model 锁定为构造时的 model）
        env_fb = os.getenv("OLLAMA_FALLBACK_MODELS", _DEFAULT_FALLBACK_MODELS)
        self.primary_model = self.model
        fb_list = [m.strip() for m in env_fb.split(",") if m.strip()]
        # 去重 + 跳过 primary
        self.fallback_models = [m for m in fb_list if m and m != self.primary_model]
        # ── 探测缓存 / 故障熔断（实例级，跨请求复用） ──
        self._alive_cache_until = 0.0
        self._alive_cache_value = False
        self._bad_models = {}            # model_name -> expires_at (epoch)
        self._bad_streak = {}            # model_name -> 连续失败计数
        self._probe_lock = threading.Lock()
        # 历史成功模型（首选用这个，省一次冷启动）
        self._last_good_model = None

    # ── Fallback chain ─────────────────────────────────────────────
    # 主模型挂掉时自动按 fallback_models 顺序切换；调用方无需感知。
    # 配置：env OLLAMA_FALLBACK_MODELS="m1,m2,m3"，缺省为 [_DEFAULT_FALLBACK_MODELS]
    def _attempts(self):
        """本次 chat 要尝试的模型链路：primary -> fallback（去重、跳过熔断中的模型）"""
        seen = []
        now = time.time()
        for m in [self.primary_model] + list(self.fallback_models):
            if not m or m in seen:
                continue
            # 跳过熔断中的模型（除非是 primary——主模型被指定后仍要试）
            bad_until = self._bad_models.get(m, 0)
            if bad_until > now and m != self.primary_model:
                continue
            seen.append(m)
        return seen

    def _probe(self, model_name: str, timeout: int = _PROBE_TIMEOUT) -> bool:
        """轻量健康探测（仅用于 is_alive，不更新 self.model）。
        已知熔断中的模型直接返回 False，不发起网络请求。"""
        bad_until = self._bad_models.get(model_name, 0)
        if bad_until > time.time():
            return False
        try:
            r = self.session.post(
                f"{self.base_url}/v1/chat/completions",
                json={"model": model_name, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
                timeout=timeout,
            )
            if r.status_code == 200:
                self._mark_good(model_name)
                return True
            self._mark_bad(model_name)
            return False
        except Exception:
            self._mark_bad(model_name)
            return False

    def _mark_bad(self, model_name: str) -> None:
        """累计失败次数，超过阈值才真正熔断（避免单次抖动误杀）。"""
        streak = self._bad_streak.get(model_name, 0) + 1
        self._bad_streak[model_name] = streak
        if streak >= _BAD_MODEL_FAILURE_THRESHOLD:
            self._bad_models[model_name] = time.time() + _BAD_MODEL_COOLDOWN
            print(f"[ai_client] 熔断 {model_name} {int(_BAD_MODEL_COOLDOWN)}s")

    def _mark_good(self, model_name: str) -> None:
        """成功的模型清零熔断计数，避免冷却累积。"""
        if model_name in self._bad_streak or model_name in self._bad_models:
            self._bad_streak.pop(model_name, None)
            self._bad_models.pop(model_name, None)
    def _call(self, model_name: str, messages, temperature: float, max_tokens: int, timeout: int = 120) -> str:
        """单模型调用：5xx / 连接失败 / 空 choices / upstream error 都抛 RuntimeError"""
        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            resp = self.session.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                timeout=timeout,
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.ChunkedEncodingError) as e:
            raise RuntimeError(f"{model_name} 连接失败: {type(e).__name__}: {e}") from e
        if resp.status_code >= 500:
            raise RuntimeError(f"{model_name} HTTP {resp.status_code}: {resp.text[:200]}")
        if resp.status_code >= 400:
            # 4xx 视为客户端问题（不切换 fallback，直接抛），让调用方修 prompt
            raise RuntimeError(f"{model_name} HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except Exception as e:
            raise RuntimeError(f"{model_name} 响应非 JSON: {resp.text[:200]}") from e
        if isinstance(data, dict) and data.get("error") and "choices" not in data:
            raise RuntimeError(f"{model_name} 上游错误: {data['error']}")
        choices = (data.get("choices") or []) if isinstance(data, dict) else []
        if not choices:
            raise RuntimeError(f"{model_name} 返回空 choices: {str(data)[:200]}")
        return choices[0]["message"]["content"]

    def set_model(self, model: str):
        """切换主模型；旧主模型压入 fallback 链首（若其确实可用过）"""
        if model == self.primary_model:
            return
        # 如果 self.model 已经在 fallback 链上（说明之前被提升过），保持不动；
        # 否则把"当前生效模型"压入 fallback 头部，下次失败时还能回来
        if self.model and self.model != self.primary_model and self.model not in self.fallback_models:
            self.fallback_models = [self.model] + self.fallback_models
        self.primary_model = model
        self.model = model

    def reset_session(self):
        """重置session以应用新的认证信息"""
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {self.api_key}"})


    def is_alive(self) -> bool:
        """主模型 + fallback 任一可达即 True。
        结果按 _PROBE_TTL 秒缓存，避免前端轮询 / 健康检查反复打到 oMLX。
        探测时不发起对已知熔断中模型的请求。"""
        with self._probe_lock:
            now = time.time()
            if now < self._alive_cache_until:
                return self._alive_cache_value
            ok = False
            for m in self._attempts():
                # _probe 内部已查熔断表；这里只对未熔断的模型发请求
                if self._probe(m):
                    ok = True
                    break
            self._alive_cache_value = ok
            self._alive_cache_until = now + _PROBE_TTL
            return ok

    def chat(self, messages, temperature=0.7, max_tokens=2048) -> str:
        """先主模型；失败按 fallback 链自动切换。成功后将 self.model 提升到可用模型。

        4xx 视为 prompt 问题（不消耗 fallback），立刻抛；
        5xx / 连接错 / 上游错误 → 切下一个。
        """
        attempts = self._attempts()
        if not attempts:
            raise RuntimeError("无可用模型：primary_model 与 fallback_models 均为空")
        last_err = None
        # 优先：上次成功过的模型最先试（避免每次付已知 fallback 的冷启动）
        if self._last_good_model and self._last_good_model in attempts and self._last_good_model != attempts[0]:
            primary_idx = attempts.index(self._last_good_model)
        else:
            primary_idx = 0
        ordered = attempts[primary_idx:] + attempts[:primary_idx]
        for m in ordered:
            try:
                text = self._call(m, messages, temperature, max_tokens)
                # 成功：清熔断计数 + 更新探活缓存 + 记住好用模型
                self._mark_good(m)
                with self._probe_lock:
                    self._alive_cache_value = True
                    self._alive_cache_until = time.time() + _PROBE_TTL
                self._last_good_model = m
                if m != self.model:
                    self.model = m
                    print(f"[ai_client] primary={self.primary_model} 不可用，已切换至 fallback {m}")
                return text
            except RuntimeError as e:
                last_err = e
                msg = str(e)
                # 4xx 直接透传给调用方，不消耗 fallback
                if "HTTP 4" in msg:
                    # 4xx 不熔断（客户端问题，模型本身没问题）
                    raise
                # 5xx / 连接 / 上游错误 → 累计熔断计数
                self._mark_bad(m)
                with self._probe_lock:
                    self._alive_cache_value = False
                    self._alive_cache_until = time.time() + _PROBE_TTL
                print(f"[ai_client] {m} 失败，转下一个: {msg[:120]}")
                continue
        raise RuntimeError(f"全部模型不可用 ({len(ordered)} 个): last={last_err}")


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

    # 不再做 client.is_alive() 预探测 — 那次探测会再走一遍 _attempts()，每次分析多 2N round trip。
    # 改为直接调 chat()：is_alive() 已被 _PROBE_TTL 缓存，chat() 内置 fallback + 熔断；
    # 整套失败时再做一次轻量回退到规则引擎（带 5s 截断，避免拖垮接口）。
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
    except Exception:
        # chat 全失败 — 退回到规则引擎，避免上次那种 502 拖死整页的体验
        pass
    text, action = rule_analyze(stock_data, indicators, index_pct)
    return text, action, False, "medium"

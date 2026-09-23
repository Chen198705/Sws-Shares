"""
AI 客户端 - oMLX在线时调用本地模型，离线时降级到规则引擎
"""
import os
import csv
import logging
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path
import requests
from config import OMLX_BASE_URL, OMLX_API_KEY, OMLX_MODEL
from rule_engine import analyze as rule_analyze
from research_snapshot import value_bp_metric
from strategy_store import get_research_overlay


_POLICY_WINDOW_DAYS = 5

# 主模型不可用时的 fallback 链（按顺序尝试）。
# 优先选择：与主模型同系列但更小（响应快）→ 不同家族的中等模型。
# 实际生效顺序：env OMLX_FALLBACK_MODELS 优先（逗号分隔），否则用此默认值。
_DEFAULT_FALLBACK_MODELS = (
    "Qwen3.5-9B-MLX-4bit,"
    "Qwen3.6-35B-A3B-8bit"
)

# ── 加载性能调优：探测缓存 + 故障熔断 ────────────────────────────
# is_alive() 探测非常昂贵（每次都打 oMLX）；分析热路径每调用一次会成倍放大。
# 这里把探测结果缓存到 _PROBE_TTL 秒，避免前端轮询 / 健康检查反复打到 oMLX。
# 同时对持续 5xx / 连接失败 / 上游错误的模型做短期熔断，
# 让热门 fallback（如 Qwen3.5-9B-MLX-4bit）成为首选，跳过对已知坏模型的重复探测。
_PROBE_TTL = 30.0           # is_alive() 结果缓存秒数
_PROBE_NEGATIVE_TTL = 3.0   # 探活失败缓存更短，服务恢复后快速改正状态
_BAD_MODEL_COOLDOWN = 90.0  # 模型失败后多少秒内跳过（熔断时长）
_BAD_MODEL_FAILURE_THRESHOLD = 2  # 连续失败次数才熔断，避免单次抖动误杀
# 冷加载 / 上游换入期间的瞬时故障不算"模型坏了"，用更宽的阈值和更短的冷却，
# 否则模型还在装载就被拉黑 90 秒，反而让后续分析直接失败。
_BAD_MODEL_TRANSIENT_COOLDOWN = 30.0
_BAD_MODEL_TRANSIENT_THRESHOLD = 4


def _env_float(name: str, default: float) -> float:
    """可调超时统一走环境变量，非法值回落默认，避免误配置把服务打死。"""
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


# 本地 oMLX 模型冷加载（换入显存 + 首次推理）可能需要 1-2 分钟，
# 因此单模型超时给足；三个值都可用环境变量覆盖，不必改代码。
# 约束：PROBE < MODEL_CALL < CHAIN_BUDGET < 前端 aiApi 超时。
_PROBE_TIMEOUT = _env_float("AI_PROBE_TIMEOUT", 10.0)          # 单模型探测超时
_MODEL_CALL_TIMEOUT = _env_float("AI_MODEL_TIMEOUT", 120.0)    # 单模型分析超时
_MODEL_CHAIN_BUDGET = _env_float("AI_CHAIN_BUDGET", 180.0)     # 整条 fallback 链总预算

# ── 瞬时故障重试 ──────────────────────────────────────────────
# oMLX 网关在模型冷启动 / 换入换出时会偶发 502 "Upstream unreachable"，
# 同一模型立刻重试一次通常就能成功。只在预算充足时重试，并且给后续
# fallback 预留时间，避免重试把整条链的预算吃光导致本轮分析彻底失败。
# 冷加载期间 oMLX 会连续返回 "upstream unreachable"，只重试一次往往还没换入完，
# 因此允许在同一模型上多退避重试几次；每次仍受整条链预算约束。
_RETRY_ON_TRANSIENT = int(_env_float("AI_TRANSIENT_RETRIES", 3))
_RETRY_MIN_REMAINING = _env_float("AI_RETRY_MIN_REMAINING", 60.0)  # 重试后必须留给 fallback 的预算（秒）
_RETRY_BACKOFF = _env_float("AI_RETRY_BACKOFF", 2.0)      # 首次退避（秒）
_RETRY_BACKOFF_MAX = _env_float("AI_RETRY_BACKOFF_MAX", 8.0)  # 退避上限（秒）


def _is_transient_error(msg: str) -> bool:
    """5xx / 连接断开 / 上游不可达属于可立即重试的瞬时故障；4xx 不属于。"""
    if "HTTP 4" in msg:
        return False
    markers = (
        "HTTP 5",
        "连接失败",
        "upstream",
        "incomplete chunked",
        "ConnectionError",
        "Timeout",
        "ChunkedEncodingError",
        "空 choices",
        "响应非 JSON",
    )
    return any(m.lower() in msg.lower() for m in markers)

logger = logging.getLogger(__name__)


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


class OMLXClient:
    """oMLX 客户端（Mac Studio 自建推理服务）。

    类名历史上叫 OllamaClient，容易和 Ollama 混淆；规范化后的名字是
    OMLXClient，旧名保留为别名，老 import 不会断。
    """

    def __init__(self, base_url=None, api_key=None, model=None):
        self.base_url = (base_url or OMLX_BASE_URL).rstrip("/")
        self.api_key = api_key or OMLX_API_KEY
        self.model = model or OMLX_MODEL
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {self.api_key}"})
        # 初始化 fallback 链（primary_model 锁定为构造时的 model）
        env_fb = os.getenv(
            "OMLX_FALLBACK_MODELS",
            os.getenv("OLLAMA_FALLBACK_MODELS", _DEFAULT_FALLBACK_MODELS),
        )
        self.primary_model = self.model
        fb_list = [m.strip() for m in env_fb.split(",") if m.strip()]
        # 去重 + 跳过 primary
        self.fallback_models = [m for m in fb_list if m and m != self.primary_model]
        # ── 探测缓存 / 故障熔断（实例级，跨请求复用） ──
        self._alive_cache_until = 0.0
        self._alive_cache_value = False
        self._bad_models = {}            # model_name -> expires_at (epoch)
        self._bad_streak = {}            # model_name -> 连续失败计数
        self._state_lock = threading.RLock()
        self._probe_inflight = False
        self._alive_revision = 0
        # 历史成功模型（首选用这个，省一次冷启动）
        self._last_good_model = None

    # ── Fallback chain ─────────────────────────────────────────────
    # 主模型挂掉时自动按 fallback_models 顺序切换；调用方无需感知。
    # 配置：env OMLX_FALLBACK_MODELS="m1,m2,m3"，缺省为 [_DEFAULT_FALLBACK_MODELS]
    def _attempts(self):
        """本次 chat 要尝试的模型链路：primary -> fallback（去重、跳过熔断中的模型）"""
        seen = []
        with self._state_lock:
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

    def _probe(self, timeout: int = _PROBE_TIMEOUT) -> bool:
        """轻量服务探测：检查 oMLX 是否在线且至少一个候选模型已注册。

        不使用 chat/completions 做探活，避免健康检查触发模型冷启动并占满推理槽位。
        """
        try:
            r = self.session.get(f"{self.base_url}/v1/models", timeout=timeout)
        except Exception:
            return False
        if r.status_code != 200:
            return False
        try:
            data = r.json()
        except Exception:
            return False
        available = {m.get("id") for m in data.get("data", []) if isinstance(m, dict)}
        return any(model in available for model in self._attempts())

    def _mark_bad(self, model_name: str, transient: bool = False) -> None:
        """累计失败次数，超过阈值才真正熔断（避免单次抖动误杀）。

        transient=True 表示冷加载 / 上游换入类瞬时故障，阈值和冷却都更宽松。
        """
        threshold = _BAD_MODEL_TRANSIENT_THRESHOLD if transient else _BAD_MODEL_FAILURE_THRESHOLD
        cooldown = _BAD_MODEL_TRANSIENT_COOLDOWN if transient else _BAD_MODEL_COOLDOWN
        with self._state_lock:
            streak = self._bad_streak.get(model_name, 0) + 1
            self._bad_streak[model_name] = streak
            if streak >= threshold:
                self._bad_models[model_name] = time.time() + cooldown
                print(f"[ai_client] 熔断 {model_name} {int(cooldown)}s")

    def _mark_good(self, model_name: str) -> None:
        """成功的模型清零熔断计数，避免冷却累积。"""
        with self._state_lock:
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
        探测时不发起对已知熔断中模型的请求，也不在持锁期间做网络请求。
        若另一线程正在探测，立即返回上一次结果，避免健康检查阻塞分析调用。"""
        now = time.time()
        with self._state_lock:
            if now < self._alive_cache_until:
                return self._alive_cache_value
            if self._probe_inflight:
                return self._alive_cache_value
            self._probe_inflight = True
            revision = self._alive_revision

        ok = False
        try:
            ok = self._probe()
        finally:
            with self._state_lock:
                # chat() 成功会写入更新的状态，不要让一个较旧探测覆盖它。
                if revision == self._alive_revision:
                    self._alive_cache_value = ok
                    ttl = _PROBE_TTL if ok else _PROBE_NEGATIVE_TTL
                    self._alive_cache_until = time.time() + ttl
                result = self._alive_cache_value
                self._probe_inflight = False
        return result

    def mark_alive(self, alive: bool = True) -> None:
        """外部已经确认 oMLX 可达 / 不可达时直接写探活缓存。

        模型列表接口和探活打的是同一个 /v1/models，首屏没必要打两次；
        服务端拿到列表结果后调用这里，健康检查就能命中缓存，
        少一次上游往返、也少一次冷启动窗口里的无谓阻塞。
        """
        with self._state_lock:
            self._alive_cache_value = bool(alive)
            self._alive_cache_until = time.time() + (_PROBE_TTL if alive else _PROBE_NEGATIVE_TTL)
            self._alive_revision += 1

    def chat(self, messages, temperature=0.7, max_tokens=2048, timeout: float = _MODEL_CALL_TIMEOUT) -> str:
        """先主模型；失败按 fallback 链自动切换。成功后将 self.model 提升到可用模型。

        4xx 视为 prompt 问题（不消耗 fallback），立刻抛；
        5xx / 连接错 / 上游错误 → 同模型重试一次，仍失败再切下一个。
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
        deadline = time.monotonic() + _MODEL_CHAIN_BUDGET
        budget_exhausted = False
        for m in ordered:
            for attempt in range(_RETRY_ON_TRANSIENT + 1):
                remaining = deadline - time.monotonic()
                if remaining <= 1:
                    last_err = RuntimeError("模型调用总预算已耗尽")
                    budget_exhausted = True
                    break
                call_timeout = max(1.0, min(float(timeout), remaining))
                if attempt > 0:
                    # 重试必须给后面的 fallback 留够预算，不能把整条链吃光
                    call_timeout = max(5.0, min(call_timeout, remaining - _RETRY_MIN_REMAINING))
                try:
                    text = self._call(
                        m,
                        messages,
                        temperature,
                        max_tokens,
                        timeout=call_timeout,
                    )
                except RuntimeError as e:
                    last_err = e
                    msg = str(e)
                    # 4xx 直接透传给调用方，不消耗 fallback
                    if "HTTP 4" in msg and not any(
                        marker in msg.lower()
                        for marker in (
                            "model_disabled",
                            "model_not_found",
                            "model not found",
                            "does not exist",
                            "disabled on the platform",
                        )
                    ):
                        # 4xx 不熔断（客户端问题，模型本身没问题）
                        raise
                    transient = _is_transient_error(msg)
                    backoff = min(_RETRY_BACKOFF * (2 ** attempt), _RETRY_BACKOFF_MAX)
                    will_retry = (
                        attempt < _RETRY_ON_TRANSIENT
                        and transient
                        and (deadline - time.monotonic()) > _RETRY_MIN_REMAINING + backoff
                    )
                    if will_retry:
                        print(f"[ai_client] {m} 瞬时失败，{backoff:.0f}s 后重试 ({attempt + 1}/{_RETRY_ON_TRANSIENT}): {msg[:80]}")
                        time.sleep(backoff)
                        continue
                    # 只有真正放弃该模型时才计一次熔断，且冷加载类故障走宽松口径。
                    # 若在每次重试时都计数，模型还在装载就会被拉黑 90 秒。
                    self._mark_bad(m, transient=transient)
                    with self._state_lock:
                        self._alive_cache_value = False
                        self._alive_cache_until = time.time() + _PROBE_NEGATIVE_TTL
                        self._alive_revision += 1
                    print(f"[ai_client] {m} 失败，转下一个: {msg[:120]}")
                    break
                # 成功：清熔断计数 + 更新探活缓存 + 记住好用模型
                self._mark_good(m)
                with self._state_lock:
                    self._alive_cache_value = True
                    self._alive_cache_until = time.time() + _PROBE_TTL
                    self._alive_revision += 1
                self._last_good_model = m
                if m != self.model:
                    self.model = m
                    print(f"[ai_client] primary={self.primary_model} 不可用，已切换至 fallback {m}")
                return text
            if budget_exhausted:
                break
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
# 历史别名：老代码 / 老测试 import OllamaClient 仍然可用
OllamaClient = OMLXClient

_client = None

def get_client() -> OMLXClient:
    global _client
    if _client is None:
        _client = OMLXClient()
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


def analyze_with_fallback(
    stock_data: dict,
    indicators: dict,
    index_pct: float = 0.0,
    diagnostics: dict = None,
) -> tuple[str, str, bool, str]:
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
    except Exception as e:
        # chat 全失败 — 退回到规则引擎，避免上次那种 502 拖死整页的体验
        if diagnostics is not None:
            diagnostics["ai_error"] = f"{type(e).__name__}: {e}"[:500]
        logger.warning("AI 分析失败，回退规则引擎: %s", e)
    text, action = rule_analyze(stock_data, indicators, index_pct)
    return text, action, False, "medium"

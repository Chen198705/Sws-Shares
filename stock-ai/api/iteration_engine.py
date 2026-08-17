"""迭代引擎 - 按 strategy_type 分组复盘，调用 oMLX"""
import sys, os, re, json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
from ai_client import OllamaClient
from strategy_store import (
    init_schema, load_params, save_params,
    get_closed_trades_for_review, get_strategy_summary, log_iteration,
    log_observation, get_recent_observations,
    should_iterate, get_max_sell_id,
)

_JSON_RE = re.compile(r'```json\s*(\{.*?\})\s*```', re.DOTALL)
LOCK_PATH = Path(__file__).parent / "logs" / "iteration.lock"
RUN_LOG_PATH = Path(__file__).parent / "logs" / "iteration_run.log"
_BOT_CONFIG_PATH = Path(__file__).parent / "bot_config.json"
_DEFAULT_MODEL = "Qwen3.6-35B-A3B-4bit"


def _get_bot_model() -> str:
    try:
        if _BOT_CONFIG_PATH.exists():
            return json.loads(_BOT_CONFIG_PATH.read_text()).get("model", _DEFAULT_MODEL)
    except Exception:
        pass
    return _DEFAULT_MODEL


def _log(msg: str):
    RUN_LOG_PATH.parent.mkdir(exist_ok=True)
    with RUN_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().isoformat()}] {msg}\n")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def iteration_running() -> bool:
    try:
        pid = int(LOCK_PATH.read_text().strip() or "0")
    except Exception:
        return False
    return bool(pid) and _pid_alive(pid)


def _acquire_lock() -> bool:
    RUN_LOG_PATH.parent.mkdir(exist_ok=True)
    try:
        pid = int(LOCK_PATH.read_text().strip() or "0")
    except Exception:
        pid = 0
    if pid and _pid_alive(pid):
        return False
    if pid:
        try:
            LOCK_PATH.unlink(missing_ok=True)
        except OSError:
            pass
    LOCK_PATH.write_text(str(os.getpid()))
    return True


def _release_lock():
    try:
        if LOCK_PATH.read_text().strip() == str(os.getpid()):
            LOCK_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def parse_params(text):
    m = _JSON_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    return {}


def build_segment_prompt(trades, stype, label):
    if not trades:
        return f"\n## {label}策略（0笔）\n无数据\n"
    lines = []
    for t in trades:
        pnl_s = f"+{t['pnl']:.2f}" if t['pnl'] >= 0 else f"{t['pnl']:.2f}"
        lines.append(
            f"- {t['code']} {'买入' if t['direction']=='buy' else '卖出'}@{t['price']} {pnl_s}元"
            f"  关闭:{t.get('closed_reason','?')}  指标:{t.get('entry_indicators','?')[:80]}")
    trades_txt = "\n".join(lines)
    wins = [x for x in trades if x['pnl'] > 0]
    loss = [x for x in trades if x['pnl'] <= 0]
    return f"""
## {label}策略（{len(trades)}笔，胜率{len(wins)/max(len(trades),1)*100:.0f}%，净盈亏{sum(x['pnl'] for x in trades):.2f}元）
{trades_txt}"""


def build_review_prompt(observations: list = None):
    summary = get_strategy_summary()
    segments = []
    for stype, label in [("短线","短线"), ("中线","中线"), ("长线","长线")]:
        trades = get_closed_trades_for_review(limit=50, strategy_type=stype)
        segments.append(build_segment_prompt(trades, stype, label))

    segments_txt = "\n".join(segments)
    all_trades = get_closed_trades_for_review(limit=50)
    if all_trades:
        wins = [x for x in all_trades if x['pnl'] > 0]
        net = sum(x['pnl'] for x in all_trades)
        summary_txt = f"总计 {len(all_trades)} 笔，胜率 {len(wins)/len(all_trades)*100:.0f}%，净盈亏 {net:.2f} 元"
    else:
        summary_txt = "尚无平仓交易"

    obs_txt = ""
    if observations:
        lines = []
        for o in observations:
            lines.append(f"[{o.get('ts','')[:10]} 观察#{o.get('iteration_num')} {o.get('closed_trades_count')}笔] {o.get('insights','')[:180]}")
        obs_txt = "\n".join(lines)

    return f"""你是专业的A股量化交易顾问，正在进行策略自我复盘。

{summary_txt}
{segments_txt}

## 此前累计观察记录（样本少时先只观察，不急于调参）
{obs_txt if obs_txt else "暂无观察记录"}

## 你的任务
1. 结合累计观察，分别判断短线/中线/长线策略的有效性：哪些有效、哪些亏损、背后规律；先判断这些规律是否可能只是噪声
2. 只在你认为样本和规律足以支撑调整时，给出参数调整建议（JSON，用 ```json 包裹）：
   - short_stop_loss / short_take_profit: 短线止损/止盈（建议 -2%/-3%、+5%/+8%）
   - mid_stop_loss / mid_take_profit: 中线止损/止盈（建议 -5%、+10%/+15%）
   - long_stop_loss / long_take_profit: 长线止损/止盈（建议 -8%/+-10%、+20%/+30%）
   - min_confidence: 最小买入置信度（当前60）
   止损必须是负数，止盈必须是正数；可用小数（-0.03 表示 -3%）或整数百分比（-3）
   若证据不足，可以只输出 JSON 空对象 {{}} 并说明原因，不要为了改而改
3. 给出300字以内的策略改进总结

请用中文回答，先总结分策略分析，再用 ```json 包裹参数。"""


def build_observation_prompt():
    summary = get_strategy_summary()
    segments = []
    for stype, label in [("短线","短线"), ("中线","中线"), ("长线","长线")]:
        trades = get_closed_trades_for_review(limit=50, strategy_type=stype)
        segments.append(build_segment_prompt(trades, stype, label))
    segments_txt = "\n".join(segments)
    all_trades = get_closed_trades_for_review(limit=50)
    if all_trades:
        wins = [x for x in all_trades if x['pnl'] > 0]
        net = sum(x['pnl'] for x in all_trades)
        summary_txt = f"当前累计 {len(all_trades)} 笔，胜率 {len(wins)/len(all_trades)*100:.0f}%，净盈亏 {net:.2f} 元"
    else:
        summary_txt = "尚无平仓交易"

    return f"""你是专业的A股量化交易顾问，正在做小样本快速观察，而不是下结论。

{summary_txt}
{segments_txt}

## 观察任务（样本还不足，禁止给出确定的策略结论）
1. 记录这笔新增样本的盈亏是否偏离近期分布
2. 指出可能成立、但需要更多样本验证的假设（最多3条）
3. 只输出观察纪要（250字内），不要输出 JSON 参数

请用中文回答。"""


_DAMP_LIMITS = {
    "short_stop_loss": 0.02,
    "short_take_profit": 0.02,
    "mid_stop_loss": 0.03,
    "mid_take_profit": 0.03,
    "long_stop_loss": 0.04,
    "long_take_profit": 0.04,
}
_DAMP_CONFIDENCE = 5


def _apply_damped(delta: dict, params) -> dict:
    """复核时应用参数调整，单次改动上限由 _DAMP_LIMITS 约束，返回实际应用值。"""
    applied = {}
    for key, limit in _DAMP_LIMITS.items():
        if key not in delta:
            continue
        try:
            val = float(delta[key])
        except (TypeError, ValueError):
            continue
        if abs(val) > 1:
            val /= 100
        if key.endswith("_stop_loss") and val >= 0:
            continue
        if key.endswith("_take_profit") and val <= 0:
            continue
        cur = getattr(params, key)
        bounded = min(max(val, cur - limit), cur + limit)
        setattr(params, key, bounded)
        applied[key] = round(bounded, 4)
    if "min_confidence" in delta:
        try:
            val = int(delta["min_confidence"])
        except (TypeError, ValueError):
            pass
        else:
            cur = params.min_confidence
            bounded = min(max(val, cur - _DAMP_CONFIDENCE), cur + _DAMP_CONFIDENCE)
            bounded = min(max(bounded, 40), 90)
            params.min_confidence = bounded
            applied["min_confidence"] = bounded
    return applied


def _run_observation(obs_cnt: int) -> bool:
    params = load_params()
    model = _get_bot_model()
    client = OllamaClient(model=model)
    _log(f"观察开始：新增平仓 {obs_cnt} 笔，模型 {model}")
    if not client.is_alive():
        _log("oMLX 不可用，观察跳过")
        print("[迭代引擎] oMLX 不可用，观察跳过")
        return False
    prompt = build_observation_prompt()
    try:
        resp = client.chat([
            {"role": "system", "content": "你是一位专业的A股量化交易顾问。小样本阶段只观察、不武断下结论，回答简洁专业。"},
            {"role": "user", "content": prompt}
        ], temperature=0.3, max_tokens=1200)
    except Exception as e:
        _log(f"观察AI调用失败: {e}")
        print(f"[迭代引擎] 观察AI调用失败: {e}")
        return False
    if not resp or not resp.strip():
        _log("观察AI返回空内容，不消费本次观察")
        print("[迭代引擎] 观察AI返回空内容，跳过")
        return False

    params.iteration += 1
    params.last_iteration_at = datetime.now().isoformat()
    params.last_insight = resp.strip()[:200]
    params.last_iterated_sell_id = get_max_sell_id()
    save_params(params)
    log_observation(params.iteration, len(get_closed_trades_for_review()),
                    resp.strip()[:500], resp[:800])
    _log(f"观察 #{params.iteration} 完成，last_iterated_sell_id={params.last_iterated_sell_id}")
    print(f"[{datetime.now().isoformat()}] 观察 #{params.iteration} 完成（只观察，未调参）")
    print(f"  观察纪要: {resp.strip()[:150]}")
    return True


def _run_review(rev_cnt: int) -> bool:
    params = load_params()
    model = _get_bot_model()
    client = OllamaClient(model=model)
    _log(f"复核开始：累计待复核平仓 {rev_cnt} 笔，模型 {model}")
    if not client.is_alive():
        _log("oMLX 不可用，复核跳过")
        print("[迭代引擎] oMLX 不可用，复核跳过")
        return False

    print(f"[{datetime.now().isoformat()}] 复核开始...")
    prompt = build_review_prompt(get_recent_observations())
    try:
        resp = client.chat([
            {"role": "system", "content": "你是一位专业的A股量化交易顾问。回答简洁专业，禁止废话。"},
            {"role": "user", "content": prompt}
        ], temperature=0.3, max_tokens=2000)
    except Exception as e:
        _log(f"AI调用失败: {e}")
        print(f"[迭代引擎] AI调用失败: {e}")
        return False
    if not resp or not resp.strip():
        _log("AI 返回空内容，不消费本次复核")
        print("[迭代引擎] AI 返回空内容，跳过")
        return False

    delta = parse_params(resp)
    summary = _JSON_RE.sub('', resp).strip()[:400]
    applied = _apply_damped(delta, params)

    params.iteration += 1
    params.last_iteration_at = datetime.now().isoformat()
    params.last_insight = summary[:200]
    params.last_iterated_sell_id = get_max_sell_id()
    params.last_reviewed_sell_id = get_max_sell_id()
    save_params(params)

    log_iteration(params.iteration, len(get_closed_trades_for_review()),
                  summary, str(applied), resp[:500], stage="review")
    _log(f"复核 #{params.iteration} 完成，last_reviewed_sell_id={params.last_reviewed_sell_id}，实际参数变化 {applied}")
    print(f"[{datetime.now().isoformat()}] 复核 #{params.iteration} 完成")
    if applied:
        print(f"  实际调参（已限幅）: {applied}")
    else:
        print("  本次复核无参数调整（样本规律不足）")
    print(f"  总结: {summary[:150]}")
    return True


def run_iteration():
    init_schema()
    if not _acquire_lock():
        _log("迭代已在进行中，跳过本次")
        print("[迭代引擎] 已有迭代进程在运行，跳过")
        return False
    try:
        obs_cnt, rev_cnt, obs_ready, rev_ready = should_iterate()
        if not obs_ready and not rev_ready:
            _log(f"观察 {obs_cnt}/{load_params().observation_trades_threshold} 笔，复核 {rev_cnt}/{load_params().adjust_trades_threshold} 笔，均未达阈值，跳过")
            print(f"[{datetime.now().isoformat()}] 迭代跳过：观察 {obs_cnt} 笔未达阈值，复核 {rev_cnt} 笔未达阈值")
            return False
        if rev_ready:
            return _run_review(rev_cnt)
        return _run_observation(obs_cnt)
    finally:
        _release_lock()


if __name__ == "__main__":
    run_iteration()

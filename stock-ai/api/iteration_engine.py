"""迭代引擎 - 按 strategy_type 分组复盘，调用 oMLX"""
import sys, re, json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
from ai_client import OllamaClient
from strategy_store import (
    init_schema, load_params, save_params,
    get_closed_trades_for_review, get_strategy_summary, log_iteration,
)

_JSON_RE = re.compile(r'```json\s*(\{.*?\})\s*```', re.DOTALL)


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


def build_review_prompt():
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

    return f"""你是专业的A股量化交易顾问，正在进行策略自我复盘。

{summary_txt}
{segments_txt}

## 你的任务
1. 分别分析短线/中线/长线策略的有效性：哪些有效、哪些亏损、背后规律
2. 给出各策略的参数调整建议（JSON，用 ```json 包裹）：
   - short_stop_loss / short_take_profit: 短线止损/止盈（建议 -2%/-3%、+5%/+8%）
   - mid_stop_loss / mid_take_profit: 中线止损/止盈（建议 -5%、+10%/+15%）
   - long_stop_loss / long_take_profit: 长线止损/止盈（建议 -8%/+-10%、+20%/+30%）
   - min_confidence: 最小买入置信度（当前60）
   - closed_trades_threshold: 触发迭代需积累的平仓笔数
3. 给出300字以内的策略改进总结

请用中文回答，先总结分策略分析，再用 ```json 包裹参数。"""


def run_iteration():
    init_schema()
    params = load_params()

    if not get_closed_trades_for_review(limit=1):
        print(f"[{datetime.now().isoformat()}] 迭代跳过：尚无已关闭交易")
        return False

    client = OllamaClient()
    if not client.is_alive():
        print("[迭代引擎] oMLX 不可用，跳过")
        return False

    print(f"[{datetime.now().isoformat()}] 迭代开始...")
    prompt = build_review_prompt()
    try:
        resp = client.chat([
            {"role": "system", "content": "你是一位专业的A股量化交易顾问。回答简洁专业，禁止废话。"},
            {"role": "user", "content": prompt}
        ], temperature=0.3, max_tokens=2000)
    except Exception as e:
        print(f"[迭代引擎] AI调用失败: {e}")
        return False

    delta = parse_params(resp)
    summary = _JSON_RE.sub('', resp).strip()[:400]

    params.iteration += 1
    params.last_iteration_at = datetime.now().isoformat()
    params.last_insight = summary[:200]
    if "min_confidence" in delta:
        params.min_confidence = int(delta["min_confidence"])
    if "closed_trades_threshold" in delta:
        params.closed_trades_threshold = int(delta["closed_trades_threshold"])
    save_params(params)

    log_iteration(params.iteration, len(get_closed_trades_for_review()),
                  summary, str(delta), resp[:500])

    changed = {k: v for k, v in delta.items() if k in [
        "short_stop_loss","short_take_profit","mid_stop_loss",
        "mid_take_profit","long_stop_loss","long_take_profit",
        "min_confidence","closed_trades_threshold"]}
    print(f"[{datetime.now().isoformat()}] 迭代 #{params.iteration} 完成")
    if changed:
        print(f"  参数变化: {changed}")
    print(f"  总结: {summary[:150]}")
    return True


if __name__ == "__main__":
    run_iteration()

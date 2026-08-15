"""全市场日线批量拉取（可断点续传，默认前复权）。"""
import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from research.data.loader import fetch_daily
from research.data.universe import get_universe


def _status_file(cache_dir: Path, adjust: str) -> Path:
    return cache_dir / f"_fetch_status_{adjust or 'raw'}.json"


def _failures_file(cache_dir: Path, adjust: str) -> Path:
    return cache_dir / f"_fetch_failures_{adjust or 'raw'}.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2014-01-01")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument("--adjust", default="qfq")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0, help="0=全部，>0 只拉前 N 只（调试用）")
    ap.add_argument("--retry", type=int, default=2)
    args = ap.parse_args()

    cache_dir = ROOT / "data" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    codes = get_universe({"universe": "all_a", "exclude_st": True})
    if args.limit:
        codes = codes[: args.limit]
    todo = [c for c in codes if not (cache_dir / f"{c}_{args.adjust}.csv").exists()]
    print(f"[fetch] 全市场候选 {len(codes)}，待拉取 {len(todo)}（{args.adjust} {args.start}~{args.end}，workers={args.workers}）", flush=True)

    failures = []
    done = 0
    lock = threading.Lock()
    t0 = time.time()

    def work(code: str):
        try:
            df = fetch_daily(code, args.start, args.end, args.adjust, args.retry)
            if df is None or df.empty:
                return code, "empty"
            df.to_csv(cache_dir / f"{code}_{args.adjust}.csv", index=False)
            return code, None
        except Exception as e:
            return code, str(e)[:200]

    def snapshot():
        elapsed = time.time() - t0
        rate = done / elapsed if elapsed > 0 else 0
        eta = (len(todo) - done) / rate / 60 if rate > 0 else None
        status = {
            "start": args.start,
            "end": args.end,
            "adjust": args.adjust,
            "workers": args.workers,
            "total": len(todo),
            "done": done,
            "failed": len(failures),
            "eta_minutes": round(eta, 1) if eta is not None else None,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        _status_file(cache_dir, args.adjust).write_text(json.dumps(status, ensure_ascii=False, indent=2))
        _failures_file(cache_dir, args.adjust).write_text(json.dumps(failures, ensure_ascii=False, indent=2))

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(work, c): c for c in todo}
            for i, fut in enumerate(as_completed(futs), 1):
                code, err = fut.result()
                with lock:
                    done += 1
                    if err:
                        failures.append({"code": code, "reason": err})
                    if done % 100 == 0 or done == len(todo):
                        snapshot()
                        print(f"[fetch] {done}/{len(todo)} 失败 {len(failures)} 耗时 {(time.time()-t0)/60:.1f}min", flush=True)
    except KeyboardInterrupt:
        print("[fetch] 中断，保存进度", flush=True)
    finally:
        snapshot()

    meta = {
        "start": args.start,
        "end": args.end,
        "adjust": args.adjust,
        "universe": "all_a_ex_st",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "fetched": done,
        "failed": len(failures),
    }
    (cache_dir / f"_snapshot_{args.adjust}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"[fetch] 完成：{done}/{len(todo)}，失败 {len(failures)}，耗时 {(time.time()-t0)/60:.1f}min", flush=True)


if __name__ == "__main__":
    main()

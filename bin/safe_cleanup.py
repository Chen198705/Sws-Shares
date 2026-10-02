#!/usr/bin/env python3
"""沈万三 Sws-Shares 安全清理脚本。

只删白名单路径，强制检查研究层红线路径不在删除列表里。
默认 dry-run，需要 --apply 才真正删除。

白名单（已确认可清）：
- stock-ai/_bak_vol_renamed_*/        9/23 旧备份（含死 venv + 旧 trading log）
- stock-ai-backups-20260813-cold/     8/13 冷备元数据

研究层红线（绝不动，脚本硬卡）：
- research/data/cache/                1.8GB EXP 数据
- research/experiments/EXP-*          实验结果
- research/logs/                      EXP 运行日志
- research/data/fetch_qfq.log         数据获取日志
- research/{factors,attribution,robustness,models,regime,export,configs,baselines,backtest,monitor}/
                                      研究层产物
- research/{README,daily_refresh.py,verify_landing.py,regularized.py,neutralize.py,run_experiment.py}
                                      研究层代码

用法：
  python3 bin/safe_cleanup.py               # dry-run，列出将删除的内容
  python3 bin/safe_cleanup.py --apply       # 真正删除
  python3 bin/safe_cleanup.py --whitelist   # 列出白名单
  python3 bin/safe_cleanup.py --redlines    # 列出研究层红线
"""
import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path('/Users/chenjianhui/AI/Sws-Shares')

# 白名单（glob 模式，相对 ROOT）
WHITELIST = [
    'stock-ai/_bak_vol_renamed_*',
    'stock-ai-backups-20260813-cold',
]

# 研究层红线（任何子路径匹配即拒绝执行）
REDLINES = [
    'research/data/cache',
    'research/data/fetch_qfq.log',
    'research/experiments',
    'research/logs',
    'research/factors',
    'research/attribution',
    'research/robustness',
    'research/models',
    'research/regime',
    'research/export',
    'research/configs',
    'research/baselines',
    'research/backtest',
    'research/monitor',
    'research/README.md',
    'research/daily_refresh.py',
    'research/verify_landing.py',
    'research/regularized.py',
    'research/neutralize.py',
    'research/run_experiment.py',
]


def expand_whitelist() -> list:
    out = []
    for pat in WHITELIST:
        matches = sorted(ROOT.glob(pat))
        if not matches:
            print(f'[warn] whitelist pattern matched nothing: {pat}', file=sys.stderr)
        out.extend(matches)
    return out


def check_redlines(targets: list) -> list:
    violations = []
    for t in targets:
        rel = t.relative_to(ROOT) if t.is_absolute() else t
        for rl in REDLINES:
            if str(rel).startswith(rl) or rl in str(rel):
                violations.append((t, rl))
    return violations


def human_size(n: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB']:
        if n < 1024:
            return f'{n:.1f}{unit}'
        n /= 1024
    return f'{n:.1f}TB'


def dir_size(p: Path) -> int:
    total = 0
    for f in p.rglob('*'):
        if f.is_file():
            total += f.stat().st_size
    return total


def cmd_dry_run():
    targets = expand_whitelist()
    if not targets:
        print('nothing to clean')
        return
    violations = check_redlines(targets)
    if violations:
        print('ABORT: redline violation detected', file=sys.stderr)
        for t, rl in violations:
            print(f'  {t}  matches redline  {rl}', file=sys.stderr)
        sys.exit(2)
    total = 0
    print(f'{"PATH":<70} {"SIZE":>10}')
    print('-' * 82)
    for t in targets:
        if t.exists():
            sz = dir_size(t) if t.is_dir() else t.stat().st_size
            total += sz
            print(f'{str(t.relative_to(ROOT)):<70} {human_size(sz):>10}')
    print('-' * 82)
    print(f'{"TOTAL":<70} {human_size(total):>10}')
    print()
    print('Run with --apply to actually delete.')


def cmd_apply():
    targets = expand_whitelist()
    violations = check_redlines(targets)
    if violations:
        print('ABORT: redline violation detected', file=sys.stderr)
        for t, rl in violations:
            print(f'  {t}  matches redline  {rl}', file=sys.stderr)
        sys.exit(2)
    for t in targets:
        if not t.exists():
            print(f'[skip] not found: {t}')
            continue
        sz = dir_size(t) if t.is_dir() else t.stat().st_size
        if t.is_dir():
            shutil.rmtree(t)
            print(f'[rmdir] {t.relative_to(ROOT)}  ({human_size(sz)})')
        else:
            t.unlink()
            print(f'[rm]    {t.relative_to(ROOT)}  ({human_size(sz)})')
    print('done')


def cmd_whitelist():
    for pat in WHITELIST:
        print(pat)


def cmd_redlines():
    for rl in REDLINES:
        print(rl)


def main():
    ap = argparse.ArgumentParser(description='沈万三安全清理脚本')
    ap.add_argument('--apply', action='store_true', help='真正删除（默认 dry-run）')
    ap.add_argument('--whitelist', action='store_true', help='列出白名单')
    ap.add_argument('--redlines', action='store_true', help='列出研究层红线')
    args = ap.parse_args()

    if args.whitelist:
        cmd_whitelist()
        return
    if args.redlines:
        cmd_redlines()
        return
    if args.apply:
        cmd_apply()
    else:
        cmd_dry_run()


if __name__ == '__main__':
    main()

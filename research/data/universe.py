"""Universe 构建：all_a / sample / list 三种模式。"""

import random
import akshare as ak


def _all_a_codes(exclude_st: bool = True):
    df = ak.stock_info_a_code_name()
    df = df.dropna(subset=["code", "name"])
    if exclude_st:
        df = df[~df["name"].str.contains("ST", na=False)]
    return df["code"].astype(str).str.zfill(6).tolist()


def get_universe(cfg: dict):
    mode = cfg.get("universe", "sample")
    if mode == "list":
        return [str(c).zfill(6) for c in cfg["codes"]]
    codes = _all_a_codes(cfg.get("exclude_st", True))
    if mode == "all_a":
        return codes
    seed = cfg.get("seed", 42)
    size = int(cfg.get("sample_size", 50))
    rng = random.Random(seed)
    return rng.sample(codes, min(size, len(codes)))

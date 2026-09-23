"""市场数据 - 腾讯行情 + Sina日K，无akshare依赖"""
import copy
import datetime, json
import threading
import time
import requests
import pandas as pd

TX_HEADERS = {"Referer": "https://finance.qq.com", "User-Agent": "Mozilla/5.0"}
SINA_HEADERS = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}

_http_local = threading.local()
_cache = {}
_cache_locks = {}
_cache_guard = threading.Lock()


def _http():
    """每线程独立 Session，行情源直连且避免 requests.Session 并发复用。"""
    session = getattr(_http_local, "session", None)
    if session is None:
        session = requests.Session()
        session.trust_env = False
        _http_local.session = session
    return session


def _clone_cache_value(value):
    if isinstance(value, pd.DataFrame):
        return value.copy(deep=True)
    if isinstance(value, (dict, list)):
        return copy.deepcopy(value)
    return value


def _cached_call(key, ttl: float, loader):
    """短 TTL 缓存 + single-flight，避免并发请求重复打行情源。"""
    now = time.monotonic()
    with _cache_guard:
        entry = _cache.get(key)
        if entry and entry[0] > now:
            return _clone_cache_value(entry[1])
        lock = _cache_locks.setdefault(key, threading.Lock())

    with lock:
        now = time.monotonic()
        with _cache_guard:
            entry = _cache.get(key)
            if entry and entry[0] > now:
                return _clone_cache_value(entry[1])
        value = loader()
        with _cache_guard:
            _cache[key] = (time.monotonic() + ttl, value)
        return _clone_cache_value(value)


def _fetch_quote_fields(symbols: list[str]) -> dict:
    """一次腾讯批量请求返回 {symbol: fields}。"""
    symbols = list(dict.fromkeys(symbols))
    if not symbols:
        return {}
    r = _http().get(
        "https://qt.gtimg.cn/q=" + ",".join(symbols),
        headers=TX_HEADERS,
        timeout=8,
    )
    raw = r.content.decode("gbk")
    result = {}
    for line in raw.split(";"):
        if '="' not in line:
            continue
        key, payload = line.split('="', 1)
        symbol = key.strip().removeprefix("v_")
        result[symbol] = payload.rstrip('";').split("~")
    return result


def _fetch_quote_fields_cached(symbols: list[str]) -> dict:
    symbols = list(dict.fromkeys(symbols))
    key = ("quotes", tuple(sorted(symbols)))
    return _cached_call(key, 2.0, lambda: _fetch_quote_fields(symbols))


def _parse_stock_fields(code: str, fields: list[str]) -> dict:
    if len(fields) < 40:
        return {"代码": code, "错误": f"字段不足({len(fields)})"}
    return {
        "股票名": fields[1],
        "代码": code,
        "最新价": float(fields[3]),
        "昨收": float(fields[4]),
        "今开": float(fields[5]),
        "最高": float(fields[33]),
        "最低": float(fields[34]),
        "成交量": float(fields[6] or 0),
        "成交额": float(fields[37] or 0),
        "涨跌额": float(fields[31]) if fields[31] else 0.0,
        "涨跌幅": float(fields[32]) if fields[32] else 0.0,
        "时间": fields[30],
        "换手率": float(fields[38]) if fields[38] else 0.0,
    }


def _prefix(code: str) -> str:
    return "sh" if code.startswith(("6", "5", "9")) else "sz"


def get_stocks_realtime(codes: list[str]) -> dict:
    """腾讯批量实时行情，含换手率。"""
    normalized = []
    for code in codes:
        code = str(code or "").strip()
        if code and code not in normalized:
            normalized.append(code)
    if not normalized:
        return {}
    symbol_to_code = {f"{_prefix(code)}{code}": code for code in normalized}
    try:
        fields_by_symbol = _fetch_quote_fields_cached(list(symbol_to_code))
    except Exception as e:
        return {code: {"代码": code, "错误": str(e)} for code in normalized}

    result = {}
    for symbol, code in symbol_to_code.items():
        fields = fields_by_symbol.get(symbol, [])
        try:
            result[code] = _parse_stock_fields(code, fields)
        except Exception as e:
            result[code] = {"代码": code, "错误": str(e)}
    return result


def get_stock_realtime(code: str) -> dict:
    """腾讯实时行情（单只；底层复用批量缓存）。"""
    return get_stocks_realtime([code]).get(code, {"代码": code, "错误": "未找到股票"})


def get_all_index_realtime() -> dict:
    INDEX_CODES = {
        "上证指数": "sh000001", "深证成指": "sz399001",
        "创业板指": "sz399006", "沪深300": "sh000300",
    }
    try:
        fields_by_symbol = _fetch_quote_fields_cached(list(INDEX_CODES.values()))
    except Exception:
        fields_by_symbol = {}
    result = {}
    for name, symbol in INDEX_CODES.items():
        fields = fields_by_symbol.get(symbol, [])
        if len(fields) > 32:
            result[name] = {
                "最新价": float(fields[3]),
                "涨跌幅": float(fields[32]) if fields[32] else 0.0,
            }
        else:
            result[name] = {"最新价": 0, "涨跌幅": 0, "错误": "获取失败"}
    return result


def _get_stock_history_uncached(code: str, days: int = 60, freq: str = 'day') -> pd.DataFrame:
    sym = f"{_prefix(code)}{code}"
    url = 'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData'
    SCALE_MAP = {'5m': 5, '15m': 15, '30m': 30, '60m': 60}
    DLEN_MAP  = {'5m': 240, '15m': 160, '30m': 120, '60m': 120}

    if freq in ('week', 'month'):
        params = {'symbol': sym, 'scale': 240, 'ma': 'no', 'datalen': max(days * 5, 600)}
        try:
            r = _http().get(url, params=params, headers=SINA_HEADERS, timeout=10)
            raw = r.json()
            if not raw:
                return pd.DataFrame()
            df = pd.DataFrame(raw)
            if 'day' in df.columns:
                df.rename(columns={'day': 'date'}, inplace=True)
            for col in ['open', 'high', 'low', 'close', 'volume']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date').reset_index(drop=True)
            grouper = df.groupby(pd.Grouper(key='date', freq='W' if freq == 'week' else 'ME'))
            agg = grouper.agg(
                date=('date', 'last'),
                open=('open', 'first'),
                high=('high', 'max'),
                low=('low', 'min'),
                close=('close', 'last'),
                volume=('volume', 'sum'),
            )
            agg['date'] = agg['date'].dt.strftime('%Y-%m-%d')
            agg = agg.dropna(subset=['open', 'close']).tail(days).reset_index(drop=True)
            agg['turnover_rate'] = 0.0
            return agg
        except Exception:
            return pd.DataFrame()

    scale = SCALE_MAP.get(freq, 240)
    datalen = DLEN_MAP.get(freq, days + 5)
    params = {'symbol': sym, 'scale': scale, 'ma': 'no', 'datalen': datalen}
    try:
        r = _http().get(url, params=params, headers=SINA_HEADERS, timeout=10)
        data = r.json()
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        if 'day' in df.columns:
            df.rename(columns={'day': 'date'}, inplace=True)
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        if not df.empty:
            df['turnover_rate'] = 0.0
        return df.tail(days).reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def get_stock_history(code: str, days: int = 60, freq: str = 'day', adjust: str = "") -> pd.DataFrame:
    """Sina K线；短线缓存 5 秒、日/周/月缓存 15 秒并做 single-flight。"""
    days = int(days)
    freq = freq or "day"
    ttl = 5.0 if freq in ("5m", "15m", "30m", "60m") else 15.0
    key = ("history", code, days, freq, adjust)
    return _cached_call(
        key,
        ttl,
        lambda: _get_stock_history_uncached(code, days, freq),
    )


def calc_indicators(df: pd.DataFrame) -> dict:
    """MA5/MA20/RSI(14)/MACD/KDJ(9,3,3)/成交量/换手率"""
    if df.empty or len(df) < 20:
        return {}

    close = df["close"]

    # MA
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()

    # RSI(14)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))

    # MACD(12,26,9)
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9).mean()
    hist = macd - signal

    # KDJ(9,3,3)
    low9 = df["low"].rolling(9).min()
    high9 = df["high"].rolling(9).max()
    rsv = (close - low9) / (high9 - low9 + 1e-9) * 100
    K = rsv.ewm(alpha=1/3).mean()
    D = K.ewm(alpha=1/3).mean()
    J = 3 * K - 2 * D

    last = df.iloc[-1]

    # 成交量
    vol_today = float(last.get("volume", 0))
    vol_ma5 = float(df["volume"].tail(5).mean())
    vol_ratio = vol_today / vol_ma5 if vol_ma5 > 0 else 1.0

    # 换手率
    turnover = float(last.get("turnover_rate", 0.0))

    return {
        "最新收盘": float(last["close"]),
        "今开": float(last.get("open", 0)),
        "最高": float(last["high"]),
        "最低": float(last["low"]),
        # 均线
        "MA5": float(ma5.iloc[-1]),
        "MA20": float(ma20.iloc[-1]),
        "均线多头": "是" if ma5.iloc[-1] > ma20.iloc[-1] else "否",
        # RSI
        "RSI(14)": float(rsi.iloc[-1]),
        "RSI状态": "超买" if rsi.iloc[-1] > 70 else ("超卖" if rsi.iloc[-1] < 30 else "正常"),
        # MACD
        "MACD": float(macd.iloc[-1]),
        "MACD_Signal": float(signal.iloc[-1]),
        "MACD金叉": "是" if hist.iloc[-1] > 0 and hist.iloc[-2] <= 0 else "否",
        "MACD状态": "多头" if hist.iloc[-1] > 0 else "空头",
        # KDJ
        "K": float(K.iloc[-1]),
        "D": float(D.iloc[-1]),
        "J": float(J.iloc[-1]),
        "KDJ金叉": "是" if K.iloc[-1] > D.iloc[-1] and K.iloc[-2] <= D.iloc[-2] else "否",
        "KDJ状态": "超买" if J.iloc[-1] > 80 else ("超卖" if J.iloc[-1] < 20 else "正常"),
        # 成交量 & 换手率
        "成交量": vol_today,
        "量比": round(vol_ratio, 2),
        "成交量状态": "放量" if vol_ratio > 1.5 else ("缩量" if vol_ratio < 0.7 else "正常"),
        "换手率": turnover,
    }


def get_turnover_rate(code: str) -> float:
    """从实时行情取换手率（%）"""
    stock = get_stock_realtime(code)
    return stock.get("换手率", 0.0)


def calc_volatility_profile(df: pd.DataFrame, atr_window: int = 20) -> dict:
    """
    波动率画像（用于波动率自适应止损止盈 + 仓位）：
    - atr_20:  N 日 ATR（True Range 的滚动均值，单位: 元）
    - atr_pct: ATR / 当前价  (如 0.025 表示 2.5% 日均振幅)
    - std_20:  20 日日收益率标准差
    - vol_rank: 在常见股票中的相对位置（启发式分数，0-1，>0.6 视为高波动）
    当样本不足或字段缺失时返回空 dict。
    """
    if df is None or df.empty or len(df) < max(atr_window, 20):
        return {}
    required = {"high", "low", "close"}
    if not required.issubset(set(df.columns)):
        return {}
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr_n = float(tr.rolling(atr_window).mean().iloc[-1])
    last_close = float(close.iloc[-1])
    atr_pct = atr_n / last_close if last_close > 0 else 0.0
    daily_ret = close.pct_change()
    std_n = float(daily_ret.rolling(atr_window).std().iloc[-1])
    # 启发式 vol_rank：A 股典型日振幅多在 1%-4% 之间；>4% 视为高波动，<1% 视为低波动
    if atr_pct <= 0:
        vol_rank = 0.5
    else:
        vol_rank = max(0.0, min(1.0, (atr_pct - 0.01) / 0.03))
    return {
        "atr_20": atr_n,
        "atr_pct": atr_pct,
        "std_20": std_n,
        "vol_rank": round(vol_rank, 3),
        "last_close": last_close,
    }


def build_entry_indicators(stock: dict, ind: dict, turnover: float = None) -> str:
    """实时行情 + 技术指标 + 换手率拼接成 entry_indicators"""
    if turnover is None:
        turnover = ind.get("换手率", 0.0)
    parts = [
        f"MA5={ind.get('MA5',0):.2f} MA20={ind.get('MA20',0):.2f} 均线多头={ind.get('均线多头','?')}",
        f"RSI={ind.get('RSI(14)',0):.1f} RSI状态={ind.get('RSI状态','?')}",
        f"MACD金叉={ind.get('MACD金叉','?')} MACD状态={ind.get('MACD状态','?')}",
        f"K={ind.get('K',0):.1f} D={ind.get('D',0):.1f} J={ind.get('J',0):.1f} "
        f"KDJ金叉={ind.get('KDJ金叉','?')} KDJ状态={ind.get('KDJ状态','?')}",
        f"量比={ind.get('量比',0):.2f} 成交量={ind.get('成交量',0):.0f} 成交量状态={ind.get('成交量状态','?')}",
        f"换手率={turnover:.2f}%",
        f"今开={stock.get('今开',0):.2f} 最高={stock.get('最高',0):.2f} 最低={stock.get('最低',0):.2f}",
    ]
    return " | ".join(parts)

# 兼容别名
get_all_indices = get_all_index_realtime

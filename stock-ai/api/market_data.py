"""市场数据 - 腾讯行情 + Sina日K，无akshare依赖"""
import datetime, requests, json
import pandas as pd

TX_HEADERS = {"Referer": "https://finance.qq.com", "User-Agent": "Mozilla/5.0"}
SINA_HEADERS = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}


def _prefix(code: str) -> str:
    return "sh" if code.startswith(("6", "5", "9")) else "sz"


def get_stock_realtime(code: str) -> dict:
    """腾讯实时行情，含换手率"""
    sym = f"{_prefix(code)}{code}"
    try:
        r = requests.get(f"https://qt.gtimg.cn/q={sym}", headers=TX_HEADERS, timeout=8)
        raw = r.content.decode("gbk")
        raw = raw.strip()
        if '="";' in raw or raw.endswith('=""'):
            return {"代码": code, "错误": f"未找到股票 {code}"}
        data = raw.split('="')[1].rstrip('";')
        f = data.split("~")
        if len(f) < 40:
            return {"代码": code, "错误": f"字段不足({len(f)})"}
        turnover = float(f[38]) if f[38] else 0.0  # 换手率%
        vol = float(f[6])         # 成交量（手）
        amount = float(f[37])    # 成交额（元）
        return {
            "股票名": f[1], "代码": code,
            "最新价": float(f[3]), "昨收": float(f[4]), "今开": float(f[5]),
            "最高": float(f[33]), "最低": float(f[34]),
            "成交量": vol, "成交额": amount,
            "涨跌额": float(f[31]) if f[31] else 0.0,
            "涨跌幅": float(f[32]) if f[31] else 0.0,
            "时间": f[30],
            "换手率": turnover,
        }
    except Exception as e:
        return {"代码": code, "错误": str(e)}


def get_all_index_realtime() -> dict:
    INDEX_CODES = {
        "上证指数": "sh000001", "深证成指": "sz399001",
        "创业板指": "sz399006", "沪深300": "sh000300",
    }
    result = {}
    for name, code in INDEX_CODES.items():
        sym = code
        try:
            r = requests.get(f"https://qt.gtimg.cn/q={sym}", headers=TX_HEADERS, timeout=8)
            raw = r.content.decode("gbk")
            raw = raw.strip()
            data = raw.split('="')[1].rstrip('";')
            f = data.split("~")
            if len(f) > 32:
                result[name] = {
                    "最新价": float(f[3]),
                    "涨跌幅": float(f[32]) if f[31] else 0.0,
                }
        except Exception:
            result[name] = {"最新价": 0, "涨跌幅": 0, "错误": "获取失败"}
    for name in INDEX_CODES:
        if name not in result:
            result[name] = {"最新价": 0, "涨跌幅": 0, "错误": "获取失败"}
    return result


def get_stock_history(code: str, days: int = 60, freq: str = 'day') -> pd.DataFrame:
    sym = f"{_prefix(code)}{code}"
    url = 'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData'
    SCALE_MAP = {'5m': 5, '15m': 15, '30m': 30, '60m': 60}
    DLEN_MAP  = {'5m': 240, '15m': 160, '30m': 120, '60m': 120}

    if freq in ('week', 'month'):
        params = {'symbol': sym, 'scale': 240, 'ma': 'no', 'datalen': max(days * 5, 600)}
        try:
            r = requests.get(url, params=params, headers=SINA_HEADERS, timeout=10)
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
        r = requests.get(url, params=params, headers=SINA_HEADERS, timeout=10)
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
        df = pd.DataFrame(data)
        df.columns = ["date", "open", "high", "low", "close", "volume"]
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        # 换手率通过腾讯日K补齐（最后一条）
        turnover = get_stock_realtime(code).get("换手率", 0.0)
        if not df.empty:
            df["turnover_rate"] = 0.0
            df.iloc[-1, df.columns.get_loc("turnover_rate")] = turnover
        return df.tail(days).reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


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

# -*- coding: utf-8 -*-
"""行情数据层 —— 专门解决"抓取失败/被限流":
 1. 本地缓存(CSV)：只补抓缺的几天，第二次运行基本不用联网
 2. 多数据源按顺序回退：新浪(akshare) → 东方财富(akshare) → Yahoo 图表接口 → yfinance → Stooq
 3. 限流退避：指数退避 + 抖动；同一数据源本轮连续失败 3 次就"熔断"跳过，不再浪费时间
 4. 全部失败时用过期缓存兜底(界面会标"数据过期N天")，不会整份清单作废
 5. 任意标的都可手动导入 CSV
"""
import os, io, time, random, datetime as dt, threading
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

from common import data_dir
import symbols

COLS = ["Open", "High", "Low", "Close", "Volume"]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


class RateLimited(Exception):
    pass


class NoData(Exception):
    pass


def _cache_path(sym):
    d = os.path.join(data_dir(), "cache")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, sym.upper() + ".csv")


def load_cache(sym):
    p = _cache_path(sym)
    if not os.path.isfile(p):
        return None
    try:
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        return df[COLS].astype(float).sort_index()
    except Exception:
        return None


def save_cache(sym, df):
    df.tail(700).to_csv(_cache_path(sym))


def normalize(raw, colmap=None):
    df = raw.copy()
    if colmap:
        df = df.rename(columns=colmap)
    low = {c.lower(): c for c in df.columns}
    for want in ["date"] + COLS:
        if want not in df.columns and want.lower() in low:
            df = df.rename(columns={low[want.lower()]: want})
    if "date" in df.columns:
        df = df.set_index("date")
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df = df[COLS].apply(pd.to_numeric, errors="coerce").dropna(subset=["Open", "High", "Low", "Close"])
    df["Volume"] = df["Volume"].fillna(0)
    return df[~df.index.duplicated(keep="last")].sort_index()


def last_closed_session(now=None):
    """美股最近一个已收盘的交易日(忽略节假日)。"""
    ny = (now or dt.datetime.now(ZoneInfo("America/New_York")))
    d = ny.date()
    if ny.weekday() < 5 and ny.time() >= dt.time(16, 30):
        return d
    d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


# ------------------------------------------------------------------ 各数据源(每个返回标准化日线 DataFrame)
def src_akshare(sym):
    """akshare 分市场: 美股(新浪) / 港股 / A股(新浪)。"""
    import akshare as ak
    m = symbols.normalize(sym)["market"]
    if m == "US":
        return normalize(ak.stock_us_daily(symbol=sym, adjust=""))
    if m == "HK":
        return normalize(ak.stock_hk_daily(symbol=sym[2:], adjust=""))
    return normalize(ak.stock_zh_a_daily(symbol=sym.lower(), adjust=""))


def yahoo_code(key):
    n = symbols.normalize(key)
    if n["market"] == "HK":
        return str(int(key[2:])).zfill(4) + ".HK"
    if n["market"] == "CN":
        return key[2:] + (".SS" if key[:2] == "SH" else ".SZ")
    return key


def src_akshare_em(sym):
    if symbols.normalize(sym)["market"] != "US":
        raise NoData("仅美股")
    import akshare as ak
    start = (dt.date.today() - dt.timedelta(days=1200)).strftime("%Y%m%d")
    end = dt.date.today().strftime("%Y%m%d")
    cn = {"日期": "date", "开盘": "Open", "最高": "High", "最低": "Low", "收盘": "Close", "成交量": "Volume"}
    last = None
    for prefix in ("105", "106", "107"):
        try:
            raw = ak.stock_us_hist(symbol=f"{prefix}.{sym}", period="daily", start_date=start, end_date=end, adjust="")
            if len(raw):
                return normalize(raw, cn)
        except Exception as ex:
            last = ex
    raise last or NoData("东方财富无数据")


def src_yahoo_chart(sym):
    """直接请求 Yahoo 图表 JSON 接口(比 yfinance 轻, 限流门槛不同)。"""
    import requests
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_code(sym)}",
                     params={"range": "3y", "interval": "1d", "includePrePost": "false"}, headers=UA, timeout=15)
    if r.status_code == 429:
        raise RateLimited("Yahoo 429")
    r.raise_for_status()
    res = r.json()["chart"]["result"]
    if not res:
        raise NoData("Yahoo 无数据")
    res = res[0]
    q = res["indicators"]["quote"][0]
    tz = res.get("meta", {}).get("exchangeTimezoneName", "America/New_York")
    idx = pd.to_datetime(res["timestamp"], unit="s", utc=True).tz_convert(tz).tz_localize(None)
    df = pd.DataFrame({"Open": q["open"], "High": q["high"], "Low": q["low"], "Close": q["close"], "Volume": q["volume"]}, index=idx)
    return normalize(df)


def src_yfinance(sym):
    """已从默认顺序移除(限流最严重)。保留给手动排查用。"""
    import yfinance as yf
    try:
        df = yf.Ticker(sym).history(period="3y", interval="1d", auto_adjust=False)
    except Exception as ex:
        if "RateLimit" in type(ex).__name__ or "Too Many" in str(ex):
            raise RateLimited(str(ex))
        raise
    if df is None or df.empty:
        raise NoData("yfinance 无数据")
    return normalize(df)


def src_stooq(sym):
    if symbols.normalize(sym)["market"] != "US":
        raise NoData("仅美股")
    import requests
    r = requests.get("https://stooq.com/q/d/l/", params={"s": f"{sym.lower()}.us", "i": "d"}, headers=UA, timeout=15)
    r.raise_for_status()
    t = r.text.strip()
    if not t.lower().startswith("date"):
        raise NoData("Stooq 无数据/需要验证")
    return normalize(pd.read_csv(io.StringIO(t)))


SOURCES = {
    "akshare": src_akshare,
    "akshare东财": src_akshare_em,
    "Stooq": src_stooq,
    "Yahoo图表": src_yahoo_chart,
}
DEFAULT_ORDER = ["akshare", "akshare东财", "Stooq", "Yahoo图表"]


class Fetcher:
    """一轮扫描用一个实例：保存各数据源的熔断状态。"""

    def __init__(self, order=None, sources=None, min_gap=1.0, retries=2, backoff=8.0, sleep=time.sleep):
        self.order = order or DEFAULT_ORDER
        self.sources = sources or SOURCES
        self.min_gap, self.retries, self.backoff, self.sleep = min_gap, retries, backoff, sleep
        self.fail = {k: 0 for k in self.order}
        self.dead = set()
        self.last_call = {}
        self.lock = threading.Lock()

    def _throttle(self, name):
        gap = self.min_gap * random.uniform(0.8, 1.4)
        wait = self.last_call.get(name, 0) + gap - time.time()
        if wait > 0:
            self.sleep(wait)
        self.last_call[name] = time.time()

    def get(self, sym, log=None):
        """返回 (df, 来源名, 错误列表)。df 为 None 表示全部失败。"""
        errs = []
        for name in self.order:
            if name in self.dead:
                errs.append(f"{name}:本轮已熔断")
                continue
            fn = self.sources.get(name)
            if not fn:
                continue
            for attempt in range(self.retries + 1):
                self._throttle(name)
                try:
                    df = fn(sym)
                    if df is None or len(df) < 5:
                        raise NoData("数据太少")
                    self.fail[name] = 0
                    return df, name, errs
                except ImportError as ex:
                    errs.append(f"{name}:未安装({ex.name})")
                    self.dead.add(name)
                    break
                except RateLimited:
                    # 被限流: 不再傻等重试, 这个源本轮直接跳过, 换下一个源
                    errs.append(f"{name}:限流")
                    if log:
                        log(f"{sym} {name} 被限流，本轮不再尝试该源，改用下一个数据源")
                    self.dead.add(name)
                    break
                except NoData as ex:
                    errs.append(f"{name}:{ex}")
                    break          # 该源没有这个标的, 不重试, 换下一个源
                except Exception as ex:
                    errs.append(f"{name}:{type(ex).__name__}")
                    if attempt < self.retries:
                        self.sleep(1.5 * (attempt + 1))
                        continue
                    self._bad(name)
                    break
        return None, None, errs

    def _bad(self, name):
        self.fail[name] += 1
        if self.fail[name] >= 3:
            self.dead.add(name)


def update_symbol(sym, fetcher, force=False, log=None):
    """取一只标的的数据: 缓存优先 → 只在需要时联网 → 失败用过期缓存兜底。
    返回 (df|None, info dict)"""
    cached = load_cache(sym)
    need = last_closed_session()
    info = {"sym": sym, "source": "", "errors": [], "stale_days": None, "last": None, "status": ""}
    if cached is not None and len(cached):
        last = cached.index[-1].date()
        checked = os.path.getmtime(_cache_path(sym))
        recent = (time.time() - checked) < 6 * 3600
        if not force and (last >= need or recent):
            info.update(source="缓存", status="cache", last=str(last), stale_days=max(0, (need - last).days))
            return cached, info
    df, name, errs = fetcher.get(sym, log)
    info["errors"] = errs
    if df is not None:
        if cached is not None:
            df = pd.concat([cached, df])
            df = df[~df.index.duplicated(keep="last")].sort_index()
        save_cache(sym, df)
        info.update(source=name, status="fresh", last=str(df.index[-1].date()),
                    stale_days=max(0, (need - df.index[-1].date()).days))
        return df, info
    if cached is not None and len(cached):
        last = cached.index[-1].date()
        os.utime(_cache_path(sym))  # 避免马上又重试
        info.update(source="过期缓存", status="stale", last=str(last), stale_days=max(0, (need - last).days))
        return cached, info
    info["status"] = "fail"
    return None, info


def import_csv(sym, text):
    """手动导入 CSV(列: Date,Open,High,Low,Close,Volume; Yahoo/Stooq/国内导出格式均可)。"""
    raw = pd.read_csv(io.StringIO(text))
    cn = {"日期": "date", "开盘": "Open", "最高": "High", "最低": "Low", "收盘": "Close", "成交量": "Volume"}
    df = normalize(raw, cn)
    if len(df) < 30:
        raise ValueError("CSV 里有效行数太少(至少 30 行日线)")
    old = load_cache(sym)
    if old is not None:
        df = pd.concat([old, df])
        df = df[~df.index.duplicated(keep="last")].sort_index()
    save_cache(sym, df)
    return len(df)


def diagnose(sym="SPY"):
    """逐个数据源测试一只标的, 返回 [(名称, 是否成功, 说明, 耗时秒)] 。给「数据源诊断」按钮用。"""
    out = []
    for name in DEFAULT_ORDER:
        fn = SOURCES[name]
        t = time.time()
        try:
            df = fn(sym)
            if df is None or len(df) < 5:
                raise NoData("返回数据太少")
            out.append((name, True, f"成功，共 {len(df)} 根日线，最新 {df.index[-1].date()}", time.time() - t))
        except ImportError as ex:
            out.append((name, False, f"未安装：{ex.name}（请运行 start.bat 的依赖安装，或 pip install {ex.name}）", time.time() - t))
        except RateLimited as ex:
            out.append((name, False, "被限流（IP 被临时限制，稍后再试，或换网络）", time.time() - t))
        except NoData as ex:
            out.append((name, False, f"没有数据：{ex}", time.time() - t))
        except Exception as ex:
            out.append((name, False, f"连接失败：{type(ex).__name__} {str(ex)[:120]}", time.time() - t))
    return out

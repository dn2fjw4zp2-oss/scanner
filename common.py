# -*- coding: utf-8 -*-
import os, sys, json

APP_NAME = "智能扫描官"
VERSION = "3.0.0"


def base_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir():
    d = os.environ.get("SCAN_DATA_DIR") or os.path.join(base_dir(), "data")
    os.makedirs(d, exist_ok=True)
    return d


def jload(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def jsave(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


DEFAULT_TICKERS = [
    "SOXL", "SPY", "QQQ", "SOXS", "KORU", "SNXX", "DRAM", "EWY",
    "NVDA", "META", "MSFT", "AAPL", "AMZN", "GOOGL", "TSLA",
    "AMD", "AVGO", "TSM", "MU", "MRVL", "INTC", "ARM", "SNDK", "SKHY", "LITE", "DELL",
    "MSTR", "CRCL", "NBIS", "HOOD", "PLTR", "SPCX", "CBRS", "ORCL", "BE", "RKLB",
]

# 参数全部可在界面里改; key -> (默认值, 中文名, 说明)
PARAMS = {
    "capital_cny": (5000, "本金(人民币)", ""),
    "usd_cny": (7.1, "汇率 USD/CNY", "按当天实际改"),
    "max_loss_pct": (0.20, "单笔最大亏损占本金", "你原来设的 20%。连续亏几次会很快打穿账户，见界面提示"),
    "hkd_cny": (0.92, "港币汇率 HKD/CNY", "按当天实际改，用于换算港股仓位"),
    "leverage": (3, "总杠杆上限", "所有持仓名义仓位之和 ≤ 本金×杠杆"),
    "max_positions": (2, "最多同时持仓数", "杠杆额度按这个数平分；清单前 N 个标「执行」"),
    "daily_loss_stop_pct": (0.30, "当日累计亏损停手线", "占本金比例"),
    "top_n": (10, "每天最多输出几个", ""),
    "min_score": (3, "最低分数", "低于不出信号"),
    "atr_n": (14, "ATR 周期", ""),
    "breakout_days": (10, "突破前 N 日高/低点", ""),
    "vol_mult": (1.5, "放量倍数", "对比近 20 日均量"),
    "tp1_r": (1.5, "第一止盈(倍止损距离)", ""),
    "min_stop_pct": (0.005, "止损距离下限", "小于它视为太紧，跳过"),
    "regime_penalty": (1, "逆大盘扣分", "SPY 在 50 日线下方时做多扣分(反之做空扣分)；0=关闭"),
    "corr_limit": (0.85, "持仓相关性上限", "两个「执行」标的近 60 日收益相关性超过它，后者降为备选；1=关闭"),
    "require_backtest": (0, "只保留历史验证通过的信号", "1=开启：同规则历史期望值≤0 的信号不出"),
    "max_stale_days": (4, "数据最多允许过期几天", "超过就不出信号(数据太旧不能交易)"),
    "push_key": ("", "Server酱推送 SendKey", "填了之后，出现新的「执行」信号会推送到你的微信。留空=不推送"),
    "request_gap": (1.5, "请求间隔(秒)", "每次取数之间等待，越大越不容易被限流，但全部取完更慢"),
    "auto_scan_time": ("05:30", "每日自动扫描时间(北京时间)", "美股收盘后(北京时间约 04:00-05:00)之后即可。每天第一次打开软件且已过该时间，会自动扫描一次；留空=关闭"),
}

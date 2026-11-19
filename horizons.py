# -*- coding: utf-8 -*-
"""短线 / 长线 两套独立的做多规则, 各自带历史回测(合并全部标的统计)。
只做规则演算, 不保证盈利, 不构成投资建议。
胜率过滤: 大盘硬过滤(SPY 在 50 日线上且向上) + 不追高(收盘不超过 20 日线 12%) + 相对强度(跑赢 SPY) + 量能确认。"""
import numpy as np
import pandas as pd
import engine as E
import symbols

HZ = {
    "short": {"name": "短线", "hold": 10, "tp_r": 1.5, "lev": 5,
              "desc": "持有最多 10 个交易日。条件：站上向上的 20 日线；放量突破 10 日高点；RSI 在 50–75；近 20 日跑赢 SPY；大盘在 50 日线上方且向上；不追高。"},
    "long": {"name": "长线", "hold": 60, "tp_r": 3.0, "lev": 3,
             "desc": "持有最多 60 个交易日（数周到数月）。条件：收盘 > 50 日线 > 200 日线且 50 日线向上；近 60 日涨幅为正且跑赢 SPY；回踩 50 日线后收回 20 日线，或放量突破 55 日高点；RSI < 80；大盘在 50 日线上方且向上。"},
}
MIN_BARS = 260   # 需要至少 ~1 年数据(200 日线 + 回测空间)


def _build(df, P, spy_df):
    X = E.indicators(df, P)
    c = X["c"]
    X["sma200"] = c.rolling(200).mean()
    X["hi55"] = X["h"].shift(1).rolling(55).max()
    X["ret20"] = c / c.shift(20) - 1
    X["ret60"] = c / c.shift(60) - 1
    if spy_df is not None:
        sc = spy_df["Close"]
        s = sc.reindex(X.index, method="ffill")
        X["spy20"] = s / s.shift(20) - 1
        X["spy60"] = s / s.shift(60) - 1
        ssma = sc.rolling(50).mean()
        reg = (sc > ssma) & (ssma > ssma.shift(5))
        X["reg"] = reg.reindex(X.index, method="ffill").fillna(False).astype(bool)
    else:
        X["spy20"] = np.nan
        X["spy60"] = np.nan
        X["reg"] = False
    return X


def _signals(X, P, h):
    c, o, l = X["c"], X["o"], X["l"]
    ext = c <= X["sma20"] * 1.12                     # 不追高
    if h == "short":
        base = (c > X["sma20"]) & (X["sma20"] > X["sma20"].shift(5))
        trig = (c > X["hi_n"]) & (X["volr"] >= P["vol_mult"])
        filt = X["rsi"].between(50, 75) & (X["ret20"] > X["spy20"])
    else:
        base = (c > X["sma50"]) & (X["sma50"] > X["sma200"]) & (X["sma50"] > X["sma50"].shift(10))
        pull = (l.rolling(3).min() <= X["sma50"] * 1.02) & (c > X["sma20"]) & (c > o)
        brk = (c > X["hi55"]) & (X["volr"] >= P["vol_mult"])
        trig = pull | brk
        filt = (X["rsi"] < 80) & (X["ret60"] > 0) & (X["ret60"] > X["spy60"])
    sig = base & trig & filt & ext & X["reg"]
    return sig.fillna(False).astype(bool)


def _bt(X, sig, hold, tp_r, P):
    """同一规则的历史表现: 信号当日收盘后判定 -> 次日触发进场 -> 止损 / 止盈 / 超时。单位 R。
    保守假设: 同日既碰止损又碰止盈按止损算; 不计手续费滑点。"""
    P2 = {**P, "tp1_r": tp_r}
    n, R, last = len(X), [], -1
    for i in np.flatnonzero(sig.values):
        if i <= last or i >= n - 1:
            continue
        lv = E.levels(X, i, "做多", P2)
        if not lv:
            continue
        nx = X.iloc[i + 1]
        if nx["h"] < lv["entry"]:
            continue
        fill = max(nx["o"], lv["entry"])
        risk = fill - lv["stop"]
        if risk <= 0:
            continue
        res, ex = None, min(n - 1, i + 1 + hold)
        for j in range(i + 1, min(n, i + 2 + hold)):
            row = X.iloc[j]
            if j > i + 1 and row["o"] <= lv["stop"]:
                res, ex = (row["o"] - fill) / risk, j
                break
            if row["l"] <= lv["stop"]:
                res, ex = -1.0, j
                break
            if row["h"] >= lv["tp1"]:
                res, ex = (lv["tp1"] - fill) / risk, j
                break
        if res is None:
            res = (X["c"].iloc[ex] - fill) / risk
        R.append(float(res))
        last = ex
    return R


def _lev_plan(lv, P, lev_target):
    """从目标杠杆开始, 逐级降低, 直到 止损 离 强平价 有足够缓冲(止损距离 <= 强平距离的60%)。
    强平价近似: 进场价 × (1 - 1/杠杆 + 维持保证金率)。达不到最低 3 倍则返回 None(不出信号)。"""
    mm = P.get("maint_margin", 0.005)
    lev = min(float(lev_target), float(P.get("lev_max", 10)))
    while lev >= 3:
        liq = lv["entry"] * (1 - 1 / lev + mm)
        if lv["entry"] - lv["stop"] <= (lv["entry"] - liq) * 0.6:
            return lev, liq
        lev -= 1
    return None, None


def _size(sym, lv, P, lev):
    info = symbols.describe(sym)
    fx = symbols.fx_to_usd(info["ccy"], P)
    cap = P["capital_cny"] / P["usd_cny"]
    q_risk = cap * P["max_loss_pct"] / (lv["risk"] * fx)
    q_lev = cap * lev / P["max_positions"] / (lv["entry"] * fx)
    qty = min(q_risk, q_lev)
    bound = "杠杆上限" if q_lev <= q_risk else "风险上限"
    note = ""
    if info["lot"] > 1:
        qty = (int(qty) // info["lot"]) * info["lot"]
        if qty < info["lot"]:
            note = f"资金不足一手({info['lot']}股)，按此价格无法下单"
    return {"qty": float(qty), "notional": float(qty * lv["entry"] * fx),
            "risk_usd": float(qty * lv["risk"] * fx), "risk_pct": float(qty * lv["risk"] * fx / cap * 100),
            "bound": bound, "ccy": info["ccy"], "lot": info["lot"], "market": info["market"], "note": note}


def _today(sym, X, sig, P, h):
    if not bool(sig.iloc[-1]):
        return None
    cfg = HZ[h]
    i = len(X) - 1
    lv = E.levels(X, i, "做多", {**P, "tp1_r": cfg["tp_r"]})
    if not lv:
        return None
    last = X.iloc[i]
    if h == "short":
        why = f"放量突破{P['breakout_days']}日高点(量比{last['volr']:.1f})；RSI {last['rsi']:.0f}；近20日跑赢SPY"
    else:
        pull = bool(((X["l"].rolling(3).min() <= X["sma50"] * 1.02) & (X["c"] > X["sma20"]) & (X["c"] > X["o"])).iloc[-1])
        how = "回踩50日线企稳收阳" if pull else "放量突破55日高点"
        why = f"50日线>200日线且向上；{how}；近60日涨幅{last['ret60']*100:.0f}%跑赢SPY"
    lev, liq = _lev_plan(lv, P, cfg["lev"])
    if lev is None:
        return None
    sz = _size(sym, lv, P, lev)
    exec_max = lv["entry"] * (1 + P.get("chase_pct", 0.003))
    out = {"sym": sym, "side": "做多", "why": why, "last": float(last["c"]),
           "entry": float(lv["entry"]), "exec_max": float(exec_max), "lev": float(lev), "liq": float(liq),
           "stop": float(lv["stop"]), "tp1": float(lv["tp1"]),
           "stop_pct": float(lv["risk"] / lv["entry"] * 100), "asof": str(X.index[-1].date()),
           "rsi": float(last["rsi"]), "volr": float(last["volr"]), "hold": cfg["hold"]}
    out.update(sz)
    return out


def run(frames, P, spy_df=None):
    out = {}
    for h, cfg in HZ.items():
        sigs, pooled, by_sym = [], [], {}
        for sym, df in frames.items():
            if sym == "SPY":
                continue
            try:
                X = _build(df, P, spy_df)
                if len(X) < MIN_BARS:
                    by_sym[sym] = {"n": 0, "note": "历史不足1年，不纳入"}
                    continue
                sig = _signals(X, P, h)
                R = _bt(X, sig, cfg["hold"], cfg["tp_r"], P)
            except Exception as ex:
                by_sym[sym] = {"n": 0, "note": f"计算出错：{ex}"}
                continue
            pooled += R
            by_sym[sym] = E.bt_stats(R)
            t = _today(sym, X, sig, P, h)
            if t:
                t["bt"] = by_sym[sym]
                sigs.append(t)
        sigs.sort(key=lambda r: (-(r["bt"].get("avg_r") or -9), r["stop_pct"]))
        out[h] = {"name": cfg["name"], "desc": cfg["desc"], "hold": cfg["hold"], "tp_r": cfg["tp_r"],
                  "signals": sigs, "bt": E.bt_stats(pooled), "bt_by_sym": by_sym}
    return out

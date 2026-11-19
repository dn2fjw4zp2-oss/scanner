# -*- coding: utf-8 -*-
"""分析引擎: 沿用原脚本的打分规则(向量化), 新增 历史回测 / 大盘过滤 / 相关性选仓。
只做规则演算, 不保证盈利, 不构成投资建议。"""
import numpy as np
import pandas as pd
from core import symbols


def indicators(df, P):
    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"]).copy()
    o, h, l, c, v = (df[k] for k in ["Open", "High", "Low", "Close", "Volume"])
    X = pd.DataFrame(index=df.index)
    X["o"], X["h"], X["l"], X["c"], X["v"] = o, h, l, c, v
    X["sma5"] = c.rolling(5).mean()
    X["sma20"] = c.rolling(20).mean()
    X["sma50"] = c.rolling(50).mean()
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    X["atr"] = tr.rolling(int(P["atr_n"])).mean()
    X["volr"] = v / v.rolling(20).mean().shift(1)
    n = int(P["breakout_days"])
    X["hi_n"] = h.shift(1).rolling(n).max()
    X["lo_n"] = l.shift(1).rolling(n).min()
    d = c.diff()
    g = d.clip(lower=0).rolling(14).mean()
    ls = (-d.clip(upper=0)).rolling(14).mean()
    X["rsi"] = 100 - 100 / (1 + g / ls.replace(0, np.nan))
    return X


def scores(X, P):
    """逐日的 做多分/做空分 及理由代码。与原 analyze() 规则一致。"""
    c, o, h, l = X["c"], X["o"], X["h"], X["l"]
    vm = P["vol_mult"]
    up = (c > X["sma20"]) & (X["sma20"] > X["sma20"].shift(5))
    dn = (c < X["sma20"]) & (X["sma20"] < X["sma20"].shift(5))
    near_l = (l <= X["sma20"] * 1.01) | (l.shift(1) <= X["sma20"].shift(1) * 1.01)
    near_s = (h >= X["sma20"] * 0.99) | (h.shift(1) >= X["sma20"].shift(1) * 0.99)
    bu, bd = c > X["hi_n"], c < X["lo_n"]
    heavy = X["volr"] >= vm

    ls = (2 * up + 1 * (up & (X["sma20"] > X["sma50"])) + 3 * (bu & heavy) + 1 * (bu & ~heavy)
          + 2 * (up & near_l & (c > o)) - 2 * (X["rsi"] > 80)).astype(float)
    ss = (2 * dn + 1 * (dn & (X["sma20"] < X["sma50"])) + 3 * (bd & heavy) + 1 * (bd & ~heavy)
          + 2 * (dn & near_s & (c < o)) - 2 * (X["rsi"] < 20)).astype(float)
    flags = {"up": up, "dn": dn, "bu": bu, "bd": bd, "heavy": heavy, "near_l": near_l, "near_s": near_s}
    return ls, ss, flags


def reasons(X, flags, i, side, P):
    n = int(P["breakout_days"])
    r = []
    f = {k: bool(v.iloc[i]) for k, v in flags.items()}
    vr = X["volr"].iloc[i]
    if side == "做多":
        if f["up"]:
            r.append("站上向上的20日线")
            if X["sma20"].iloc[i] > X["sma50"].iloc[i]:
                r.append("20日线在50日线上方")
        if f["bu"]:
            r.append(f"放量突破{n}日高点(量比{vr:.1f})" if f["heavy"] else f"突破{n}日高点但量不足")
        if f["up"] and f["near_l"] and X["c"].iloc[i] > X["o"].iloc[i]:
            r.append("回踩20日线企稳收阳")
        if X["rsi"].iloc[i] > 80:
            r.append("RSI>80过热扣分")
    else:
        if f["dn"]:
            r.append("跌破向下的20日线")
            if X["sma20"].iloc[i] < X["sma50"].iloc[i]:
                r.append("20日线在50日线下方")
        if f["bd"]:
            r.append(f"放量跌破{n}日低点(量比{vr:.1f})" if f["heavy"] else f"跌破{n}日低点但量不足")
        if f["dn"] and f["near_s"] and X["c"].iloc[i] < X["o"].iloc[i]:
            r.append("反弹20日线受阻收阴")
        if X["rsi"].iloc[i] < 20:
            r.append("RSI<20超卖扣分")
    return r


def levels(X, i, side, P):
    """进场/止损/止盈。i 为信号日下标。返回 dict 或 None。"""
    a = X["atr"].iloc[i]
    if not np.isfinite(a) or a <= 0:
        return None
    hi, lo = X["h"].iloc[i], X["l"].iloc[i]
    if side == "做多":
        entry = hi * 1.001
        stop = max(entry - a, X["l"].iloc[max(0, i - 2):i + 1].min() * 0.999)
        if stop >= entry:
            stop = entry - a
        risk = entry - stop
        tp1 = entry + P["tp1_r"] * risk
    else:
        entry = lo * 0.999
        stop = min(entry + a, X["h"].iloc[max(0, i - 2):i + 1].max() * 1.001)
        if stop <= entry:
            stop = entry + a
        risk = stop - entry
        tp1 = entry - P["tp1_r"] * risk
    if risk <= 0 or risk / entry < P["min_stop_pct"]:
        return None
    return {"entry": entry, "stop": stop, "tp1": tp1, "risk": risk}


# ------------------------------------------------------------------ 回测(同一套规则, 看历史上"这类信号"表现)
def backtest(X, P, ls, ss, hold=10):
    """逐日: 分数>=门槛 -> 次日触发进场 -> 止损/止盈1/超时离场。结果用 R(止损距离的倍数)表示。
    简化假设(会偏乐观, 仅作参考): 同日既触及止损又触及止盈按止损算; 止盈1一次性全平; 不计手续费/滑点。"""
    ms = P["min_score"]
    R = []
    n = len(X)
    last_exit = -1
    for i in range(55, n - 1):
        if i <= last_exit:
            continue
        a, b = ls.iloc[i], ss.iloc[i]
        if max(a, b) < ms or a == b:
            continue
        side = "做多" if a > b else "做空"
        lv = levels(X, i, side, P)
        if not lv:
            continue
        # 次日是否触发
        nx = X.iloc[i + 1]
        if side == "做多":
            if nx["h"] < lv["entry"]:
                continue
            fill = max(nx["o"], lv["entry"])
        else:
            if nx["l"] > lv["entry"]:
                continue
            fill = min(nx["o"], lv["entry"])
        risk = abs(fill - lv["stop"])
        if risk <= 0:
            continue
        sgn = 1 if side == "做多" else -1
        res, exit_i = None, min(n - 1, i + 1 + hold)
        for j in range(i + 1, min(n, i + 2 + hold)):
            row = X.iloc[j]
            if j == i + 1:      # 进场当天: 只看进场之后能否直接打止损(保守处理)
                hit_stop = (row["l"] <= lv["stop"]) if side == "做多" else (row["h"] >= lv["stop"])
                hit_tp = (row["h"] >= lv["tp1"]) if side == "做多" else (row["l"] <= lv["tp1"])
            else:
                hit_stop = (row["l"] <= lv["stop"]) if side == "做多" else (row["h"] >= lv["stop"])
                hit_tp = (row["h"] >= lv["tp1"]) if side == "做多" else (row["l"] <= lv["tp1"])
            if j > i + 1:       # 跳空越过止损 -> 按开盘价离场
                gap_stop = (row["o"] <= lv["stop"]) if side == "做多" else (row["o"] >= lv["stop"])
                if gap_stop:
                    res, exit_i = sgn * (row["o"] - fill) / risk, j
                    break
            if hit_stop:
                res, exit_i = -1.0, j
                break
            if hit_tp:
                res, exit_i = sgn * (lv["tp1"] - fill) / risk, j
                break
        if res is None:
            res = sgn * (X["c"].iloc[exit_i] - fill) / risk
        R.append(float(res))
        last_exit = exit_i
    return R


def bt_stats(R):
    n = len(R)
    if n == 0:
        return {"n": 0, "win": None, "avg_r": None, "pf": None, "max_dd_r": None, "ok": None}
    a = np.array(R)
    wins, losses = a[a > 0].sum(), -a[a < 0].sum()
    eq = np.cumsum(a)
    dd = float((np.maximum.accumulate(eq) - eq).max())
    return {"n": n, "win": float((a > 0).mean()), "avg_r": float(a.mean()),
            "pf": float(wins / losses) if losses > 0 else None, "max_dd_r": dd,
            "ok": bool(a.mean() > 0)}


# ------------------------------------------------------------------ 单标的 & 全体
def regime(frames, P):
    """大盘状态: 用 SPY(没有则 QQQ) 的 收盘 vs 50 日线。"""
    for s in ("SPY", "QQQ"):
        df = frames.get(s)
        if df is not None and len(df) > 60:
            c = df["Close"]
            sma = c.rolling(50).mean()
            slope = sma.iloc[-1] - sma.iloc[-6]
            up = c.iloc[-1] > sma.iloc[-1]
            tag = "偏多" if (up and slope > 0) else "偏空" if ((not up) and slope < 0) else "震荡"
            return {"sym": s, "state": tag, "close": float(c.iloc[-1]), "sma50": float(sma.iloc[-1])}
    return {"sym": None, "state": "未知"}


def analyze(sym, df, P, reg=None, with_bt=True):
    if df is None or len(df) < 60:
        return None
    X = indicators(df, P)
    if len(X) < 60 or not np.isfinite(X["atr"].iloc[-1]):
        return None
    ls, ss, flags = scores(X, P)
    i = len(X) - 1
    a, b = float(ls.iloc[i]), float(ss.iloc[i])
    note = []
    # 大盘过滤: 大盘偏空时做多扣分, 偏多时做空扣分(逆势惩罚, 只影响今天的打分)
    pen = P.get("regime_penalty", 0)
    if reg and pen and reg["state"] == "偏空" and a > 0:
        a -= pen; note.append(f"逆大盘(大盘偏空)做多扣{pen}分")
    if reg and pen and reg["state"] == "偏多" and b > 0:
        b -= pen; note.append(f"逆大盘(大盘偏多)做空扣{pen}分")
    if max(a, b) < P["min_score"] or a == b:
        return None
    side = "做多" if a > b else "做空"
    score = max(a, b)
    lv = levels(X, i, side, P)
    if not lv:
        return None
    why = reasons(X, flags, i, side, P) + note

    info = symbols.describe(sym)
    fx = symbols.fx_to_usd(info["ccy"], P)              # 1 单位本币 = 多少美元
    cap = P["capital_cny"] / P["usd_cny"]               # 本金(美元等值)
    q_risk = cap * P["max_loss_pct"] / (lv["risk"] * fx)
    q_lev = cap * P["leverage"] / P["max_positions"] / (lv["entry"] * fx)
    qty = min(q_risk, q_lev)
    bound = "杠杆上限" if q_lev <= q_risk else "风险上限"
    extra = []
    if info["lot"] > 1:
        qty = (int(qty) // info["lot"]) * info["lot"]   # A股/港股按整手
        if qty < info["lot"]:
            extra.append(f"资金不足一手({info['lot']}股)，按此价格无法下单")
    out = {"sym": sym, "side": side, "score": score, "why": "；".join(why + extra), "last": float(X["c"].iloc[i]),
           "entry": float(lv["entry"]), "stop": float(lv["stop"]), "tp1": float(lv["tp1"]),
           "stop_pct": float(lv["risk"] / lv["entry"] * 100), "qty": float(qty),
           "notional": float(qty * lv["entry"] * fx), "risk_usd": float(qty * lv["risk"] * fx),
           "risk_pct": float(qty * lv["risk"] * fx / cap * 100),
           "bound": bound, "asof": str(df.index[-1].date()), "ccy": info["ccy"], "lot": info["lot"],
           "market": info["market"],
           "rsi": float(X["rsi"].iloc[i]), "volr": float(X["volr"].iloc[i]) if np.isfinite(X["volr"].iloc[i]) else None}
    if with_bt:
        # 回测用无大盘惩罚的原始打分(惩罚只作用于今天)
        out["bt"] = bt_stats(backtest(X, P, ls, ss))
    return out


def pick(results, frames, P):
    """排序 + 相关性选仓: 前 N 个「执行」, 与已选标的相关性过高的降为备选。"""
    results = sorted(results, key=lambda r: (-r["score"], r["stop_pct"]))
    chosen, N = [], int(P["max_positions"])
    rets = {}

    def ret(s):
        if s not in rets:
            c = frames[s]["Close"].pct_change().dropna().tail(60)
            rets[s] = c
        return rets[s]
    for r in results:
        r["role"] = "备选"
        if len(chosen) < N:
            ok = True
            for k in chosen:
                try:
                    j = pd.concat([ret(r["sym"]), ret(k["sym"])], axis=1, join="inner").dropna()
                    cr = float(j.corr().iloc[0, 1]) if len(j) > 20 else 0
                except Exception:
                    cr = 0
                if abs(cr) > P["corr_limit"]:
                    r["role_note"] = f"与 {k['sym']} 相关性 {cr:.2f}"
                    ok = False
                    break
            if ok:
                r["role"] = "执行"
                chosen.append(r)
    return results[:int(P["top_n"])]


def scan(frames, infos, P, reg_frames=None):
    """frames: 候选标的; infos: 数据状态; reg_frames: 用来判断大盘的(SPY/QQQ)。"""
    reg = regime(reg_frames if reg_frames is not None else frames, P)
    res, dropped = [], []
    for sym, df in frames.items():
        inf = infos.get(sym, {})
        if inf.get("stale_days") is not None and inf["stale_days"] > P["max_stale_days"]:
            dropped.append(f"{sym}(数据过期{inf['stale_days']}天)")
            continue
        r = analyze(sym, df, P, reg)
        if not r:
            continue
        r["stale_days"] = inf.get("stale_days")
        r["source"] = inf.get("source", "")
        if P.get("require_backtest") and r["bt"]["n"] >= 5 and not r["bt"]["ok"]:
            dropped.append(f"{sym}(历史验证未通过 期望{r['bt']['avg_r']:+.2f}R)")
            continue
        res.append(r)
    return {"signals": pick(res, frames, P), "dropped": dropped, "regime": reg}

# -*- coding: utf-8 -*-
"""每天在 GitHub 服务器上运行：取数 → 扫描 → 写 docs/data.json（网页读取它）"""
import os, sys, json, datetime as dt
from zoneinfo import ZoneInfo
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
os.environ.setdefault("SCAN_DATA_DIR", os.path.join(ROOT, "_work"))
from core import data as D, engine as E
from core.common import PARAMS

BJ = ZoneInfo("Asia/Shanghai")


def main():
    P = {k: v[0] for k, v in PARAMS.items()}
    with open(os.path.join(ROOT, "cloud", "universe.txt"), encoding="utf-8") as f:
        uni = [l.strip().upper() for l in f if l.strip() and not l.startswith("#")]
    fetcher = D.Fetcher(order=["Yahoo图表", "Stooq"], min_gap=2.0, retries=1, backoff=20)
    frames, infos = {}, {}
    for s in dict.fromkeys(uni + ["SPY"]):
        df, name, errs = fetcher.get(s)
        if df is not None:
            frames[s] = df
            infos[s] = {"stale_days": 0, "source": name}
    now = dt.datetime.now(BJ)
    cand = {s: frames[s] for s in uni if s in frames}
    out = {"time": f"{now:%Y-%m-%d %H:%M}", "ok": bool(cand), "universe": len(uni), "got": len(cand)}
    if not cand:
        out["reason"] = "今天没有取到任何数据（数据源暂时不可用）。这不代表没有信号，明天会自动重试。"
    else:
        res = E.scan(cand, infos, P, reg_frames={"SPY": frames["SPY"]} if "SPY" in frames else None)
        out.update(asof=max(str(cand[s].index[-1].date()) for s in cand), regime=res["regime"],
                   signals=res["signals"], dropped=res["dropped"],
                   failed=[s for s in uni if s not in frames],
                   capital_cny=P["capital_cny"], usd_cny=P["usd_cny"], leverage=P["leverage"],
                   max_positions=P["max_positions"], max_loss_pct=P["max_loss_pct"],
                   daily_loss_stop_pct=P["daily_loss_stop_pct"])
    os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
    with open(os.path.join(ROOT, "docs", "data.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("done", out["time"], "signals", len(out.get("signals", [])), "got", out["got"])


if __name__ == "__main__":
    main()

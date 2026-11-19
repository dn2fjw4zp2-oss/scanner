# -*- coding: utf-8 -*-
"""统一代码格式。内部 key：美股 NVDA；港股 HK00700；A股 SH600519 / SZ000001"""
import re

CN_RE = re.compile(r"(?:(SH|SZ)\.?)?(\d{6})(?:\.(SH|SZ|SS))?")
HK_RE = re.compile(r"(?:HK)?(\d{1,5})(?:\.HK)?")
US_RE = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}")


def normalize(raw):
    s = str(raw).strip().upper()
    m = CN_RE.fullmatch(s)
    if m:
        pre = m.group(1) or ("SH" if m.group(3) in ("SH", "SS") else m.group(3) or "")
        code = m.group(2)
        if not pre:
            pre = "SH" if code[0] in "69" else "SZ"
        return {"key": pre + code, "market": "CN", "ccy": "CNY", "display": f"{code}.{pre}"}
    m = HK_RE.fullmatch(s)
    if m and s[:2] == "HK" or (m and s.endswith(".HK")) or (m and s.isdigit() and len(s) == 5):
        code = m.group(1).zfill(5)
        return {"key": "HK" + code, "market": "HK", "ccy": "HKD", "display": code + ".HK"}
    if US_RE.fullmatch(s):
        return {"key": s, "market": "US", "ccy": "USD", "display": s}
    raise ValueError(f"代码格式不对：{raw}（美股如 NVDA，港股如 00700，A股如 600519）")


def describe(key):
    n = normalize(key)
    return {"market": n["market"], "ccy": n["ccy"], "lot": 1 if n["market"] == "US" else 100}


def fx_to_usd(ccy, P):
    """1 单位该币种 = 多少美元"""
    if ccy == "USD":
        return 1.0
    if ccy == "HKD":
        return P["hkd_cny"] / P["usd_cny"]
    return 1.0 / P["usd_cny"]          # CNY

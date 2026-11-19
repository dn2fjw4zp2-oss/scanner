# -*- coding: utf-8 -*-
"""生成 signals.md（给 Grok / 执行方看）和 signals.html（双击用浏览器打开，可核对现价）"""
import os, html

RULES = """## 给执行方的规则（请原样遵守）
1. 每个标的只挂一张进场条件单，触发后立刻同时挂对应止损单，止损不得上移放宽。
2. 下单前先把完整订单（标的、方向、价格、数量、杠杆）发给我确认，我回复"确认"才执行。
3. 使用逐仓，杠杆不得超过上限；数量不得超过表中数字。
4. 只执行状态为「执行」的订单；「备选」仅在某个「执行」标的被放弃时，才可按其表中数量替补，同时持仓不得超过上限个数。
5. 当日累计亏损达到停手线后不再开新仓。
6. 清单只对下一个交易日有效；开盘价与进场价偏差过大（跳空）则放弃该单，等新清单。
7. 涨到止盈1先平一半，剩余仓位跌破5日均线再平。"""


def _bt(b):
    if not b or not b.get("n"):
        return "样本不足"
    return f"{b['n']}次 · 胜率{b['win']*100:.0f}% · 期望{b['avg_r']:+.2f}R"


def to_md(o):
    L = []
    cap = o["capital_usd"]
    L.append(f"# 每日信号清单（生成于 {o['time']}）")
    L.append(f"数据截至最近交易日：{o['asof']}。价格基于美股/ETF日线，下单前请和合约现价核对，偏差超过2%就作废重算。")
    reg = o["regime"]
    L.append(f"大盘状态：{reg.get('state')}（{reg.get('sym') or '无数据'}）。本金约 {cap:.0f} USD；单笔最大亏损 {o['max_loss_pct']*100:.0f}%；"
             f"总杠杆上限 {o['leverage']}x；最多同时持仓 {o['max_positions']} 个；当日累计亏损达 {o['daily_loss_stop_pct']*100:.0f}% 停手。\n")
    run = [s for s in o["signals"] if s["role"] == "执行"]
    if run:
        tr = sum(s["risk_usd"] for s in run); tn = sum(s["notional"] for s in run)
        L.append(f"**只执行标「执行」的 {len(run)} 个。** 止损全部触发时合计最大亏损约 {tr:.0f} USD（{tr/cap*100:.1f}% 本金），"
                 f"合计名义仓位 {tn:.0f} USD（{tn/cap:.1f}x）。\n")
    L.append("| # | 状态 | 标的 | 方向 | 分数 | 进场条件单 | 止损 | 止盈1 | 止损距离 | 数量 | 名义USD | 最大亏损USD(%本金) | 历史同规则验证 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, s in enumerate(o["signals"], 1):
        trig = "涨到≥" if s["side"] == "做多" else "跌到≤"
        L.append(f"| {i} | {s['role']} | {s['sym']} | {s['side']} | {s['score']:.0f} | {trig}{s['entry']:.2f} | {s['stop']:.2f} | "
                 f"{s['tp1']:.2f} | {s['stop_pct']:.1f}% | {s['qty']:.3f} | {s['notional']:.0f} | {s['risk_usd']:.0f} ({s['risk_pct']:.1f}%) | {_bt(s.get('bt'))} |")
    if not o["signals"]:
        L.append("| - | - | 今天没有符合条件的信号，空仓也是一种决定 | | | | | | | | | | |")
    L.append("\n## 入选理由")
    for i, s in enumerate(o["signals"], 1):
        extra = f"（{s['role_note']}）" if s.get("role_note") else ""
        L.append(f"{i}. {s['sym']} {s['side']}：{s['why']}{extra}")
    if o["dropped"]:
        L.append("\n已剔除：" + "、".join(o["dropped"]))
    if o["failed"]:
        L.append("本次没取到数据：" + "、".join(o["failed"]) + "（这些标的今天没有信号，不代表没有机会）")
    if o["stale"]:
        L.append("数据比最近交易日旧（已使用，请留意）：" + "、".join(o["stale"]))
    L.append("注意：数据源不含财报日期，请自己确认财报日。\n")
    L.append(RULES)
    return "\n".join(L)


def to_html(o):
    cap = o["capital_usd"]
    cards = []
    for s in o["signals"]:
        cls = "long" if s["side"] == "做多" else "short"
        run = s["role"] == "执行"
        cards.append(
            f'<div class="card {cls} {"" if run else "alt"}" data-entry="{s["entry"]:.4f}" data-side="{cls}">'
            f'<div class="top"><b>{html.escape(s["sym"])}</b><span class="tag {cls}">{s["side"]}</span>'
            f'<span class="tag">{s["role"]}</span><span class="sc">分数 {s["score"]:.0f}</span></div>'
            f'<div class="g"><div><i>进场</i><b>{s["entry"]:.2f}</b></div><div><i>止损</i><b class="stop">{s["stop"]:.2f}</b></div>'
            f'<div><i>止盈1</i><b class="tp">{s["tp1"]:.2f}</b></div><div><i>数量上限</i><b>{s["qty"]:.3f}</b></div></div>'
            f'<div class="m">止损距离 {s["stop_pct"]:.1f}% · 最大亏损 {s["risk_usd"]:.0f} USD（{s["risk_pct"]:.1f}%）· 历史验证：{_bt(s.get("bt"))}</div>'
            f'<div class="w">{html.escape(s["why"])}</div>'
            f'<div class="ck"><input type="number" step="any" placeholder="输入合约现价"><span class="msg">未输入现价</span></div></div>')
    body = "".join(cards) or "<p>今天没有符合条件的信号，空仓也是一种决定。</p>"
    page = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>每日信号</title><style>
body{{margin:0;font:15px/1.5 -apple-system,"Microsoft YaHei",sans-serif;background:#f4f6f9;color:#16202b}}
.w{{max-width:980px;margin:0 auto;padding:18px 14px}}h1{{font-size:20px;margin:0}}.s{{color:#6b7785;font-size:13px;margin:4px 0 14px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:12px}}
.card{{background:#fff;border:1px solid #e3e8ef;border-left:4px solid #94a3b8;border-radius:12px;padding:14px}}
.card.long{{border-left-color:#2563eb}}.card.short{{border-left-color:#d97706}}.card.alt{{opacity:.8}}
.top{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px}}.tag{{font-size:12px;border:1px solid #e3e8ef;border-radius:99px;padding:1px 8px}}
.sc{{margin-left:auto;color:#6b7785;font-size:12px}}.g{{display:grid;grid-template-columns:1fr 1fr;gap:6px 12px}}
.g i{{display:block;font-style:normal;color:#6b7785;font-size:12px}}.g b{{font-size:18px;font-variant-numeric:tabular-nums}}
.stop{{color:#dc2626}}.tp{{color:#16a34a}}.m,.w{{color:#6b7785;font-size:12.5px;margin-top:8px}}
.ck{{display:flex;gap:8px;align-items:center;margin-top:10px;flex-wrap:wrap}}.ck input{{width:140px;padding:5px 8px;border:1px solid #e3e8ef;border-radius:8px}}
.msg{{font-size:13px;color:#6b7785}}.ok{{color:#16a34a}}.bad{{color:#dc2626}}.warn{{color:#d97706}}
.f{{color:#6b7785;font-size:12.5px;margin-top:16px;line-height:1.8}}
</style></head><body><div class="w"><h1>每日信号</h1>
<div class="s">数据截至 {o['asof']} · 生成于 {o['time']} · 本金约 {cap:.0f} USD · 只执行标「执行」的卡片</div>
<div class="cards">{body}</div>
<div class="f">{html.escape(o.get('footer',''))}仅为规则演算，不保证盈利，不构成投资建议。下单前请和合约现价核对，偏差超过2%作废。</div></div>
<script>
document.querySelectorAll('.card').forEach(function(c){{var inp=c.querySelector('input'),m=c.querySelector('.msg');
var e=parseFloat(c.dataset.entry),side=c.dataset.side;
inp.addEventListener('input',function(){{var p=parseFloat(inp.value);if(!isFinite(p)){{m.textContent='未输入现价';m.className='msg';return}}
var d=(p-e)/e*100,t='偏差 '+(d>=0?'+':'')+d.toFixed(2)+'%';
if(Math.abs(d)>2){{m.textContent=t+'，超过2%，这张单作废';m.className='msg bad'}}
else if((side==='short'&&p<=e)||(side==='long'&&p>=e)){{m.textContent=t+'，已越过进场价，条件单会立刻成交';m.className='msg warn'}}
else{{m.textContent=t+'，尚未触发，可以挂单';m.className='msg ok'}}}})}});
</script></body></html>"""
    return page


def write_all(o, folder):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "signals.md"), "w", encoding="utf-8") as f:
        f.write(to_md(o))
    with open(os.path.join(folder, "signals.html"), "w", encoding="utf-8") as f:
        f.write(to_html(o))

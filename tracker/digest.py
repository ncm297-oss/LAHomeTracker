"""Weekly digest: build text + HTML and send it through the Gmail API (send scope)."""
from __future__ import annotations

import base64
import json
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

from . import alerts, export
from .config import secret
from .db import DB


def _money(v) -> str:
    if v is None:
        return "—"
    return f"${v/1e6:.2f}M" if abs(v) >= 1e6 else f"${v/1e3:.0f}K"


def _pct_change(rows: list[dict], back: int) -> tuple[float, float, float] | None:
    if len(rows) <= back:
        return None
    a, b = rows[-1 - back]["value"], rows[-1]["value"]
    if a in (None, 0) or b is None:
        return None
    return a, b, (b - a) / a


def build_digest(db: DB, cfg: dict) -> tuple[str, str]:
    dcfg = cfg["digest"]
    threshold = cfg["scoring"]["digest_threshold"]
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    notes = export.load_notes()
    listings = export.listing_payload(db, cfg, notes)
    from .normalize import type_group
    active = [l for l in listings if l["status"] in ("active", "pending")]
    if not dcfg.get("include_multi_family", False):
        active = [l for l in active if type_group(l.get("property_type")) != "multi"]

    top = [l for l in active if (l.get("score") or 0) >= threshold][: dcfg["max_listings"]]
    crossed = [l for l in active if l.get("breakeven_price") and l["list_price"] and l["list_price"] <= l["breakeven_price"]]
    new = [l for l in active if (l.get("first_seen") or "") >= week_ago]
    cuts = []
    for l in active:
        recent = [h for h in l["price_history"] if h["event"] == "cut" and h["date"] >= week_ago]
        if recent:
            cuts.append((l, recent[-1]))

    rate_lines = []
    for sid, label in cfg["rates"]["series"].items():
        rows = db.series(sid)
        if not rows:
            continue
        cur = rows[-1]
        prev = next((r for r in reversed(rows) if r["date"] <= (date.fromisoformat(cur["date"]) - timedelta(days=7)).isoformat()), None)
        delta = f" ({cur['value'] - prev['value']:+.2f} wk)" if prev else ""
        rate_lines.append(f"{label}: {cur['value']:.2f}%{delta} as of {cur['date']}")

    market_lines = []
    for sid, label in cfg["market"]["fred_series"].items():
        rows = db.series(sid)
        ch = _pct_change(rows, 1)
        if ch:
            market_lines.append(f"{label}: {ch[1]:,.0f} ({ch[2]*100:+.1f}% vs prior, {rows[-1]['date']})")
    for key, label in (("inventory", "Zillow LA inventory"), ("days_to_pending", "Zillow days to pending"), ("sale_to_list", "Zillow sale-to-list")):
        rows = db.series(f"zillow_{key}", geo="metro")
        ch = _pct_change(rows, 1)
        if ch:
            v = f"{ch[1]:.3f}" if key == "sale_to_list" else f"{ch[1]:,.0f}"
            market_lines.append(f"{label}: {v} ({ch[2]*100:+.1f}% m/m, {rows[-1]['date']})")

    alert_list = alerts.evaluate(db, cfg)
    month_start = date.today().replace(day=1).isoformat()
    reqs = db.request_counts(since=month_start)
    budget = cfg["sources"]["rentcast"].get("monthly_budget")

    # ---- text ----
    L = [f"LA Tracker digest — {date.today():%a %b %d, %Y}", ""]
    if alert_list:
        L.append("RATE ALERTS")
        L += [f"  ! {a['title']}\n    {a['detail']}" for a in alert_list]
        L.append("")
    L.append(f"TOP LISTINGS (score >= {threshold}): {len(top)}")
    for l in top:
        L.append(f"  {l['score']:.0f}  {l['address']}, {l.get('city') or ''} — {_money(l['list_price'])}"
                 + (f", {l['sqft']:.0f} sf" if l.get("sqft") else "") + f"\n      {l.get('verdict') or ''}"
                 + (f"\n      {l['source_url']}" if l.get("source_url") else ""))
    L.append("")
    L.append(f"AT OR BELOW BREAKEVEN PRICE: {len(crossed)}")
    L += [f"  {l['address']} — asking {_money(l['list_price'])} vs breakeven {_money(l['breakeven_price'])}" for l in crossed[:10]]
    L.append("")
    L.append(f"NEW THIS WEEK: {len(new)}    PRICE CUTS THIS WEEK: {len(cuts)}")
    L += [f"  cut  {l['address']} -> {_money(h['price'])} on {h['date']}" for l, h in cuts[:10]]
    L.append("")
    L.append("RATES")
    L += [f"  {x}" for x in rate_lines] or ["  (no rate data yet — run `python -m tracker rates`)"]
    L.append("")
    L.append("MARKET")
    L += [f"  {x}" for x in market_lines] or ["  (no market data yet)"]
    L.append("")
    L.append("REQUESTS THIS MONTH: " + ", ".join(f"{k} {v}" for k, v in sorted(reqs.items())) + (f"  (RentCast budget {budget})" if budget else ""))
    if budget and reqs.get("rentcast", 0) > budget:
        L.append(f"  ! RentCast over the {budget}/month free tier — consider a paid plan or lower enrich limits")
    text = "\n".join(L)

    # ---- html (simple, same content) ----
    def li(items):
        return "".join(f"<li>{i}</li>" for i in items)
    H = [f"<h2>LA Tracker digest — {date.today():%a %b %d, %Y}</h2>"]
    if alert_list:
        H.append("<h3 style='color:#b3261e'>Rate alerts</h3><ul>" + li(f"<b>{escape(a['title'])}</b><br><small>{escape(a['detail'])}</small>" for a in alert_list) + "</ul>")
    H.append(f"<h3>Top listings (score ≥ {threshold})</h3><ol>" + li(
        f"<b>{l['score']:.0f}</b> " + (f"<a href='{escape(l['source_url'])}'>" if l.get("source_url") else "") + escape(l['address']) + (", " + escape(l.get('city') or "")) + ("</a>" if l.get("source_url") else "")
        + f" — {_money(l['list_price'])}<br><small>{escape(l.get('verdict') or '')}</small>" for l in top) + "</ol>")
    H.append(f"<h3>At or below breakeven: {len(crossed)}</h3><ul>" + li(f"{escape(l['address'])} — asking {_money(l['list_price'])} vs breakeven {_money(l['breakeven_price'])}" for l in crossed[:10]) + "</ul>")
    H.append(f"<h3>New this week: {len(new)} · Price cuts: {len(cuts)}</h3><ul>" + li(f"{escape(l['address'])} → {_money(h['price'])} ({h['date']})" for l, h in cuts[:10]) + "</ul>")
    H.append("<h3>Rates</h3><ul>" + li(escape(x) for x in rate_lines) + "</ul>")
    H.append("<h3>Market</h3><ul>" + li(escape(x) for x in market_lines) + "</ul>")
    H.append("<p><small>Requests this month: " + escape(", ".join(f"{k} {v}" for k, v in sorted(reqs.items()))) + "</small></p>")
    html = "<div style='font-family:system-ui,sans-serif;max-width:720px'>" + "".join(H) + "</div>"
    return text, html


def send_digest(db: DB, cfg: dict, text: str, html: str) -> str:
    from .sources.gmail_alerts import load_credentials
    from googleapiclient.discovery import build
    to = secret("DIGEST_TO")
    if not to:
        raise RuntimeError("DIGEST_TO not set")
    msg = MIMEMultipart("alternative")
    msg["To"] = to
    msg["From"] = "me"
    msg["Subject"] = f"{cfg['digest']['subject_prefix']} {date.today():%b %d} digest"
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))
    svc = build("gmail", "v1", credentials=load_credentials(), cache_discovery=False)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    res = svc.users().messages().send(userId="me", body={"raw": raw}).execute()
    db.log_request("gmail", "messages.send")
    return f"sent digest to {to} (id {res.get('id')})"

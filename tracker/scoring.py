"""Deal score 0-100 from six components. Each component is 0-100 or None (no data);
the total is the weight-normalized mean of the available ones."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from statistics import median

from . import buyrent
from .db import DB


def clamp(x: float, lo: float = 0, hi: float = 100) -> float:
    return max(lo, min(hi, x))


def type_group(property_type: str | None) -> str:
    """Collapse source-specific property types into sfr / condo / multi / other so $/sqft is
    compared like with like (a duplex at $400/sf is not a cheap house)."""
    t = (property_type or "").lower()
    if not t:
        return "other"
    if "multi" in t or "duplex" in t or "triplex" in t or "units" in t or "income" in t:
        return "multi"
    if "condo" in t or "town" in t or "co-op" in t or "coop" in t or "apartment" in t:
        return "condo"
    if "single" in t or "sfr" in t or "house" in t:
        return "sfr"
    return "other"


def comp_stats(db: DB, neighborhood: str, window_months: int, min_count: int, group: str = "other") -> dict:
    since = (date.today() - timedelta(days=30 * window_months)).isoformat()
    rows = db.all(
        "SELECT sold_price, sqft, days_on_market, property_type FROM comps WHERE neighborhood=? AND sold_date>=? AND sold_price>0",
        (neighborhood, since),
    )
    if group != "other":
        same = [r for r in rows if type_group(r.get("property_type")) == group]
        if len(same) >= min_count:
            rows = same
    ppsf = [r["sold_price"] / r["sqft"] for r in rows if r["sqft"]]
    dom = [r["days_on_market"] for r in rows if r["days_on_market"] is not None]
    out = {
        "n": len(rows),
        "median_ppsf": median(ppsf) if len(ppsf) >= min_count else None,
        "median_dom": median(dom) if len(dom) >= min_count else None,
        "source": "sold_comps",
    }
    # Fallback: RentCast zip-level market medians stored as series (geo = neighborhood key).
    if out["median_ppsf"] is None:
        s = db.series("rentcast_median_ppsf", geo=neighborhood, limit=1)
        if s:
            out["median_ppsf"], out["source"] = s[-1]["value"], "rentcast_market"
    if out["median_dom"] is None:
        s = db.series("rentcast_median_dom", geo=neighborhood, limit=1)
        if s:
            out["median_dom"] = s[-1]["value"]
    return out


def active_dom_median(db: DB, neighborhood: str) -> float | None:
    rows = db.all("SELECT days_on_market FROM listings WHERE neighborhood=? AND status='active' AND days_on_market IS NOT NULL", (neighborhood,))
    vals = [r["days_on_market"] for r in rows]
    return median(vals) if len(vals) >= 5 else None


def score_listing(listing: dict, history: list[dict], comps: dict, cfg: dict, nb_cfg: dict | None) -> dict:
    w = cfg["scoring"]["weights"]
    comp: dict[str, float | None] = {}
    raw: dict = {}
    price = listing.get("list_price") or 0
    sqft = listing.get("sqft")

    # 1. $/sqft vs sold-comp median: 15% below -> 100, 15% above -> 0
    if price and sqft and comps.get("median_ppsf"):
        r = (price / sqft) / comps["median_ppsf"]
        raw["ppsf"] = price / sqft
        raw["ppsf_ratio"] = r
        comp["ppsf"] = clamp(100 * (1.15 - r) / 0.30)
    else:
        comp["ppsf"] = None

    # 2. cumulative cut from original list + number of cuts
    orig = listing.get("original_list_price") or price
    cuts = [h for h in history if h.get("event") == "cut"]
    if price and orig and orig > 0:
        cut_pct = max(0.0, (orig - price) / orig)
        raw["cut_pct"] = cut_pct
        raw["n_cuts"] = len(cuts)
        comp["cuts"] = clamp(cut_pct / 0.10 * 70) + min(30, len(cuts) * 15)
    else:
        comp["cuts"] = None

    # 3. days on market vs neighborhood median (sold comps, else active listings): 2x median -> 100
    dom = listing.get("days_on_market")
    med_dom = comps.get("median_dom") or comps.get("active_median_dom")
    if dom is not None and med_dom:
        ratio = dom / med_dom
        raw["dom_ratio"] = ratio
        comp["dom"] = clamp((ratio - 0.5) / 1.5 * 100)
    else:
        comp["dom"] = None

    # 4. asking below the seller's last purchase price
    last = listing.get("last_sale_price")
    if price and last:
        below = (last - price) / last
        raw["vs_last_sale"] = -below
        comp["below_last_sale"] = clamp(50 + below / 0.10 * 50) if below > 0 else clamp(30 + below / 0.30 * 30)
    else:
        comp["below_last_sale"] = None

    # 5. gross rent yield: 2.5% -> 0, 4.5% -> 100
    rent = listing.get("est_rent")
    if price and rent:
        y = rent * 12 / price
        raw["gross_yield"] = y
        raw["yield_flag"] = y >= cfg["scoring"]["yield_flag"]
        comp["yield"] = clamp((y - 0.025) / 0.02 * 100)
    else:
        comp["yield"] = None

    # 6. breakeven price vs asking: asking 20% under breakeven -> 100, 30% over -> 0
    be = None
    if price and rent:
        p = buyrent.from_config(
            cfg["buyrent"], rent=rent,
            prop_tax_rate=(nb_cfg or {}).get("prop_tax_rate", 0.012),
            insurance=(nb_cfg or {}).get("insurance", 10_000),
            hoa=listing.get("hoa") or 0, price=price,
        )
        be = buyrent.breakeven_price(p)
        if be:
            ratio = price / be
            raw["breakeven_price"] = be
            raw["price_vs_breakeven"] = ratio
            comp["breakeven"] = clamp((1.3 - ratio) / 0.5 * 100)
        else:
            comp["breakeven"] = None
    else:
        comp["breakeven"] = None

    avail = {k: v for k, v in comp.items() if v is not None}
    total_w = sum(w[k] for k in avail)
    score = sum(w[k] * v for k, v in avail.items()) / total_w if total_w else 0.0
    return {"score": round(score, 1), "components": comp, "raw": raw, "breakeven_price": be, "coverage": round(total_w / sum(w.values()), 2)}


def verdict(listing: dict, s: dict, cfg: dict) -> str:
    bits = []
    raw = s["raw"]
    if "breakeven_price" in raw:
        be = raw["breakeven_price"]
        delta = listing["list_price"] - be
        bits.append(f"{'below' if delta < 0 else 'above'} breakeven by ${abs(delta)/1e3:.0f}K")
    if "ppsf_ratio" in raw:
        bits.append(f"${raw['ppsf']:.0f}/sf, {(raw['ppsf_ratio']-1)*100:+.0f}% vs comps")
    if raw.get("cut_pct"):
        bits.append(f"cut {raw['cut_pct']*100:.0f}% ({raw['n_cuts']} cuts)")
    if raw.get("vs_last_sale") is not None and raw["vs_last_sale"] < 0:
        bits.append(f"{-raw['vs_last_sale']*100:.0f}% under seller's purchase")
    if "gross_yield" in raw:
        bits.append(f"{raw['gross_yield']*100:.1f}% gross yield" + (" ⚑" if raw.get("yield_flag") else ""))
    return "; ".join(bits) if bits else "insufficient data"


def score_all(db: DB, cfg: dict) -> list[dict]:
    """Re-score every active listing. Returns rows with score info."""
    from .export import load_notes  # local import: export also imports scoring results
    sc = cfg["scoring"]
    notes = load_notes()
    out = []
    comps_cache: dict[str, dict] = {}
    for lst in db.all("SELECT * FROM listings WHERE status IN ('active','pending')"):
        nb = lst.get("neighborhood")
        grp = type_group(lst.get("property_type"))
        ck = f"{nb}|{grp}"
        if ck not in comps_cache:
            c = comp_stats(db, nb, sc["comps_window_months"], sc["comps_min_count"], grp) if nb else {}
            c["active_median_dom"] = active_dom_median(db, nb) if nb else None
            comps_cache[ck] = c
        hist = db.price_history(lst["id"])
        # Manual overrides from data/notes.yaml: est_rent on the listing, cost items on the neighborhood profile.
        ov = (notes.get(lst["id"]) or {}).get("overrides") or {}
        nb_cfg = dict(cfg["neighborhoods"].get(nb) or {})
        if ov.get("est_rent"):
            lst["est_rent"] = float(ov["est_rent"])
        for k in ("insurance", "prop_tax_rate"):
            if ov.get(k) is not None:
                nb_cfg[k] = float(ov[k])
        s = score_listing(lst, hist, comps_cache[ck], cfg, nb_cfg)
        v = verdict(lst, s, cfg)
        db.upsert("scores", {
            "listing_id": lst["id"], "scored_at": datetime.now().isoformat(timespec="seconds"),
            "score": s["score"], "components": json.dumps({"components": s["components"], "raw": s["raw"], "coverage": s["coverage"]}),
            "breakeven_price": s["breakeven_price"], "verdict": v,
        }, ["listing_id"])
        out.append({"id": lst["id"], "score": s["score"], "verdict": v, **s})
    return out

"""Write the JSON the static dashboard reads (docs/data/*.json)."""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import yaml

from . import alerts
from .config import DATA_DIR, DOCS_DATA_DIR
from .db import DB

NOTES_PATH = DATA_DIR / "notes.yaml"
RED_FLAGS = ["fire_zone", "insurability", "hillside_geotech", "unpermitted_work", "freeway_flightpath", "hoa_litigation", "tenant_occupied"]


def load_notes() -> dict:
    if not NOTES_PATH.exists():
        return {}
    return yaml.safe_load(NOTES_PATH.read_text(encoding="utf-8")) or {}


def save_notes(notes: dict) -> None:
    NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    NOTES_PATH.write_text(yaml.safe_dump(notes, sort_keys=True, allow_unicode=True), encoding="utf-8")


def listing_payload(db: DB, cfg: dict, notes: dict) -> list[dict]:
    out = []
    for lst in db.all("SELECT l.*, s.score, s.components, s.breakeven_price, s.verdict, s.scored_at FROM listings l LEFT JOIN scores s ON s.listing_id=l.id ORDER BY s.score DESC"):
        if lst.get("extra") and isinstance(lst["extra"], str):
            try:
                lst["extra"] = json.loads(lst["extra"])
            except json.JSONDecodeError:
                pass
        comp = json.loads(lst["components"]) if lst.get("components") else {}
        lst["components"] = comp.get("components")
        lst["raw"] = comp.get("raw")
        lst["coverage"] = comp.get("coverage")
        lst["price_history"] = db.price_history(lst["id"])
        n = notes.get(lst["id"], {})
        lst["flags"] = {k: (n.get("flags") or {}).get(k) for k in RED_FLAGS}
        lst["notes"] = n.get("notes")
        lst["overrides"] = n.get("overrides") or {}
        lst["sources"] = (lst.get("sources") or "").split(",") if lst.get("sources") else []
        out.append(lst)
    return out


def series_payload(db: DB, names: list[str], geo: str = "", limit: int | None = None) -> dict:
    return {n: db.series(n, geo=geo, limit=limit) for n in names}


def market_payload(db: DB, cfg: dict) -> dict:
    out = {"fred": {}, "zillow_metro": {}, "zillow_zip": {}, "redfin": {}, "rentcast": {}}
    for sid in cfg["market"]["fred_series"]:
        out["fred"][sid] = db.series(sid)
    for key in cfg["market"]["zillow"]["files"]:
        if key.endswith("_zip"):
            out["zillow_zip"][key] = {}
            for nb in cfg["neighborhoods"].values():
                for z in nb["zips"]:
                    rows = db.series(f"zillow_{key}", geo=z, limit=132)
                    if rows:
                        out["zillow_zip"][key][z] = rows
        else:
            out["zillow_metro"][key] = db.series(f"zillow_{key}", geo="metro", limit=132)
    for name in ("redfin_median_sale_price", "redfin_median_dom", "redfin_sale_to_list", "redfin_inventory", "redfin_months_of_supply", "redfin_price_drops"):
        rows = db.series(name, geo="metro", limit=60)
        if rows:
            out["redfin"][name] = rows
    for nb in cfg["neighborhoods"]:
        rows = {n: db.series(n, geo=nb, limit=12) for n in ("rentcast_median_ppsf", "rentcast_median_dom", "rentcast_median_price")}
        if any(rows.values()):
            out["rentcast"][nb] = rows
    out["comps"] = db.all("SELECT neighborhood, COUNT(*) AS n, MIN(sold_date) AS from_date, MAX(sold_date) AS to_date FROM comps GROUP BY neighborhood")
    return out


def export_all(db: DB, cfg: dict, out_dir: Path = DOCS_DATA_DIR) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    notes = load_notes()
    listings = listing_payload(db, cfg, notes)
    rates = {sid: db.series(sid) for sid in cfg["rates"]["series"]}
    market = market_payload(db, cfg)
    alert_list = alerts.evaluate(db, cfg)
    month_start = date.today().replace(day=1).isoformat()
    meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "price_band": cfg["price_band"], "rent_band": cfg["rent_band"], "cash_ceiling": cfg["cash_ceiling"],
        "neighborhoods": {k: {"name": v["name"], "city": v["city"], "zips": v["zips"], "prop_tax_rate": v["prop_tax_rate"], "fire_risk": v["fire_risk"], "insurance": v["insurance"]} for k, v in cfg["neighborhoods"].items()},
        "scoring": cfg["scoring"], "rates_labels": cfg["rates"]["series"], "market_labels": cfg["market"]["fred_series"],
        "alerts": alert_list, "red_flags": RED_FLAGS,
        "requests_this_month": db.request_counts(since=month_start), "requests_total": db.request_counts(),
        "counts": {"listings": len(listings), "active": sum(1 for l in listings if l["status"] == "active"), "comps": db.one("SELECT COUNT(*) AS n FROM comps")["n"]},
        "last_runs": db.all("SELECT ts, summary FROM runs ORDER BY ts DESC LIMIT 5"),
    }
    for name, payload in (("listings", listings), ("rates", rates), ("market", market), ("meta", meta)):
        (out_dir / f"{name}.json").write_text(json.dumps(payload, default=str), encoding="utf-8")
    return meta

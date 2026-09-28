"""Rental search: merge for-rent homes from every source into data/rentals.sqlite, decide
which ones fit the search, retire ones that stop showing up, and export
docs/data/rentals.json for docs/rentals.html.

One row per home (matched on normalized address); every source that carries it gets a
row in rental_links so the page can link to each site's posting.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from .config import DATA_DIR, DOCS_DATA_DIR, ROOT
from .db import DB
from .models import Rental

NOTES_PATH = DATA_DIR / "rental_notes.yaml"
TRIAGE = ["shortlist", "contacted", "toured", "applied", "pass"]
# Sources that are pulled on every run, so "not seen lately" means the listing is gone.
PULLED = {"realtor.com"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS rentals (
  id TEXT PRIMARY KEY,
  address TEXT NOT NULL, address_norm TEXT NOT NULL,
  city TEXT, zip_code TEXT, neighborhood TEXT,
  rent REAL, original_rent REAL,
  beds REAL, baths REAL, sqft REAL, property_type TEXT, kind TEXT,
  year_built INTEGER, lot_sqft REAL, listed_date TEXT, available_date TEXT,
  latitude REAL, longitude REAL, photo TEXT, description TEXT, listed_by TEXT,
  status TEXT DEFAULT 'active', first_seen TEXT, last_seen TEXT, gone_date TEXT
);
CREATE INDEX IF NOT EXISTS idx_rentals_addr ON rentals(address_norm);

CREATE TABLE IF NOT EXISTS rental_links (
  rental_id TEXT NOT NULL, source TEXT NOT NULL, url TEXT NOT NULL DEFAULT '', feed TEXT,
  rent REAL, first_seen TEXT, last_seen TEXT,
  UNIQUE(rental_id, source, url)
);

CREATE TABLE IF NOT EXISTS rental_price_history (
  rental_id TEXT NOT NULL, date TEXT NOT NULL, rent REAL NOT NULL, event TEXT, source TEXT,
  UNIQUE(rental_id, date, rent)
);
"""

_FILL = ["city", "zip_code", "neighborhood", "beds", "baths", "sqft", "property_type", "kind", "year_built",
         "lot_sqft", "listed_date", "available_date", "latitude", "longitude", "photo", "description", "listed_by"]


def db_path(cfg: dict) -> Path:
    return ROOT / cfg.get("rentals", {}).get("db", "data/rentals.sqlite")


def open_db(cfg: dict, path: Path | str | None = None) -> DB:
    db = DB(path or db_path(cfg))
    db.conn.executescript(SCHEMA)
    return db


# ---------------- fit ----------------

def fit(r: dict, cfg: dict) -> tuple[str, list[str]]:
    """('match' | 'near' | 'out', misses). Unknown sqft or type is not held against a listing."""
    rc = cfg["rentals"]
    lo, hi = rc["rent"]
    misses = []
    if r.get("rent") is None or not (lo <= r["rent"] <= hi):
        misses.append("rent")
    if r.get("beds") is None or r["beds"] < rc["min_beds"]:
        misses.append("beds")
    if r.get("sqft") and r["sqft"] < rc["min_sqft"]:
        misses.append("sqft")
    if r.get("kind") and r["kind"] not in rc["match_types"]:
        misses.append("type")
    return ("match" if not misses else "near" if len(misses) == 1 else "out"), misses


def in_scope(rental: Rental, cfg: dict) -> bool:
    """Inside the target zips, a house-like type, and within the pull's price range."""
    pull = cfg["rentals"]["pull"]
    if not rental.neighborhood or not rental.address:
        return False
    if rental.kind == "apartment":
        return False
    if rental.rent is not None and not (pull["price_min"] <= rental.rent <= pull["price_max"]):
        return False
    return True


# ---------------- merge ----------------

def _pulled_recently(db: DB, rid: str, today: str, days: int) -> bool:
    cutoff = (date.fromisoformat(today) - timedelta(days=days)).isoformat()
    ph = ",".join("?" * len(PULLED))
    return db.one(f"SELECT 1 FROM rental_links WHERE rental_id=? AND source IN ({ph}) AND last_seen>=?",
                  (rid, *PULLED, cutoff)) is not None


def upsert(db: DB, r: Rental, cfg: dict, today: str | None = None) -> tuple[str, bool, list[str]]:
    """Returns (rental_id, is_new, changes)."""
    today = today or date.today().isoformat()
    existing = db.one("SELECT * FROM rentals WHERE address_norm=?", (r.address_norm,))
    changes: list[str] = []
    if existing is None:
        rid = r.id
        row = {k: getattr(r, k) for k in _FILL}
        row.update(id=rid, address=r.address, address_norm=r.address_norm, rent=r.rent, original_rent=r.rent,
                   status="active", first_seen=today, last_seen=today)
        db.insert("rentals", row)
        if r.rent:
            db.insert_ignore("rental_price_history", {"rental_id": rid, "date": today, "rent": r.rent, "event": "list", "source": r.source})
        changes.append("new")
    else:
        rid = existing["id"]
        upd: dict = {"last_seen": today}
        for k in _FILL:
            v = getattr(r, k)
            if existing.get(k) in (None, "") and v not in (None, ""):
                upd[k] = v
        if existing["status"] != "active":
            upd.update(status="active", gone_date=None)
            changes.append("back on market")
        # Daily-pulled sources own the rent figure; an alert email only moves it when no pull has
        # seen the home lately, so two sites quoting different rents don't flip-flop the history.
        authoritative = r.source in PULLED or not _pulled_recently(db, rid, today, cfg["rentals"]["gone_after_days"])
        if r.rent and authoritative and existing.get("rent") and abs(r.rent - existing["rent"]) > 0.5:
            event = "cut" if r.rent < existing["rent"] else "raise"
            db.insert_ignore("rental_price_history", {"rental_id": rid, "date": today, "rent": r.rent, "event": event, "source": r.source})
            upd["rent"] = r.rent
            changes.append(f"rent {existing['rent']:.0f} -> {r.rent:.0f}")
        elif r.rent and not existing.get("rent"):
            upd.update(rent=r.rent, original_rent=r.rent)
        db.update("rentals", rid, upd)
    link = {"rental_id": rid, "source": r.source, "url": r.url or "", "feed": r.feed, "rent": r.rent, "first_seen": today, "last_seen": today}
    if not db.insert_ignore("rental_links", link):
        db.conn.execute("UPDATE rental_links SET last_seen=?, rent=COALESCE(?, rent), feed=COALESCE(?, feed) WHERE rental_id=? AND source=? AND url=?",
                        (today, r.rent, r.feed, rid, r.source, r.url or ""))
        db.conn.commit()
    return rid, existing is None, changes


def ingest(db: DB, rentals: list[Rental], cfg: dict, today: str | None = None) -> dict:
    st = {"seen": 0, "new": 0, "changed": 0, "skipped": 0}
    for r in rentals:
        if not in_scope(r, cfg):
            st["skipped"] += 1
            continue
        st["seen"] += 1
        _, is_new, changes = upsert(db, r, cfg, today)
        if is_new:
            st["new"] += 1
        elif changes:
            st["changed"] += 1
    return st


def mark_gone(db: DB, cfg: dict, today: str | None = None, pulled_ok: bool = True) -> int:
    """Retire listings that stopped showing up. Pulled sources: gone after `gone_after_days`
    without a sighting (only judged after a successful pull). Email/manual-only listings: gone
    after `email_stale_days`, since an alert only mentions a home once."""
    today = today or date.today().isoformat()
    rc = cfg["rentals"]
    d = date.fromisoformat(today)
    pull_cut = (d - timedelta(days=rc["gone_after_days"])).isoformat()
    stale_cut = (d - timedelta(days=rc["email_stale_days"])).isoformat()
    ph = ",".join("?" * len(PULLED))
    n = 0
    for r in db.all("SELECT id, last_seen FROM rentals WHERE status='active'"):
        pulled = db.one(f"SELECT MAX(last_seen) AS t FROM rental_links WHERE rental_id=? AND source IN ({ph})", (r["id"], *PULLED))["t"]
        if pulled:
            gone = pulled_ok and r["last_seen"] < pull_cut
        else:
            gone = r["last_seen"] < stale_cut
        if gone:
            db.update("rentals", r["id"], {"status": "gone", "gone_date": today})
            n += 1
    return n


# ---------------- notes ----------------

def load_notes() -> dict:
    if not NOTES_PATH.exists():
        return {}
    return yaml.safe_load(NOTES_PATH.read_text(encoding="utf-8")) or {}


def save_notes(notes: dict) -> None:
    NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    NOTES_PATH.write_text(yaml.safe_dump(notes, sort_keys=True, allow_unicode=True), encoding="utf-8")


def find(db: DB, key: str) -> dict | None:
    return db.one("SELECT * FROM rentals WHERE id=?", (key,)) or db.one("SELECT * FROM rentals WHERE address LIKE ? ORDER BY last_seen DESC", (f"%{key}%",))


# ---------------- export ----------------

def payload(db: DB, cfg: dict, notes: dict, today: str | None = None) -> list[dict]:
    today = today or date.today().isoformat()
    d = date.fromisoformat(today)
    out = []
    for r in db.all("SELECT * FROM rentals ORDER BY first_seen DESC, rent"):
        r["fit"], r["misses"] = fit(r, cfg)
        r["links"] = db.all("SELECT source, url, feed, rent, first_seen, last_seen FROM rental_links WHERE rental_id=? ORDER BY first_seen", (r["id"],))
        r["history"] = db.all("SELECT date, rent, event, source FROM rental_price_history WHERE rental_id=? ORDER BY date", (r["id"],))
        start = r.get("listed_date") or r["first_seen"]
        try:
            r["days_listed"] = (d - date.fromisoformat(start[:10])).days
        except ValueError:
            r["days_listed"] = None
        n = notes.get(r["id"], {})
        r["triage"] = n.get("status")
        r["notes"] = n.get("notes")
        if r.get("description") and len(r["description"]) > 600:
            r["description"] = r["description"][:600].rsplit(" ", 1)[0] + "…"
        out.append(r)
    return out


def export(db: DB, cfg: dict, out_dir: Path = DOCS_DATA_DIR, today: str | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = payload(db, cfg, load_notes(), today)
    rc = cfg["rentals"]
    doc = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "criteria": {"rent": rc["rent"], "min_beds": rc["min_beds"], "min_sqft": rc["min_sqft"], "match_types": rc["match_types"]},
        "neighborhoods": {k: {"name": v["name"], "zips": v["zips"]} for k, v in cfg["neighborhoods"].items()},
        "triage": TRIAGE,
        "counts": {
            "total": len(rows),
            "active": sum(r["status"] == "active" for r in rows),
            "matches": sum(r["status"] == "active" and r["fit"] == "match" for r in rows),
        },
        "last_runs": db.all("SELECT ts, summary FROM runs ORDER BY ts DESC LIMIT 5"),
        "rentals": rows,
    }
    (out_dir / "rentals.json").write_text(json.dumps(doc, default=str), encoding="utf-8")
    return doc["counts"]

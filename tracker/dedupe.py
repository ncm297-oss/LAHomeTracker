"""Merge listings from several sources into the database.

Match order: MLS number, then normalized address. On match, fill missing
fields, union sources, append new price history points, and refresh the
list price (recording a cut/raise when it changed).
"""
from __future__ import annotations

from datetime import date

from .db import DB
from .models import Listing, PricePoint

# Fields where an incoming non-null value should overwrite the stored one.
_REFRESH = {"list_price", "days_on_market", "status", "source_url"}
# Fields only filled when the stored value is missing.
_FILL = {
    "city", "zip_code", "neighborhood", "original_list_price", "sqft", "beds", "baths", "lot_sqft",
    "year_built", "property_type", "listed_date", "last_sale_price", "last_sale_date", "est_rent",
    "est_value", "hoa", "mls_number", "latitude", "longitude",
}


def find_existing(db: DB, listing: Listing) -> dict | None:
    if listing.mls_number:
        row = db.one("SELECT * FROM listings WHERE mls_number = ?", (listing.mls_number.strip().upper(),))
        if row:
            return row
    return db.one("SELECT * FROM listings WHERE address_norm = ?", (listing.address_norm,))


def upsert(db: DB, listing: Listing, today: str | None = None) -> tuple[str, bool, list[str]]:
    """Returns (listing_id, is_new, changes)."""
    today = today or date.today().isoformat()
    if listing.mls_number:
        listing.mls_number = listing.mls_number.strip().upper()
    existing = find_existing(db, listing)
    changes: list[str] = []

    if existing is None:
        row = listing.to_row()
        row["first_seen"] = today
        row["last_seen"] = today
        if row.get("original_list_price") is None:
            row["original_list_price"] = row.get("list_price")
        db.insert("listings", row)
        lid = row["id"]
        pts = list(listing.price_history)
        if not pts and listing.list_price:
            pts.append(PricePoint(listing.listed_date or today, listing.list_price, "list", ",".join(listing.sources)))
        for pt in pts:
            db.insert_ignore("price_history", {"listing_id": lid, "date": pt.date, "price": pt.price, "event": pt.event, "source": pt.source})
        return lid, True, ["new"]

    lid = existing["id"]
    upd: dict = {"last_seen": today}
    incoming = listing.to_row()
    for k in _FILL:
        if existing.get(k) in (None, "") and incoming.get(k) not in (None, ""):
            upd[k] = incoming[k]
            changes.append(f"filled {k}")
    for k in _REFRESH:
        v = incoming.get(k)
        if v in (None, ""):
            continue
        if k == "list_price" and existing.get("list_price") and abs(v - existing["list_price"]) > 0.5:
            event = "cut" if v < existing["list_price"] else "raise"
            db.insert_ignore("price_history", {"listing_id": lid, "date": today, "price": v, "event": event, "source": ",".join(listing.sources)})
            changes.append(f"price {existing['list_price']:.0f} -> {v:.0f}")
        if v != existing.get(k):
            upd[k] = v
    srcs = set(filter(None, (existing.get("sources") or "").split(","))) | set(listing.sources)
    upd["sources"] = ",".join(sorted(srcs))
    if existing.get("original_list_price") is None and (upd.get("list_price") or existing.get("list_price")):
        upd["original_list_price"] = existing.get("list_price") or upd.get("list_price")
    db.update("listings", lid, upd)
    for pt in listing.price_history:
        if db.insert_ignore("price_history", {"listing_id": lid, "date": pt.date, "price": pt.price, "event": pt.event, "source": pt.source}):
            changes.append(f"history {pt.date} {pt.price:.0f}")
    # Keep original_list_price as the earliest known price.
    first = db.one("SELECT price FROM price_history WHERE listing_id = ? ORDER BY date ASC LIMIT 1", (lid,))
    if first and (existing.get("original_list_price") is None or first["price"] > (existing.get("original_list_price") or 0)):
        db.update("listings", lid, {"original_list_price": max(first["price"], existing.get("original_list_price") or 0)})
    return lid, False, changes

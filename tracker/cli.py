"""Command line: python -m tracker <command>. Run `python -m tracker -h`."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

from . import config, dedupe, export, scoring, alerts, rentals
from .db import DB
from .models import Listing, PricePoint, Rental
from .normalize import rental_kind
from .sources.base import SourceError
from .sources.fred import FRED
from .sources.gmail_alerts import GmailAlerts, run_oauth_flow
from .sources.homeharvest_adapter import HomeHarvest
from .sources.redfin_csv import RedfinCSV
from .sources.redfin_datacenter import RedfinDataCenter
from .sources.rentcast import RentCast
from .sources.zillow_research import ZillowResearch

log = logging.getLogger("tracker")


def _ingest(db: DB, listings: list[Listing]) -> dict:
    stats = {"new": 0, "updated": 0, "changes": []}
    for lst in listings:
        lid, is_new, changes = dedupe.upsert(db, lst)
        if is_new:
            stats["new"] += 1
        elif changes:
            stats["updated"] += 1
            stats["changes"].append((lid, lst.address, changes))
    return stats


def _ingest_comps(db: DB, comps: list[dict]) -> int:
    n = 0
    for c in comps:
        db.upsert("comps", c, ["id"])
        n += 1
    return n


# ---------------- commands ----------------

def cmd_init(args, db, cfg):
    config.DATA_DIR.mkdir(exist_ok=True)
    (config.DATA_DIR / "inbox").mkdir(exist_ok=True)
    print(f"database at {db.path}")


def cmd_pull(args, db, cfg):
    nbs = [args.neighborhood] if args.neighborhood else list(cfg["neighborhoods"])
    adapters = {"rentcast": RentCast(db, cfg), "homeharvest": HomeHarvest(db, cfg)}
    order = [args.source] if args.source else ["rentcast", "homeharvest"]
    total = {"new": 0, "updated": 0}
    for nb in nbs:
        got = False
        for name in order:
            ad = adapters[name]
            if not ad.enabled:
                log.info("%s disabled or missing key; skipping", name)
                continue
            try:
                listings = ad.fetch_listings(nb)
            except SourceError as e:
                log.warning("%s failed for %s: %s", name, nb, e)
                continue
            st = _ingest(db, listings)
            log.info("%s %s: %d listings, %d new, %d updated", name, nb, len(listings), st["new"], st["updated"])
            total["new"] += st["new"]; total["updated"] += st["updated"]
            got = True
            if not args.all_sources:
                break
        if not got:
            log.warning("no listing source succeeded for %s", nb)
    print(json.dumps(total))
    return total


def cmd_import_csv(args, db, cfg):
    ad = RedfinCSV(db, cfg)
    paths = [Path(p) for p in args.paths] if args.paths else ad.inbox_files()
    for p in paths:
        listings, comps = ad.parse_file(p)
        st = _ingest(db, listings)
        nc = _ingest_comps(db, comps)
        print(f"{p.name}: {len(listings)} listings ({st['new']} new, {st['updated']} updated), {nc} sold comps")


def cmd_import_redfin_dc(args, db, cfg):
    ad = RedfinDataCenter(db, cfg)
    paths = [Path(p) for p in args.paths] if args.paths else ad.inbox_files()
    for p in paths:
        print(f"{p.name}: {ad.import_file(p)} points")


def cmd_import_gmail(args, db, cfg):
    ad = GmailAlerts(db, cfg)
    if not ad.enabled:
        print("gmail disabled in config"); return
    try:
        listings = ad.fetch_alerts()
    except SourceError as e:
        print(f"gmail: {e}"); return
    st = _ingest(db, listings)
    print(f"gmail: {len(listings)} listings parsed, {st['new']} new, {st['updated']} updated")


def cmd_comps(args, db, cfg):
    nbs = [args.neighborhood] if args.neighborhood else list(cfg["neighborhoods"])
    hh, rc = HomeHarvest(db, cfg), RentCast(db, cfg)
    for nb in nbs:
        n = 0
        if hh.enabled and args.source in (None, "homeharvest"):
            try:
                n = _ingest_comps(db, hh.fetch_sold(nb))
            except SourceError as e:
                log.warning("homeharvest sold %s: %s", nb, e)
        if rc.enabled and args.source in (None, "rentcast"):
            try:
                stats = rc.fetch_market(nb)
                log.info("rentcast market %s: %s", nb, stats)
            except SourceError as e:
                log.warning("rentcast market %s: %s", nb, e)
        print(f"{nb}: {n} sold comps")


def cmd_enrich(args, db, cfg):
    """Rent estimates and property records for listings missing them (costs RentCast requests)."""
    rc = RentCast(db, cfg)
    if not rc.enabled:
        print("rentcast disabled or no key"); return
    # Stay inside the monthly plan: each AVM call is one request.
    budget = cfg["sources"]["rentcast"].get("monthly_budget")
    used = db.request_counts(since=date.today().replace(day=1).isoformat()).get("rentcast", 0)
    if budget:
        room = max(0, budget - used)
        if room < args.limit:
            print(f"rentcast: {used}/{budget} requests used this month; limiting enrich to {room}")
            args.limit = room
        if room == 0:
            return
    # Highest-scored first so a small request budget goes to the listings that matter.
    rows = db.all("SELECT l.* FROM listings l LEFT JOIN scores s ON s.listing_id=l.id "
                  "WHERE l.status='active' AND (l.est_rent IS NULL OR (l.last_sale_price IS NULL AND NOT ?)) "
                  "ORDER BY s.score DESC NULLS LAST, l.last_seen DESC LIMIT ?", (int(args.rent_only), args.limit))
    for r in rows:
        lst = Listing(address=r["address"], city=r["city"], zip_code=r["zip_code"], state=r["state"] or "CA",
                      property_type=r["property_type"], beds=r["beds"], baths=r["baths"], sqft=r["sqft"])
        upd = {}
        try:
            if r["est_rent"] is None:
                rent = rc.fetch_rent_estimate(lst)
                if rent:
                    upd["est_rent"] = rent
            if r["last_sale_price"] is None and not args.rent_only:
                upd.update({k: v for k, v in rc.fetch_property(lst).items() if k in ("last_sale_price", "last_sale_date", "lot_sqft", "year_built", "sqft") and r.get(k) is None})
        except SourceError as e:
            log.warning("enrich %s: %s", r["address"], e)
            continue
        if upd:
            db.update("listings", r["id"], upd)
            print(f"{r['address']}: {upd}")


def cmd_score(args, db, cfg):
    rows = scoring.score_all(db, cfg)
    rows.sort(key=lambda r: -r["score"])
    for r in rows[: args.top]:
        lst = db.one("SELECT address, list_price, neighborhood FROM listings WHERE id=?", (r["id"],))
        print(f"{r['score']:5.1f}  {lst['address']:<40} {lst['neighborhood'] or '':<15} ${lst['list_price'] or 0:,.0f}  {r['verdict']}")
    print(f"{len(rows)} listings scored")


def cmd_rates(args, db, cfg):
    fred = FRED(db, cfg)
    counts = fred.refresh(list(cfg["rates"]["series"]), cfg["rates"]["history_years"])
    print(counts)
    for a in alerts.evaluate(db, cfg):
        print(f"ALERT [{a['level']}] {a['title']}")


def cmd_market(args, db, cfg):
    fred = FRED(db, cfg)
    print("fred", fred.refresh(list(cfg["market"]["fred_series"]), 5))
    zr = ZillowResearch(db, cfg)
    if zr.enabled:
        try:
            print("zillow", zr.refresh())
        except SourceError as e:
            log.warning("zillow research: %s", e)


def cmd_export(args, db, cfg):
    meta = export.export_all(db, cfg)
    print(f"exported {meta['counts']} to {config.DOCS_DATA_DIR}")


def cmd_note(args, db, cfg):
    notes = export.load_notes()
    lid = args.id
    if not db.one("SELECT id FROM listings WHERE id=?", (lid,)):
        row = db.one("SELECT id FROM listings WHERE address LIKE ?", (f"%{lid}%",))
        if not row:
            print("no such listing"); return
        lid = row["id"]
    n = notes.setdefault(lid, {})
    if args.flag:
        flags = n.setdefault("flags", {})
        for f in args.flag:
            k, _, v = f.partition("=")
            flags[k] = {"true": True, "yes": True, "false": False, "no": False, "": True}.get(v.lower(), v)
    if args.text:
        n["notes"] = ((n.get("notes") or "") + "\n" + args.text).strip()
    if args.override:
        ov = n.setdefault("overrides", {})
        for o in args.override:
            k, _, v = o.partition("=")
            ov[k] = float(v) if v.replace(".", "", 1).isdigit() else v
    export.save_notes(notes)
    print(json.dumps({lid: n}, indent=2))


def cmd_add(args, db, cfg):
    lst = Listing(address=args.address, city=args.city, zip_code=args.zip, list_price=args.price, sqft=args.sqft,
                  beds=args.beds, baths=args.baths, mls_number=args.mls, source_url=args.url, sources=["manual"],
                  est_rent=args.rent, neighborhood=config.neighborhood_for(args.zip, args.city, cfg))
    lst.price_history.append(PricePoint(date.today().isoformat(), args.price, "list", "manual"))
    lid, is_new, changes = dedupe.upsert(db, lst)
    print(f"{'added' if is_new else 'updated'} {lid} {changes}")


def cmd_status(args, db, cfg):
    month_start = date.today().replace(day=1).isoformat()
    print("requests this month:", db.request_counts(since=month_start))
    print("requests total:     ", db.request_counts())
    print("listings:", db.one("SELECT COUNT(*) AS n FROM listings")["n"], "comps:", db.one("SELECT COUNT(*) AS n FROM comps")["n"])
    for r in db.all("SELECT ts, summary FROM runs ORDER BY ts DESC LIMIT 3"):
        print(r["ts"], r["summary"])


def cmd_gmail_auth(args, db, cfg):
    print("token written to", run_oauth_flow())


def cmd_digest(args, db, cfg):
    from .digest import build_digest, send_digest
    text, html = build_digest(db, cfg)
    if args.dry_run:
        print(text)
    else:
        print(send_digest(db, cfg, text, html))


def cmd_run_weekly(args, db, cfg):
    from .digest import build_digest, send_digest
    summary = {}
    t0 = datetime.now()
    ns = argparse.Namespace(neighborhood=None, source=None, all_sources=False, paths=None, limit=args.enrich_limit, rent_only=False, top=0)
    summary["pull"] = cmd_pull(ns, db, cfg)
    cmd_import_csv(ns, db, cfg)
    if cfg["sources"]["gmail"].get("enabled"):
        cmd_import_gmail(ns, db, cfg)
    # Sold comps (HomeHarvest) monthly; RentCast zip medians every run (30-day disk cache makes repeats free).
    last = db.one("SELECT MAX(fetched_at) AS t FROM comps")
    if not last or not last["t"] or (datetime.now() - datetime.fromisoformat(last["t"])).days >= 30:
        cmd_comps(argparse.Namespace(neighborhood=None, source="homeharvest"), db, cfg)
    cmd_comps(argparse.Namespace(neighborhood=None, source="rentcast"), db, cfg)
    cmd_enrich(ns, db, cfg)
    scored = scoring.score_all(db, cfg)
    summary["scored"] = len(scored)
    cmd_rates(ns, db, cfg)
    cmd_market(ns, db, cfg)
    meta = export.export_all(db, cfg)
    summary["alerts"] = len(meta["alerts"])
    summary["requests"] = meta["requests_this_month"]
    db.upsert("runs", {"ts": t0.isoformat(timespec="seconds"), "summary": json.dumps(summary)}, ["ts"])
    text, html = build_digest(db, cfg)
    if args.dry_run:
        print(text)
    else:
        print(send_digest(db, cfg, text, html))
    print(json.dumps(summary))


# ---------------- rentals ----------------

def _rdb(args, cfg):
    return rentals.open_db(cfg, getattr(args, "rentals_db", None))


def _zips(cfg, neighborhood=None) -> list[str]:
    nbs = [neighborhood] if neighborhood else list(cfg["neighborhoods"])
    return [z for nb in nbs for z in cfg["neighborhoods"][nb]["zips"]]


def cmd_rentals_pull(args, db, cfg, rdb=None) -> bool:
    """Realtor.com for-rent listings via HomeHarvest. Returns True when every zip came back."""
    rdb = rdb or _rdb(args, cfg)
    hh = HomeHarvest(rdb, cfg)
    if not hh.enabled:
        print("homeharvest disabled in config"); return False
    ok, got = True, []
    for z in _zips(cfg, args.neighborhood):
        try:
            got.extend(hh.fetch_rentals([z]))
        except SourceError as e:
            log.warning("rentals %s: %s", z, e)
            ok = False
    st = rentals.ingest(rdb, got, cfg)
    print(f"realtor.com: {json.dumps(st)}")
    return ok and not args.neighborhood


def cmd_rentals_import_gmail(args, db, cfg, rdb=None):
    rdb = rdb or _rdb(args, cfg)
    ad = GmailAlerts(rdb, cfg)
    if not ad.enabled or not ad.scfg.get("rental_query"):
        print("gmail rentals disabled in config"); return
    try:
        found = ad.fetch_rental_alerts()
    except SourceError as e:
        print(f"gmail: {e}"); return
    print(f"gmail: {len(found)} homes parsed, {json.dumps(rentals.ingest(rdb, found, cfg))}")


def cmd_rentals_add(args, db, cfg):
    """A rental seen somewhere the tracker can't read: a yard sign, Craigslist, Facebook, a friend."""
    rdb = _rdb(args, cfg)
    r = Rental(address=args.address, source=args.source, city=args.city, zip_code=args.zip,
               neighborhood=config.neighborhood_for(args.zip, args.city, cfg), rent=args.rent, beds=args.beds,
               baths=args.baths, sqft=args.sqft, property_type=args.type, kind=rental_kind(args.type, None, args.address), url=args.url)
    if not r.neighborhood:
        print("zip is outside the tracked neighborhoods"); return
    rid, is_new, changes = rentals.upsert(rdb, r, cfg)
    if args.text:
        notes = rentals.load_notes()
        n = notes.setdefault(rid, {})
        n["notes"] = ((n.get("notes") or "") + "\n" + args.text).strip()
        rentals.save_notes(notes)
    print(f"{'added' if is_new else 'updated'} {rid} {changes}")


def cmd_rental_note(args, db, cfg):
    rdb = _rdb(args, cfg)
    row = rentals.find(rdb, args.id)
    if not row:
        print("no such rental"); return
    notes = rentals.load_notes()
    n = notes.setdefault(row["id"], {})
    if args.status:
        if args.status == "clear":
            n.pop("status", None)
        else:
            n["status"] = args.status
    if args.text:
        n["notes"] = ((n.get("notes") or "") + "\n" + args.text).strip()
    if not n:
        notes.pop(row["id"])
    rentals.save_notes(notes)
    print(f"{row['address']}: {json.dumps(n)}")


def cmd_rentals_export(args, db, cfg, rdb=None):
    rdb = rdb or _rdb(args, cfg)
    print(f"exported rentals {rentals.export(rdb, cfg)}")


def cmd_run_rentals(args, db, cfg):
    """Daily: pull Realtor.com, read rental alert emails, retire vanished listings, export."""
    rdb = _rdb(args, cfg)
    t0 = datetime.now()
    ns = argparse.Namespace(neighborhood=None)
    pulled_ok = cmd_rentals_pull(ns, db, cfg, rdb)
    if cfg["sources"]["gmail"].get("enabled"):
        cmd_rentals_import_gmail(ns, db, cfg, rdb)
    summary = {"pulled_ok": pulled_ok, "gone": rentals.mark_gone(rdb, cfg, pulled_ok=pulled_ok)}
    rdb.upsert("runs", {"ts": t0.isoformat(timespec="seconds"), "summary": json.dumps(summary)}, ["ts"])
    print(json.dumps({**summary, **rentals.export(rdb, cfg)}))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tracker", description="West Side LA housing tracker")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--db", default=None, help="sqlite path (default data/tracker.sqlite)")
    ap.add_argument("--rentals-db", default=None, help="rentals sqlite path (default data/rentals.sqlite)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(fn=cmd_init)
    p = sub.add_parser("pull", help="fetch active listings"); p.add_argument("--neighborhood"); p.add_argument("--source", choices=["rentcast", "homeharvest"]); p.add_argument("--all-sources", action="store_true"); p.set_defaults(fn=cmd_pull)
    p = sub.add_parser("import-csv", help="import Redfin CSV exports (default: data/inbox/*.csv)"); p.add_argument("paths", nargs="*"); p.set_defaults(fn=cmd_import_csv)
    p = sub.add_parser("import-redfin-dc", help="import Redfin Data Center tsv"); p.add_argument("paths", nargs="*"); p.set_defaults(fn=cmd_import_redfin_dc)
    sub.add_parser("import-gmail", help="parse alert emails").set_defaults(fn=cmd_import_gmail)
    p = sub.add_parser("comps", help="refresh sold comps / market stats"); p.add_argument("--neighborhood"); p.add_argument("--source", choices=["homeharvest", "rentcast"]); p.set_defaults(fn=cmd_comps)
    p = sub.add_parser("enrich", help="rent estimates + property records via RentCast"); p.add_argument("--limit", type=int, default=20); p.add_argument("--rent-only", action="store_true"); p.set_defaults(fn=cmd_enrich)
    p = sub.add_parser("score"); p.add_argument("--top", type=int, default=20); p.set_defaults(fn=cmd_score)
    sub.add_parser("rates", help="pull FRED rate series and evaluate alerts").set_defaults(fn=cmd_rates)
    sub.add_parser("market", help="pull FRED market series + Zillow Research files").set_defaults(fn=cmd_market)
    sub.add_parser("export", help="write docs/data/*.json").set_defaults(fn=cmd_export)
    p = sub.add_parser("note", help="red-flag checklist / notes / cost overrides"); p.add_argument("id", help="listing id or address fragment"); p.add_argument("--flag", action="append", help="e.g. fire_zone=true"); p.add_argument("--text"); p.add_argument("--override", action="append", help="e.g. insurance=18000"); p.set_defaults(fn=cmd_note)
    p = sub.add_parser("add", help="manual listing"); p.add_argument("--address", required=True); p.add_argument("--city"); p.add_argument("--zip"); p.add_argument("--price", type=float, required=True); p.add_argument("--sqft", type=float); p.add_argument("--beds", type=float); p.add_argument("--baths", type=float); p.add_argument("--rent", type=float); p.add_argument("--mls"); p.add_argument("--url"); p.set_defaults(fn=cmd_add)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("gmail-auth", help="one-time OAuth flow; writes token.json").set_defaults(fn=cmd_gmail_auth)
    p = sub.add_parser("digest"); p.add_argument("--dry-run", action="store_true"); p.set_defaults(fn=cmd_digest)
    p = sub.add_parser("run-weekly"); p.add_argument("--dry-run", action="store_true"); p.add_argument("--enrich-limit", type=int, default=5, help="RentCast AVM calls per run (50/month free tier)"); p.set_defaults(fn=cmd_run_weekly)

    p = sub.add_parser("rentals-pull", help="for-rent homes from Realtor.com (HomeHarvest)"); p.add_argument("--neighborhood"); p.set_defaults(fn=cmd_rentals_pull)
    sub.add_parser("rentals-import-gmail", help="parse saved rental-search alert emails").set_defaults(fn=cmd_rentals_import_gmail)
    p = sub.add_parser("rentals-add", help="add a rental by hand (yard sign, Craigslist, word of mouth)")
    p.add_argument("--address", required=True); p.add_argument("--zip", required=True); p.add_argument("--city"); p.add_argument("--rent", type=float, required=True)
    p.add_argument("--beds", type=float); p.add_argument("--baths", type=float); p.add_argument("--sqft", type=float)
    p.add_argument("--type", help="house, townhome, condo, duplex"); p.add_argument("--url"); p.add_argument("--source", default="manual", help="e.g. craigslist, sign, facebook")
    p.add_argument("--text", help="note"); p.set_defaults(fn=cmd_rentals_add)
    p = sub.add_parser("rental-note", help="triage status / notes for a rental"); p.add_argument("id", help="rental id or address fragment")
    p.add_argument("--status", choices=rentals.TRIAGE + ["clear"]); p.add_argument("--text"); p.set_defaults(fn=cmd_rental_note)
    sub.add_parser("rentals-export", help="write docs/data/rentals.json").set_defaults(fn=cmd_rentals_export)
    sub.add_parser("run-rentals", help="daily rentals job: pull, alerts, retire, export").set_defaults(fn=cmd_run_rentals)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    cfg = config.load()
    db = DB(args.db) if args.db else DB()
    try:
        return args.fn(args, db, cfg)
    finally:
        db.close()

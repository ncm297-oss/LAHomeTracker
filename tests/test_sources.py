import json
from pathlib import Path

import pytest

from tracker import config, dedupe
from tracker.db import DB
from tracker.sources.gmail_alerts import html_to_text, parse_alert_text
from tracker.sources.redfin_csv import RedfinCSV
from tracker.sources.rentcast import RentCast

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def db(tmp_path):
    d = DB(tmp_path / "t.sqlite")
    yield d
    d.close()


@pytest.fixture
def cfg():
    return config.load()


def test_redfin_csv_splits_active_and_sold(db, cfg):
    listings, comps = RedfinCSV(db, cfg).parse_file(FIX / "redfin_sample.csv")
    assert len(listings) == 4 and len(comps) == 3
    pearl = next(l for l in listings if l.address == "2417 Pearl St")
    assert pearl.neighborhood == "ocean_park" and pearl.mls_number == "26-512345" and pearl.list_price == 2495000
    assert next(l for l in listings if "Gorham" in l.address).status == "pending"
    assert comps[0]["sold_date"] == "2026-08-14" and comps[0]["neighborhood"] == "ocean_park"
    unit = next(l for l in listings if "Grand View" in l.address)
    assert unit.hoa == 450


def test_rentcast_mapping_and_price_band(db, cfg):
    items = json.loads((FIX / "rentcast_listings.json").read_text())
    rc = RentCast(db, cfg)
    l = rc.to_listing(items[0])
    assert l.mls_number == "26-512345" and l.original_list_price == 2695000 and len(l.price_history) == 2
    assert l.price_history[1].price == 2495000
    assert l.zip_code == "90405"


def test_gmail_parser_redfin_text():
    text = (FIX / "alert_redfin.txt").read_text()
    found = parse_alert_text(text, "gmail:redfin", "2026-09-20")
    assert [l.address for l in found] == ["2417 Pearl St", "11711 Gorham Ave"]
    pearl = found[0]
    assert pearl.list_price == 2495000 and pearl.original_list_price == 2695000
    assert [p.event for p in pearl.price_history] == ["list", "cut"]
    assert pearl.source_url.startswith("https://www.redfin.com/")
    gorham = found[1]
    assert gorham.mls_number == "26-512348" and gorham.neighborhood == "brentwood"


def test_gmail_parser_zillow_html():
    text = html_to_text((FIX / "alert_zillow.html").read_text())
    found = parse_alert_text(text, "gmail:zillow")
    assert len(found) == 1
    assert found[0].list_price == 2199000 and found[0].original_list_price == 2299000
    assert "zillow.com" in found[0].source_url


def test_dedupe_merges_sources_and_records_cut(db, cfg):
    listings, _ = RedfinCSV(db, cfg).parse_file(FIX / "redfin_sample.csv")
    for l in listings:
        dedupe.upsert(db, l, today="2026-09-01")
    assert db.one("SELECT COUNT(*) AS n FROM listings")["n"] == 4
    # Same house arrives from RentCast with a lower price and richer history.
    rc = RentCast(db, cfg).to_listing(json.loads((FIX / "rentcast_listings.json").read_text())[0])
    rc.list_price = 2395000
    lid, is_new, changes = dedupe.upsert(db, rc, today="2026-09-22")
    assert not is_new
    row = db.one("SELECT * FROM listings WHERE id=?", (lid,))
    assert row["list_price"] == 2395000 and row["original_list_price"] == 2695000
    assert set(row["sources"].split(",")) == {"redfin_csv", "rentcast"}
    hist = db.price_history(lid)
    assert any(h["event"] == "cut" and h["price"] == 2395000 for h in hist)
    # Address-only match (no MLS) from an email also lands on the same row.
    email = parse_alert_text("2417 Pearl Street, Santa Monica, CA 90405 $2,295,000", "gmail:redfin", "2026-09-23")[0]
    lid2, is_new2, _ = dedupe.upsert(db, email, today="2026-09-23")
    assert lid2 == lid and not is_new2
    assert db.one("SELECT list_price FROM listings WHERE id=?", (lid,))["list_price"] == 2295000

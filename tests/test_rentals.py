from pathlib import Path

import pytest

from tracker import config, rentals
from tracker.models import Rental
from tracker.normalize import rental_kind
from tracker.sources.gmail_alerts import html_to_text, parse_rental_alert_text, rental_source_for
from tracker.sources.homeharvest_adapter import rental_from_row

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def cfg():
    return config.load()


@pytest.fixture
def rdb(tmp_path, cfg):
    d = rentals.open_db(cfg, tmp_path / "r.sqlite")
    yield d
    d.close()


def test_rental_kind_fixes_source_mislabels():
    assert rental_kind("SINGLE_FAMILY") == "house"
    assert rental_kind("TOWNHOMES") == "townhome"
    assert rental_kind("CONDOS", "Front corner townhome with roof deck") == "townhome"
    assert rental_kind("CONDOS", "High-rise with valet") == "condo"
    assert rental_kind("APARTMENT") == "apartment"
    # High-rise units filed as houses.
    assert rental_kind("SINGLE_FAMILY", "penthouse with views", "10336 Wilshire Blvd Apt 703") == "condo"
    assert rental_kind("TOWNHOMES", "2-story townhouse-style unit", "1040 Glendon Ave Unit 4177") == "townhome"
    # A unit number that repeats the street number is still a house.
    assert rental_kind("SINGLE_FAMILY", "", "4170 Madison Ave Unit 4170") == "house"
    assert rental_kind("SINGLE_FAMILY", "", "1227 23rd St Unit A") == "house"
    assert rental_kind(None) is None


def test_realtor_row_mapping(cfg):
    row = {"street": "1141 19th St", "unit": "Apt 4", "city": "Santa Monica", "zip_code": "90403", "list_price": 6300,
           "beds": 3, "full_baths": 2, "half_baths": 1, "sqft": 1700, "style": "TOWNHOMES", "mls": "WECA",
           "office_name": "KW Advisors", "agent_name": None, "property_url": "https://www.realtor.com/rentals/details/x",
           "list_date": "2026-09-23T00:00:00", "text": "Bright townhome", "primary_photo": "https://ap.rdcpix.com/x.jpg",
           "latitude": 34.03, "longitude": -118.48, "year_built": float("nan"), "lot_sqft": None}
    r = rental_from_row(row, cfg)
    assert r.address == "1141 19th St Apt 4" and r.neighborhood == "santa_monica" and r.kind == "townhome"
    assert r.baths == 2.5 and r.listed_date == "2026-09-23" and r.feed == "WECA" and r.listed_by == "KW Advisors"
    assert r.year_built is None
    assert rental_from_row({"street": "1 Main St", "list_price": None}, cfg) is None


def test_fit(cfg):
    base = {"rent": 6500, "beds": 3, "sqft": 1600, "kind": "house"}
    assert rentals.fit(base, cfg) == ("match", [])
    assert rentals.fit({**base, "sqft": None}, cfg) == ("match", [])          # unknown sqft is not held against it
    assert rentals.fit({**base, "sqft": 1200}, cfg) == ("near", ["sqft"])
    assert rentals.fit({**base, "kind": "condo"}, cfg) == ("near", ["type"])
    assert rentals.fit({**base, "rent": 7900, "beds": 2}, cfg) == ("out", ["rent", "beds"])


def test_zillow_rental_alert_html(cfg):
    found = parse_rental_alert_text(html_to_text((FIX / "alert_zillow_rental.html").read_text()), "zillow", cfg)
    assert [r.address for r in found] == ["4163 Lincoln Ave", "1141 19th St APT 4", "1600 Vine St"]
    lincoln, nineteenth, vine = found
    assert (lincoln.rent, lincoln.beds, lincoln.baths, lincoln.sqft, lincoln.kind) == (6495, 3, 3, 1910, "house")
    assert lincoln.url == "https://click.zillow.com/ls/click?upn=abc123" and lincoln.neighborhood == "culver_city"
    assert nineteenth.rent == 6250 and nineteenth.kind == "townhome" and nineteenth.url.endswith("def456")
    assert vine.neighborhood is None                                            # Hollywood: outside the search
    assert not rentals.in_scope(vine, cfg)


def test_apartments_alert_details_above_address(cfg):
    found = parse_rental_alert_text((FIX / "alert_apartments_rental.txt").read_text(), "apartments.com", cfg)
    alberta, greenlawn = found
    assert (alberta.address, alberta.rent, alberta.beds, alberta.sqft) == ("12024 Alberta Dr", 6995, 5, 1648)
    assert alberta.url == "https://www.apartments.com/12024-alberta-dr-culver-city-ca/nhbb59m/"
    assert (greenlawn.rent, greenlawn.beds, greenlawn.sqft) == (7050, 3, 1680)
    assert greenlawn.url.endswith("2mytcy9/")


def test_rental_parser_ignores_sale_prices(cfg):
    assert parse_rental_alert_text("3811 Mountain View Ave, Los Angeles, CA 90066 $2,199,000 4 bd", "zillow", cfg) == []
    assert rental_source_for("Westside Rentals <alerts@westsiderentals.com>") == "apartments.com"
    assert rental_source_for("Zillow <rentals@mail.zillow.com>") == "zillow"


def _r(**kw):
    base = dict(address="1141 19th St Apt 4", source="realtor.com", city="Santa Monica", zip_code="90403",
                neighborhood="santa_monica", rent=6300, beds=3, sqft=1700, kind="townhome",
                url="https://www.realtor.com/rentals/details/x")
    base.update(kw)
    return Rental(**base)


def test_merge_across_sources_and_rent_changes(rdb, cfg):
    rid, is_new, _ = rentals.upsert(rdb, _r(), cfg, today="2026-09-20")
    assert is_new
    # Same home from a Zillow alert, written differently and quoting another rent: merged, link
    # added, but the daily pull keeps ownership of the rent figure.
    z = _r(address="1141 19th St #4", source="zillow", rent=6250, url="https://click.zillow.com/a", beds=None, sqft=None)
    rid2, is_new2, changes = rentals.upsert(rdb, z, cfg, today="2026-09-21")
    assert rid2 == rid and not is_new2 and not any(c.startswith("rent") for c in changes)
    assert rdb.one("SELECT rent FROM rentals WHERE id=?", (rid,))["rent"] == 6300
    assert {l["source"] for l in rdb.all("SELECT source FROM rental_links WHERE rental_id=?", (rid,))} == {"realtor.com", "zillow"}
    # A cut seen by the pull is recorded.
    _, _, changes = rentals.upsert(rdb, _r(rent=6100), cfg, today="2026-09-24")
    assert changes == ["rent 6300 -> 6100"]
    hist = rdb.all("SELECT rent, event FROM rental_price_history WHERE rental_id=? ORDER BY date", (rid,))
    assert [(h["rent"], h["event"]) for h in hist] == [(6300, "list"), (6100, "cut")]
    row = rdb.one("SELECT * FROM rentals WHERE id=?", (rid,))
    assert row["original_rent"] == 6300 and row["last_seen"] == "2026-09-24"


def test_mark_gone(rdb, cfg):
    rentals.upsert(rdb, _r(), cfg, today="2026-09-20")
    rentals.upsert(rdb, _r(address="12024 Alberta Dr", zip_code="90230", neighborhood="culver_city", source="apartments.com",
                           url="https://www.apartments.com/x"), cfg, today="2026-09-10")
    # A failed pull says nothing about pulled listings.
    assert rentals.mark_gone(rdb, cfg, today="2026-09-25", pulled_ok=False) == 0
    assert rentals.mark_gone(rdb, cfg, today="2026-09-25") == 1        # pulled, unseen for 5 days
    assert rentals.mark_gone(rdb, cfg, today="2026-10-02") == 1        # email-only, unseen 22 days
    rid, _, changes = rentals.upsert(rdb, _r(), cfg, today="2026-10-03")
    assert "back on market" in changes
    assert rdb.one("SELECT status, gone_date FROM rentals WHERE id=?", (rid,)) == {"status": "active", "gone_date": None}


def test_export_payload(rdb, cfg, tmp_path):
    rentals.upsert(rdb, _r(), cfg, today="2026-09-20")
    rentals.upsert(rdb, _r(address="4058 Wade St", zip_code="90066", neighborhood="mar_vista", rent=5800), cfg, today="2026-09-20")
    counts = rentals.export(rdb, cfg, out_dir=tmp_path, today="2026-09-28")
    assert counts == {"total": 2, "active": 2, "matches": 1}
    import json
    doc = json.loads((tmp_path / "rentals.json").read_text())
    wade = next(r for r in doc["rentals"] if r["address"] == "4058 Wade St")
    assert wade["fit"] == "near" and wade["misses"] == ["rent"] and wade["days_listed"] == 8
    assert wade["links"][0]["source"] == "realtor.com"

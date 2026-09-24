import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from tracker import alerts, config, dedupe, export, scoring
from tracker.db import DB
from tracker.sources.redfin_csv import RedfinCSV

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def db(tmp_path):
    d = DB(tmp_path / "t.sqlite")
    yield d
    d.close()


@pytest.fixture
def cfg():
    return config.load()


def seed(db, cfg):
    listings, comps = RedfinCSV(db, cfg).parse_file(FIX / "redfin_sample.csv")
    for l in listings:
        dedupe.upsert(db, l, today="2026-09-01")
    # enough comps for a median: 3 from the CSV + synthetic
    today = date.today()
    for c in comps:
        c["sold_date"] = (today - timedelta(days=40)).isoformat()
        db.upsert("comps", c, ["id"])
    for i in range(6):
        db.upsert("comps", {"id": f"syn{i}", "neighborhood": "ocean_park", "address": f"{i} Synthetic St", "address_norm": f"{i} SYNTHETIC ST",
                            "zip_code": "90405", "sold_price": 2_400_000, "sold_date": (today - timedelta(days=30 + i)).isoformat(),
                            "sqft": 1600, "beds": 3, "baths": 2, "list_price": 2_450_000, "days_on_market": 30, "property_type": "SFR",
                            "source": "test", "fetched_at": "2026-09-01T00:00:00"}, ["id"])
    for l in db.all("SELECT id, list_price FROM listings"):
        db.update("listings", l["id"], {"est_rent": l["list_price"] * 0.0035, "last_sale_price": l["list_price"] * 1.05})


def test_scores_rank_cheaper_ppsf_higher(db, cfg):
    seed(db, cfg)
    rows = {r["id"]: r for r in scoring.score_all(db, cfg)}
    pearl = db.one("SELECT id FROM listings WHERE address='2417 Pearl St'")["id"]
    s = rows[pearl]
    assert 0 <= s["score"] <= 100
    assert s["components"]["ppsf"] is not None and s["components"]["breakeven"] is not None
    assert s["components"]["below_last_sale"] > 50   # asking below seller's purchase
    assert "breakeven" in s["verdict"]
    # Cutting the price raises the score.
    db.update("listings", pearl, {"list_price": 2_000_000})
    rows2 = {r["id"]: r for r in scoring.score_all(db, cfg)}
    assert rows2[pearl]["score"] > s["score"]


def test_missing_components_are_dropped_not_zeroed(cfg):
    s = scoring.score_listing({"list_price": 2_000_000, "sqft": None, "days_on_market": None}, [], {}, cfg, None)
    assert s["components"]["ppsf"] is None and s["coverage"] < 1
    assert s["score"] == 0  # only 'cuts' available and it is 0


def test_export_writes_all_files(db, cfg, tmp_path):
    seed(db, cfg)
    scoring.score_all(db, cfg)
    meta = export.export_all(db, cfg, out_dir=tmp_path / "out")
    for name in ("listings", "rates", "market", "meta"):
        assert (tmp_path / "out" / f"{name}.json").exists()
    listings = json.loads((tmp_path / "out" / "listings.json").read_text())
    assert listings[0]["score"] >= listings[-1]["score"]
    assert set(listings[0]["flags"]) == set(export.RED_FLAGS)
    assert meta["counts"]["comps"] == 9


def test_rate_alerts(db, cfg, tmp_path, monkeypatch):
    today = date.today()
    db.put_series("OBMMIJUMBO30YF", [(today.isoformat(), 4.0)])           # cheap jumbo -> leverage alert
    db.put_series("MORTGAGE30US", [(today.isoformat(), 6.2)])
    db.put_series("DGS10", [((today - timedelta(days=31)).isoformat(), 5.1), (today.isoformat(), 4.5)])
    scen = tmp_path / "scenarios.yaml"
    scen.write_text("scenarios:\n  - name: 50% down jumbo\n    rate: 7.3\n")
    monkeypatch.setattr(alerts, "SCENARIOS_PATH", scen)
    kinds = {a["kind"] for a in alerts.evaluate(db, cfg)}
    assert kinds == {"leverage", "refi", "ten_year"}
    db.put_series("OBMMIJUMBO30YF", [(today.isoformat(), 7.3)])
    assert "leverage" not in {a["kind"] for a in alerts.evaluate(db, cfg)}

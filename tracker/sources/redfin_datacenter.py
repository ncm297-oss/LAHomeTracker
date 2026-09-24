"""Importer for Redfin Data Center market-tracker files (zip_code_market_tracker.tsv000.gz
or the city file). They are hundreds of MB, so this is a manual import from data/inbox
rather than a weekly download."""
from __future__ import annotations

import csv
import gzip
import io
from pathlib import Path

from .base import SourceAdapter

_FIELDS = {
    "median_sale_price": "redfin_median_sale_price",
    "median_ppsf": "redfin_median_ppsf",
    "median_dom": "redfin_median_dom",
    "avg_sale_to_list": "redfin_sale_to_list",
    "inventory": "redfin_inventory",
    "months_of_supply": "redfin_months_of_supply",
    "price_drops": "redfin_price_drops",
    "homes_sold": "redfin_homes_sold",
}


class RedfinDataCenter(SourceAdapter):
    name = "redfin_datacenter"

    def import_file(self, path: Path | str) -> int:
        zips = {z for nb in self.cfg["neighborhoods"].values() for z in nb["zips"]}
        opener = gzip.open if str(path).endswith(".gz") else open
        n = 0
        with opener(path, "rt", encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                region = row.get("region", "")
                if (row.get("property_type") or "") != "All Residential":
                    continue
                geo = None
                if region.startswith("Zip Code:"):
                    z = region.split(":")[1].strip()
                    if z in zips:
                        geo = z
                elif region == "Los Angeles, CA metro area":
                    geo = "metro"
                if not geo:
                    continue
                d = row.get("period_end") or row.get("period_begin")
                for src, dst in _FIELDS.items():
                    v = row.get(src)
                    if v not in (None, ""):
                        try:
                            n += self.db.put_series(dst, [(d, float(v))], geo=geo)
                        except ValueError:
                            pass
        self.count(str(path), n=0)
        return n

    def inbox_files(self) -> list[Path]:
        inbox = Path(self.cfg["sources"]["redfin_csv"].get("inbox", "data/inbox"))
        return sorted(list(inbox.glob("*.tsv000*")) + list(inbox.glob("*market_tracker*"))) if inbox.exists() else []

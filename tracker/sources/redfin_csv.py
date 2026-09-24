"""Importer for Redfin's search-results CSV download (the "Download All" button).
Drop files into data/inbox/; active rows become listings, sold rows become comps."""
from __future__ import annotations

import csv
import hashlib
from datetime import datetime
from pathlib import Path

from ..config import neighborhood_for
from ..models import Listing, PricePoint
from ..normalize import normalize_address, parse_int, parse_money
from .base import SourceAdapter


def _col(row: dict, prefix: str):
    for k, v in row.items():
        if k and k.strip().upper().startswith(prefix):
            return v
    return None


def _date(s: str | None) -> str | None:
    if not s:
        return None
    for fmt in ("%B-%d-%Y", "%m/%d/%Y", "%Y-%m-%d", "%b-%d-%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


class RedfinCSV(SourceAdapter):
    name = "redfin_csv"

    def parse_file(self, path: Path | str) -> tuple[list[Listing], list[dict]]:
        listings: list[Listing] = []
        comps: list[dict] = []
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                addr = (_col(row, "ADDRESS") or "").strip()
                if not addr:
                    continue
                city = (_col(row, "CITY") or "").strip() or None
                zip5 = (str(_col(row, "ZIP") or "").strip())[:5] or None
                price = parse_money(_col(row, "PRICE"))
                sqft = parse_money(_col(row, "SQUARE FEET"))
                sold_date = _date(_col(row, "SOLD DATE"))
                status = (_col(row, "STATUS") or "").strip().lower()
                sale_type = (_col(row, "SALE TYPE") or "").strip().lower()
                nb = neighborhood_for(zip5, city, self.cfg)
                if sold_date or "sold" in status or "past sale" in sale_type:
                    if price and sold_date:
                        comps.append({
                            "id": hashlib.sha1(f"{normalize_address(addr, zip5)}|{sold_date}".encode()).hexdigest()[:16],
                            "neighborhood": nb, "address": addr, "address_norm": normalize_address(addr, zip5), "zip_code": zip5,
                            "sold_price": price, "sold_date": sold_date, "sqft": sqft, "beds": parse_money(_col(row, "BEDS")),
                            "baths": parse_money(_col(row, "BATHS")), "list_price": None, "days_on_market": parse_int(_col(row, "DAYS ON MARKET")),
                            "property_type": (_col(row, "PROPERTY TYPE") or "").strip() or None, "source": "redfin_csv",
                            "fetched_at": datetime.now().isoformat(timespec="seconds"),
                        })
                    continue
                lst = Listing(
                    address=addr, city=city, zip_code=zip5, list_price=price, sqft=sqft,
                    beds=parse_money(_col(row, "BEDS")), baths=parse_money(_col(row, "BATHS")),
                    lot_sqft=parse_money(_col(row, "LOT SIZE")), year_built=parse_int(_col(row, "YEAR BUILT")),
                    property_type=(_col(row, "PROPERTY TYPE") or "").strip() or None,
                    days_on_market=parse_int(_col(row, "DAYS ON MARKET")), hoa=parse_money(_col(row, "HOA")),
                    status="pending" if "pending" in status or "contingent" in status else "active",
                    mls_number=(_col(row, "MLS#") or "").strip() or None, source_url=(_col(row, "URL") or "").strip() or None,
                    sources=["redfin_csv"], latitude=parse_money(_col(row, "LATITUDE")), longitude=parse_money(_col(row, "LONGITUDE")),
                    neighborhood=nb,
                )
                if price:
                    lst.price_history.append(PricePoint(datetime.now().date().isoformat(), price, "list", "redfin_csv"))
                listings.append(lst)
        self.count(str(path), n=0)
        return listings, comps

    def inbox_files(self) -> list[Path]:
        inbox = Path(self.scfg.get("inbox", "data/inbox"))
        return sorted(inbox.glob("*.csv")) if inbox.exists() else []

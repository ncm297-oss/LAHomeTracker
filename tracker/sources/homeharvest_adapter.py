"""HomeHarvest adapter (unofficial Realtor.com scraper). Fallback for listings and the
main source of sold comps. Imported lazily; if the package is missing or it gets
blocked (403) we raise SourceError and the job carries on with other sources."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime

from ..config import neighborhood_for
from ..models import Listing, PricePoint
from ..normalize import normalize_address
from .base import SourceAdapter, SourceError

log = logging.getLogger("tracker")


def _f(v):
    try:
        if v is None or v != v:  # NaN
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _s(v):
    return None if v is None or (isinstance(v, float) and v != v) else str(v)


class HomeHarvest(SourceAdapter):
    name = "homeharvest"

    def _scrape(self, location: str, listing_type: str, past_days: int | None = None):
        try:
            from homeharvest import scrape_property  # type: ignore
        except ImportError as e:
            raise SourceError("homeharvest not installed (pip install homeharvest)") from e
        kwargs = {"location": location, "listing_type": listing_type}
        if past_days:
            kwargs["past_days"] = past_days
        try:
            df = scrape_property(**kwargs)
        except Exception as e:  # noqa: BLE001 - library raises plain Exceptions on 403
            self.count(listing_type, ok=False)
            raise SourceError(f"homeharvest {listing_type} {location}: {e}") from e
        self.count(listing_type)
        self.polite_pause(float(self.scfg.get("pause_seconds", 3)))
        return df.to_dict("records") if df is not None else []

    def _address(self, r: dict) -> str:
        parts = [r.get("street"), r.get("unit")]
        return " ".join(str(p) for p in parts if p and str(p) != "nan").strip()

    def fetch_listings(self, neighborhood: str) -> list[Listing]:
        nb = self.cfg["neighborhoods"][neighborhood]
        band = self.cfg["price_band"]
        out = []
        for z in nb["zips"]:
            for r in self._scrape(f"{z}, CA", "for_sale"):
                price = _f(r.get("list_price"))
                if not price or not (band["min"] * 0.9 <= price <= band["max"] * 1.1):
                    continue
                if (_s(r.get("style")) or "").upper() in ("LAND", "MOBILE", "FARM"):
                    continue
                addr = self._address(r)
                if not addr:
                    continue
                lst = Listing(
                    address=addr, city=_s(r.get("city")), zip_code=(_s(r.get("zip_code")) or "")[:5] or None,
                    list_price=price, sqft=_f(r.get("sqft")), beds=_f(r.get("beds")),
                    baths=(_f(r.get("full_baths")) or 0) + 0.5 * (_f(r.get("half_baths")) or 0) or None,
                    lot_sqft=_f(r.get("lot_sqft")), year_built=int(_f(r.get("year_built")) or 0) or None,
                    property_type=_s(r.get("style")), days_on_market=int(_f(r.get("days_on_mls")) or 0) or None,
                    listed_date=(_s(r.get("list_date")) or "")[:10] or None,
                    last_sale_price=_f(r.get("last_sold_price")) or None, last_sale_date=(_s(r.get("last_sold_date")) or "")[:10] or None,
                    hoa=_f(r.get("hoa_fee")), mls_number=_s(r.get("mls_id")), source_url=_s(r.get("property_url")),
                    sources=["homeharvest"], latitude=_f(r.get("latitude")), longitude=_f(r.get("longitude")),
                )
                lst.neighborhood = neighborhood_for(lst.zip_code, lst.city, self.cfg) or neighborhood
                if lst.listed_date:
                    lst.price_history.append(PricePoint(lst.listed_date, price, "list", "homeharvest"))
                out.append(lst)
        return out

    def fetch_sold(self, neighborhood: str) -> list[dict]:
        nb = self.cfg["neighborhoods"][neighborhood]
        past = int(self.scfg.get("past_days_sold", 270))
        out = []
        for z in nb["zips"]:
            for r in self._scrape(f"{z}, CA", "sold", past_days=past):
                price = _f(r.get("sold_price")) or _f(r.get("list_price"))
                addr = self._address(r)
                sold_date = (_s(r.get("last_sold_date")) or "")[:10]
                if not price or not addr or not sold_date:
                    continue
                zip5 = (_s(r.get("zip_code")) or "")[:5]
                out.append({
                    "id": hashlib.sha1(f"{normalize_address(addr, zip5)}|{sold_date}".encode()).hexdigest()[:16],
                    "neighborhood": neighborhood_for(zip5, _s(r.get("city")), self.cfg) or neighborhood,
                    "address": addr, "address_norm": normalize_address(addr, zip5), "zip_code": zip5 or None,
                    "sold_price": price, "sold_date": sold_date, "sqft": _f(r.get("sqft")), "beds": _f(r.get("beds")),
                    "baths": (_f(r.get("full_baths")) or 0) + 0.5 * (_f(r.get("half_baths")) or 0) or None,
                    "list_price": _f(r.get("list_price")), "days_on_market": int(_f(r.get("days_on_mls")) or 0) or None,
                    "property_type": _s(r.get("style")), "source": "homeharvest",
                    "fetched_at": datetime.now().isoformat(timespec="seconds"),
                })
        return out

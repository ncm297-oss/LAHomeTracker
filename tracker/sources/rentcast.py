"""RentCast API adapter (primary). Docs: https://developers.rentcast.io

Endpoints used:
  GET /listings/sale        active listings by zip (paged; no price filter server-side)
  GET /avm/rent/long-term   rent estimate for a listing
  GET /properties           property record: last sale price/date, lot, year built
  GET /markets              zip-level sale/rental statistics with history
Every network hit is counted in request_log; responses are cached on disk so
re-runs inside the TTL are free.
"""
from __future__ import annotations

import logging
from datetime import date

from ..config import secret, neighborhood_for
from ..models import Listing, PricePoint
from .base import SourceAdapter, SourceError

log = logging.getLogger("tracker")

_TYPE_MAP = {"Single Family": "Single Family", "Condo": "Condo", "Townhouse": "Townhouse", "Multi-Family": "Multi-Family"}


class RentCast(SourceAdapter):
    name = "rentcast"

    def __init__(self, db, cfg):
        super().__init__(db, cfg)
        self.key = secret("RENTCAST_API_KEY")
        self.base = self.scfg.get("base_url", "https://api.rentcast.io/v1")

    @property
    def enabled(self) -> bool:
        return super().enabled and bool(self.key)

    _dead: str | None = None  # set after an auth/billing error so a run can't burn the quota on repeats

    def _get(self, path: str, params: dict, ttl_hours: float, endpoint: str):
        if not self.key:
            raise SourceError("RENTCAST_API_KEY not set")
        if self._dead:
            raise SourceError(f"rentcast disabled for this run: {self._dead}")
        try:
            return self.cached_get(f"{self.base}{path}", params=params,
                                   headers={"X-Api-Key": self.key, "Accept": "application/json"},
                                   ttl_hours=ttl_hours, endpoint=endpoint)
        except SourceError as e:
            msg = str(e)
            if "HTTP 401" in msg or "HTTP 403" in msg or "HTTP 429" in msg:
                self._dead = msg[:160]
                log.warning("rentcast: %s -- skipping further RentCast calls this run", self._dead)
            raise

    # ---- listings ----
    def fetch_listings(self, neighborhood: str) -> list[Listing]:
        nb = self.cfg["neighborhoods"][neighborhood]
        band = self.cfg["price_band"]
        limit = int(self.scfg.get("listing_limit", 500))
        out: list[Listing] = []
        for z in nb["zips"]:
            offset = 0
            while True:
                data = self._get("/listings/sale", {"zipCode": z, "status": "Active", "limit": limit, "offset": offset},
                                 ttl_hours=20, endpoint="listings/sale")
                if not isinstance(data, list):
                    break
                for item in data:
                    lst = self.to_listing(item)
                    if lst is None:
                        continue
                    if lst.list_price and not (band["min"] * 0.9 <= lst.list_price <= band["max"] * 1.1):
                        continue
                    if lst.property_type and lst.property_type not in self.cfg["property_types"]:
                        continue
                    lst.neighborhood = neighborhood_for(lst.zip_code, lst.city, self.cfg) or neighborhood
                    out.append(lst)
                if len(data) < limit:
                    break
                offset += limit
        return out

    def to_listing(self, item: dict) -> Listing | None:
        addr = item.get("formattedAddress") or item.get("addressLine1")
        if not addr:
            return None
        hist = []
        for d, h in sorted((item.get("history") or {}).items()):
            if h.get("price"):
                hist.append(PricePoint(d[:10], float(h["price"]), h.get("event", "list").lower().replace("sale listing", "list"), "rentcast"))
        lst = Listing(
            address=item.get("addressLine1") or addr.split(",")[0],
            city=item.get("city"), zip_code=str(item.get("zipCode") or "")[:5] or None, state=item.get("state") or "CA",
            list_price=item.get("price"), sqft=item.get("squareFootage"), beds=item.get("bedrooms"), baths=item.get("bathrooms"),
            lot_sqft=item.get("lotSize"), year_built=item.get("yearBuilt"), property_type=_TYPE_MAP.get(item.get("propertyType"), item.get("propertyType")),
            days_on_market=item.get("daysOnMarket"), listed_date=(item.get("listedDate") or "")[:10] or None,
            hoa=(item.get("hoa") or {}).get("fee") if isinstance(item.get("hoa"), dict) else None,
            status="active" if (item.get("status") or "").lower() == "active" else (item.get("status") or "active").lower(),
            mls_number=item.get("mlsNumber"), sources=["rentcast"],
            latitude=item.get("latitude"), longitude=item.get("longitude"),
            price_history=hist,
            extra={"rentcast_id": item.get("id"), "mls": item.get("mlsName"), "agent": (item.get("listingAgent") or {}).get("name")},
        )
        if hist:
            lst.original_list_price = max(p.price for p in hist)
        return lst

    # ---- enrichment ----
    def fetch_rent_estimate(self, listing: Listing) -> float | None:
        if not self.scfg.get("rent_estimates", True):
            return None
        params = {"address": f"{listing.address}, {listing.city}, {listing.state} {listing.zip_code}"}
        for k, v in (("propertyType", listing.property_type), ("bedrooms", listing.beds), ("bathrooms", listing.baths), ("squareFootage", listing.sqft)):
            if v:
                params[k] = v
        data = self._get("/avm/rent/long-term", params, ttl_hours=24 * 30, endpoint="avm/rent")
        return float(data["rent"]) if isinstance(data, dict) and data.get("rent") else None

    def fetch_property(self, listing: Listing) -> dict:
        """Last sale, lot size, year built, tax assessment from the property record."""
        params = {"address": f"{listing.address}, {listing.city}, {listing.state} {listing.zip_code}"}
        data = self._get("/properties", params, ttl_hours=24 * 90, endpoint="properties")
        rec = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {})
        out = {}
        if rec.get("lastSalePrice"):
            out["last_sale_price"] = float(rec["lastSalePrice"])
            out["last_sale_date"] = (rec.get("lastSaleDate") or "")[:10] or None
        for src, dst in (("lotSize", "lot_sqft"), ("yearBuilt", "year_built"), ("squareFootage", "sqft")):
            if rec.get(src):
                out[dst] = rec[src]
        assessments = rec.get("taxAssessments") or {}
        if assessments:
            latest = assessments[max(assessments)]
            out["assessed_value"] = latest.get("value")
            out["assessed_land"] = latest.get("land")
        return out

    # ---- market stats (comp fallback) ----
    def fetch_market(self, neighborhood: str) -> dict:
        """Median $/sqft and DOM for the neighborhood's zips; stored as series with geo=neighborhood."""
        nb = self.cfg["neighborhoods"][neighborhood]
        ppsf, dom, prices = [], [], []
        for z in nb["zips"]:
            data = self._get("/markets", {"zipCode": z, "dataType": "Sale", "historyRange": 6}, ttl_hours=24 * 7, endpoint="markets")
            sale = (data or {}).get("saleData") or {}
            if sale.get("medianPricePerSquareFoot"):
                ppsf.append(sale["medianPricePerSquareFoot"])
            if sale.get("medianDaysOnMarket"):
                dom.append(sale["medianDaysOnMarket"])
            if sale.get("medianPrice"):
                prices.append(sale["medianPrice"])
            for h in (sale.get("history") or {}).values():
                d = (h.get("date") or "")[:10]
                if d and h.get("medianPricePerSquareFoot"):
                    self.db.put_series("rentcast_median_ppsf", [(d, h["medianPricePerSquareFoot"])], geo=z)
        today = date.today().isoformat()
        out = {}
        if ppsf:
            out["median_ppsf"] = sum(ppsf) / len(ppsf)
            self.db.put_series("rentcast_median_ppsf", [(today, out["median_ppsf"])], geo=neighborhood)
        if dom:
            out["median_dom"] = sum(dom) / len(dom)
            self.db.put_series("rentcast_median_dom", [(today, out["median_dom"])], geo=neighborhood)
        if prices:
            self.db.put_series("rentcast_median_price", [(today, sum(prices) / len(prices))], geo=neighborhood)
        return out

"""The single normalized listing schema. Every source adapter produces these."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from typing import Any

from .normalize import normalize_address


@dataclass
class PricePoint:
    date: str            # YYYY-MM-DD
    price: float
    event: str = "list"  # list | cut | raise | sold | pending | removed
    source: str = ""


@dataclass
class Listing:
    address: str
    city: str | None = None
    zip_code: str | None = None
    state: str = "CA"
    neighborhood: str | None = None
    list_price: float | None = None
    original_list_price: float | None = None
    sqft: float | None = None
    beds: float | None = None
    baths: float | None = None
    lot_sqft: float | None = None
    year_built: int | None = None
    property_type: str | None = None
    days_on_market: int | None = None
    listed_date: str | None = None
    last_sale_price: float | None = None
    last_sale_date: str | None = None
    est_rent: float | None = None
    est_value: float | None = None
    hoa: float | None = None
    status: str = "active"          # active | pending | sold | removed
    mls_number: str | None = None
    source_url: str | None = None
    sources: list[str] = field(default_factory=list)
    latitude: float | None = None
    longitude: float | None = None
    price_history: list[PricePoint] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def address_norm(self) -> str:
        return normalize_address(self.address, self.zip_code)

    @property
    def id(self) -> str:
        """Stable id: MLS number when known, else normalized address."""
        key = f"mls:{self.mls_number.strip().upper()}" if self.mls_number else f"addr:{self.address_norm}"
        return hashlib.sha1(key.encode()).hexdigest()[:16]

    @property
    def ppsf(self) -> float | None:
        if self.list_price and self.sqft:
            return self.list_price / self.sqft
        return None

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("price_history")
        d["id"] = self.id
        d["address_norm"] = self.address_norm
        d["sources"] = ",".join(sorted(set(self.sources)))
        return d

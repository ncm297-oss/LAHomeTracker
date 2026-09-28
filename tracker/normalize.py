"""Address normalization for dedupe. Stdlib only; good enough for LA street addresses."""
from __future__ import annotations

import re

_SUFFIX = {
    "STREET": "ST", "AVENUE": "AVE", "AV": "AVE", "BOULEVARD": "BLVD", "DRIVE": "DR", "ROAD": "RD",
    "PLACE": "PL", "COURT": "CT", "LANE": "LN", "TERRACE": "TER", "CIRCLE": "CIR", "WAY": "WAY",
    "PARKWAY": "PKWY", "HIGHWAY": "HWY", "TRAIL": "TRL", "WALK": "WALK", "PROMENADE": "PROM",
}
_DIR = {"NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W"}
_UNIT_RE = re.compile(r"\b(?:UNIT|APT|APARTMENT|STE|SUITE|#)\s*([A-Z0-9-]+)", re.I)
_ORDINAL_RE = re.compile(r"\b(\d+)(ST|ND|RD|TH)\b")


def normalize_address(address: str, zip_code: str | None = None) -> str:
    """'1234 Ocean Park Blvd., Unit 3, Santa Monica, CA 90405' -> '1234 OCEAN PARK BLVD #3 90405'."""
    if not address:
        return ""
    a = address.upper()
    unit = None
    m = _UNIT_RE.search(a)
    if m:
        unit = m.group(1)
        a = a[: m.start()] + a[m.end():]
    # Keep only the street line: drop everything after the first comma once we've captured the unit.
    a = a.split(",")[0]
    a = re.sub(r"[.\-']", " ", a)
    a = re.sub(r"[^A-Z0-9# ]", " ", a)
    tokens = []
    for t in a.split():
        t = _DIR.get(t, t)
        t = _SUFFIX.get(t, t)
        tokens.append(t)
    a = " ".join(tokens)
    a = _ORDINAL_RE.sub(r"\1", a)
    if unit:
        a += f" #{unit}"
    if zip_code:
        a += f" {str(zip_code)[:5]}"
    return a.strip()


def type_group(property_type: str | None) -> str:
    """Collapse source-specific property types into sfr / condo / multi / other so $/sqft and
    rent/sqft are compared like with like (a duplex at $400/sf is not a cheap house)."""
    t = (property_type or "").lower()
    if not t:
        return "other"
    if "multi" in t or "duplex" in t or "triplex" in t or "units" in t or "income" in t:
        return "multi"
    if "condo" in t or "town" in t or "co-op" in t or "coop" in t or "apartment" in t:
        return "condo"
    if "single" in t or "sfr" in t or "house" in t:
        return "sfr"
    return "other"


_UPPER_UNIT_RE = re.compile(r"(?:APT|UNIT|STE|SUITE|#)\s*(\d{3,})", re.I)


def rental_kind(property_type: str | None, text: str | None = None, address: str | None = None) -> str | None:
    """house | townhome | condo | duplex | apartment | None. Sources mislabel in both directions:
    townhomes filed as condos (a condo whose description says townhome counts as a townhome), and
    high-rise units filed as houses/townhomes (a 3+ digit unit number like 'Apt 703', with no
    townhome wording, counts as a condo)."""
    t = (property_type or "").lower().replace("_", " ")
    if not t:
        return None
    town = re.search(r"town ?(?:home|house)", (text or "").lower())
    if "condo" in t or "coop" in t or "co-op" in t:
        return "townhome" if town else "condo"
    if "duplex" in t or "triplex" in t or "multi" in t:
        return "duplex"
    if "apartment" in t:
        return "apartment"
    if "town" in t or "row" in t:
        kind = "townhome"
    elif "single" in t or "house" in t or "sfr" in t:
        kind = "house"
    else:
        return None
    um = _UPPER_UNIT_RE.search(address or "")
    street_no = re.match(r"\s*(\d+)", address or "")
    # '4170 Madison Ave Unit 4170' is a house whose unit repeats the street number.
    if um and not town and not (street_no and um.group(1).startswith(street_no.group(1))):
        return "condo"
    return kind


def parse_money(s) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = re.sub(r"[^0-9.]", "", str(s))
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def parse_int(s) -> int | None:
    v = parse_money(s)
    return int(v) if v is not None else None

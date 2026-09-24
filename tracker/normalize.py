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

"""FRED series. Uses the API when FRED_API_KEY is set, otherwise the public
fredgraph.csv endpoint (no key needed, same data)."""
from __future__ import annotations

import csv
import io
from datetime import date, timedelta

from ..config import secret
from .base import SourceAdapter


class FRED(SourceAdapter):
    name = "fred"

    @property
    def enabled(self) -> bool:
        return True

    def fetch_series(self, series_id: str, years: int = 3) -> list[tuple[str, float | None]]:
        start = (date.today() - timedelta(days=365 * years)).isoformat()
        key = secret("FRED_API_KEY")
        if key:
            data = self.cached_get("https://api.stlouisfed.org/fred/series/observations",
                                   {"series_id": series_id, "api_key": key, "file_type": "json", "observation_start": start},
                                   ttl_hours=12, endpoint=f"series/{series_id}")
            return [(o["date"], None if o["value"] == "." else float(o["value"])) for o in data.get("observations", [])]
        text = self.cached_get("https://fred.stlouisfed.org/graph/fredgraph.csv", {"id": series_id, "cosd": start},
                               ttl_hours=12, as_json=False, endpoint=f"fredgraph/{series_id}")
        out = []
        for row in csv.DictReader(io.StringIO(text)):
            d = row.get("observation_date") or row.get("DATE")
            v = row.get(series_id)
            out.append((d, None if v in (None, ".", "") else float(v)))
        return out

    def refresh(self, series_ids: list[str], years: int = 3) -> dict[str, int]:
        counts = {}
        for sid in series_ids:
            counts[sid] = self.db.put_series(sid, self.fetch_series(sid, years))
        return counts

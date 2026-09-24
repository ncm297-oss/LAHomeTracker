"""Zillow Research public CSVs (wide format: one row per region, one column per month).
Metro files feed the market panel; zip-level ZHVI / ZORI feed per-neighborhood priors."""
from __future__ import annotations

import csv
import io

from .base import SourceAdapter


class ZillowResearch(SourceAdapter):
    name = "zillow_research"

    def _rows(self, url: str):
        text = self.cached_get(url, ttl_hours=24 * 7, as_json=False, endpoint=url.rsplit("/", 1)[-1])
        return csv.DictReader(io.StringIO(text))

    @staticmethod
    def _points(row: dict) -> list[tuple[str, float | None]]:
        pts = []
        for k, v in row.items():
            if k and len(k) == 10 and k[4] == "-" and k[7] == "-":
                try:
                    pts.append((k, float(v) if v not in ("", None) else None))
                except ValueError:
                    pass
        return pts

    def refresh(self) -> dict[str, int]:
        mcfg = self.cfg["market"]["zillow"]
        metro = mcfg["metro_name"]
        zips = {z for nb in self.cfg["neighborhoods"].values() for z in nb["zips"]}
        counts: dict[str, int] = {}
        for key, url in mcfg["files"].items():
            n = 0
            for row in self._rows(url):
                name = row.get("RegionName", "")
                rtype = (row.get("RegionType") or "").lower()
                if key.endswith("_zip"):
                    if name.zfill(5) in zips:
                        n += self.db.put_series(f"zillow_{key}", self._points(row), geo=name.zfill(5))
                elif rtype in ("msa", "metro") and name == metro:
                    n += self.db.put_series(f"zillow_{key}", self._points(row), geo="metro")
                    break
            counts[key] = n
        return counts

"""Adapter interface plus shared HTTP/caching/request-counting helpers."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from ..config import CACHE_DIR
from ..db import DB
from ..models import Listing

log = logging.getLogger("tracker")


class SourceError(Exception):
    pass


class SourceAdapter:
    name = "base"

    def __init__(self, db: DB, cfg: dict):
        self.db = db
        self.cfg = cfg
        self.scfg = cfg.get("sources", {}).get(self.name, {})

    @property
    def enabled(self) -> bool:
        return bool(self.scfg.get("enabled", False))

    # Any of these may raise SourceError or return [] when unsupported.
    def fetch_listings(self, neighborhood: str) -> list[Listing]:
        return []

    def fetch_sold(self, neighborhood: str) -> list[dict]:
        """Sold comps as dicts matching the comps table columns."""
        return []

    def fetch_rent_estimate(self, listing: Listing) -> float | None:
        return None

    # ---- helpers ----
    def count(self, endpoint: str = "", n: int = 1, ok: bool = True) -> None:
        self.db.log_request(self.name, endpoint, n, ok)

    def cached_get(self, url: str, params: dict | None = None, headers: dict | None = None,
                   ttl_hours: float = 24, as_json: bool = True, endpoint: str = "") -> Any:
        """GET with an on-disk cache keyed on URL+params. Only real network hits are counted."""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha1(json.dumps([url, params], sort_keys=True).encode()).hexdigest()
        path = CACHE_DIR / f"{self.name}_{key}.{'json' if as_json else 'txt'}"
        if path.exists() and datetime.fromtimestamp(path.stat().st_mtime) > datetime.now() - timedelta(hours=ttl_hours):
            log.debug("cache hit %s %s", self.name, endpoint or url)
            return json.loads(path.read_text(encoding="utf-8")) if as_json else path.read_text(encoding="utf-8")
        resp = requests.get(url, params=params, headers=headers, timeout=60)
        self.count(endpoint or url, ok=resp.ok)
        if not resp.ok:
            raise SourceError(f"{self.name}: HTTP {resp.status_code} for {endpoint or url}: {resp.text[:200]}")
        data = resp.json() if as_json else resp.text
        path.write_text(json.dumps(data) if as_json else data, encoding="utf-8")
        return data

    @staticmethod
    def polite_pause(seconds: float) -> None:
        time.sleep(seconds)

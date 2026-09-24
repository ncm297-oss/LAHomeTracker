"""SQLite persistence. Thin helpers over sqlite3; rows come back as dicts."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
  id TEXT PRIMARY KEY,
  address TEXT NOT NULL,
  address_norm TEXT NOT NULL,
  city TEXT, zip_code TEXT, state TEXT, neighborhood TEXT,
  list_price REAL, original_list_price REAL,
  sqft REAL, beds REAL, baths REAL, lot_sqft REAL, year_built INTEGER, property_type TEXT,
  days_on_market INTEGER, listed_date TEXT,
  last_sale_price REAL, last_sale_date TEXT,
  est_rent REAL, est_value REAL, hoa REAL,
  status TEXT DEFAULT 'active',
  mls_number TEXT, source_url TEXT, sources TEXT,
  latitude REAL, longitude REAL,
  extra TEXT,
  first_seen TEXT, last_seen TEXT
);
CREATE INDEX IF NOT EXISTS idx_listings_mls ON listings(mls_number);
CREATE INDEX IF NOT EXISTS idx_listings_addr ON listings(address_norm);

CREATE TABLE IF NOT EXISTS price_history (
  listing_id TEXT NOT NULL, date TEXT NOT NULL, price REAL NOT NULL,
  event TEXT, source TEXT,
  UNIQUE(listing_id, date, price)
);

CREATE TABLE IF NOT EXISTS comps (
  id TEXT PRIMARY KEY,
  neighborhood TEXT, address TEXT, address_norm TEXT, zip_code TEXT,
  sold_price REAL, sold_date TEXT, sqft REAL, beds REAL, baths REAL,
  list_price REAL, days_on_market INTEGER, property_type TEXT, source TEXT,
  fetched_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_comps_nb ON comps(neighborhood, sold_date);

CREATE TABLE IF NOT EXISTS series (
  series TEXT NOT NULL, date TEXT NOT NULL, value REAL, geo TEXT DEFAULT '',
  UNIQUE(series, date, geo)
);

CREATE TABLE IF NOT EXISTS scores (
  listing_id TEXT PRIMARY KEY, scored_at TEXT, score REAL, components TEXT, breakeven_price REAL, verdict TEXT
);

CREATE TABLE IF NOT EXISTS request_log (
  source TEXT NOT NULL, ts TEXT NOT NULL, endpoint TEXT, n INTEGER DEFAULT 1, ok INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS runs (
  ts TEXT PRIMARY KEY, summary TEXT
);
"""


def _to_db(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return v


class DB:
    def __init__(self, path: Path | str = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # ---- generic helpers ----
    def all(self, sql: str, params: Iterable = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable = ()) -> dict | None:
        r = self.conn.execute(sql, tuple(params)).fetchone()
        return dict(r) if r else None

    def insert(self, table: str, row: dict) -> None:
        cols = list(row)
        self.conn.execute(
            f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [_to_db(row[c]) for c in cols],
        )
        self.conn.commit()

    def insert_ignore(self, table: str, row: dict) -> bool:
        cols = list(row)
        cur = self.conn.execute(
            f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [_to_db(row[c]) for c in cols],
        )
        self.conn.commit()
        return cur.rowcount > 0

    def upsert(self, table: str, row: dict, keys: list[str]) -> None:
        cols = list(row)
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c not in keys)
        self.conn.execute(
            f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
            f"ON CONFLICT({','.join(keys)}) DO UPDATE SET {updates}",
            [_to_db(row[c]) for c in cols],
        )
        self.conn.commit()

    def update(self, table: str, id_: str, fields: dict) -> None:
        if not fields:
            return
        sets = ",".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE {table} SET {sets} WHERE id=?", [_to_db(v) for v in fields.values()] + [id_])
        self.conn.commit()

    # ---- domain helpers ----
    def log_request(self, source: str, endpoint: str = "", n: int = 1, ok: bool = True) -> None:
        self.insert("request_log", {"source": source, "ts": datetime.now(timezone.utc).isoformat(), "endpoint": endpoint, "n": n, "ok": int(ok)})

    def request_counts(self, since: str | None = None) -> dict[str, int]:
        # Successful calls only, which is what RentCast's usage dashboard counts.
        sql = "SELECT source, SUM(n) AS n FROM request_log WHERE ok=1"
        params: tuple = ()
        if since:
            sql += " AND ts >= ?"
            params = (since,)
        sql += " GROUP BY source"
        return {r["source"]: r["n"] for r in self.all(sql, params)}

    def price_history(self, listing_id: str) -> list[dict]:
        return self.all("SELECT date, price, event, source FROM price_history WHERE listing_id=? ORDER BY date", (listing_id,))

    def series(self, name: str, geo: str = "", limit: int | None = None) -> list[dict]:
        sql = "SELECT date, value FROM series WHERE series=? AND geo=? ORDER BY date"
        rows = self.all(sql, (name, geo))
        return rows[-limit:] if limit else rows

    def put_series(self, name: str, points: Iterable[tuple[str, float | None]], geo: str = "") -> int:
        n = 0
        for d, v in points:
            if v is None:
                continue
            self.upsert("series", {"series": name, "date": d, "value": v, "geo": geo}, ["series", "date", "geo"])
            n += 1
        return n

    def close(self) -> None:
        self.conn.close()

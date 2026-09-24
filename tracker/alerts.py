"""Rate alerts evaluated against config thresholds and saved scenarios (data/scenarios.yaml)."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import yaml

from .config import DATA_DIR
from .db import DB

SCENARIOS_PATH = DATA_DIR / "scenarios.yaml"


def latest(db: DB, series: str) -> tuple[str, float] | None:
    rows = db.series(series, limit=1)
    return (rows[-1]["date"], rows[-1]["value"]) if rows else None


def value_on_or_before(db: DB, series: str, day: str) -> float | None:
    row = db.one("SELECT value FROM series WHERE series=? AND geo='' AND date<=? ORDER BY date DESC LIMIT 1", (series, day))
    return row["value"] if row else None


def after_tax_retained(rate: float, mode: str, years: int, fed_rate: float, cg_rate: float) -> float:
    if mode == "annual":
        return rate * (1 - fed_rate)
    if mode == "deferred":
        gross = (1 + rate) ** years
        return (gross - (gross - 1) * cg_rate) ** (1 / years) - 1
    return rate


def load_scenarios(path: Path = SCENARIOS_PATH) -> list[dict]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("scenarios", []) or []


def evaluate(db: DB, cfg: dict) -> list[dict]:
    a = cfg["alerts"]
    br = cfg["buyrent"]
    out: list[dict] = []

    jumbo = latest(db, "OBMMIJUMBO30YF")
    thirty = latest(db, "MORTGAGE30US")
    ten = latest(db, "DGS10")

    # 1. Leverage crossover: after-tax jumbo below after-tax retained return.
    if a.get("leverage_crossover") and jumbo:
        loan = cfg["price_band"]["max"] * 0.5
        shield = min(1.0, 750_000 / loan) * br["fed_rate"] + min(1.0, 1_000_000 / loan) * br["ca_rate"]
        cost = jumbo[1] / 100 * (1 - shield)
        cg = br["ltcg_fed_rate"] + br["niit_rate"] + br["ca_rate"]
        ret = after_tax_retained(a["retained_rate"], a["retained_tax_mode"], br["years"], br["fed_rate"], cg)
        if cost < ret:
            out.append({"level": "high", "kind": "leverage",
                        "title": f"Leverage crossover: jumbo {jumbo[1]:.2f}% costs {cost*100:.2f}% after tax, below {ret*100:.2f}% after-tax return on cash",
                        "detail": f"As of {jumbo[0]}. Financing now improves returns, not just liquidity."})

    # 2. Refi / delayed-financing trigger vs saved scenarios.
    if thirty:
        for s in load_scenarios():
            rate = float(s.get("rate", 0)) * (100 if float(s.get("rate", 0)) < 1 else 1)
            if rate and thirty[1] <= rate - a["refi_drop_points"]:
                out.append({"level": "high", "kind": "refi",
                            "title": f"30-yr at {thirty[1]:.2f}% is {rate - thirty[1]:.2f} pts below saved scenario '{s.get('name', '?')}' ({rate:.2f}%)",
                            "detail": f"Threshold {a['refi_drop_points']} pts. As of {thirty[0]}."})

    # 3. 10-year moved more than the threshold in ~a month.
    if ten:
        month_ago = (date.fromisoformat(ten[0]) - timedelta(days=30)).isoformat()
        prev = value_on_or_before(db, "DGS10", month_ago)
        if prev is not None and abs(ten[1] - prev) >= a["ten_year_move_points"]:
            out.append({"level": "medium", "kind": "ten_year",
                        "title": f"10-yr Treasury moved {ten[1] - prev:+.2f} pts in a month ({prev:.2f}% -> {ten[1]:.2f}%)",
                        "detail": f"As of {ten[0]}. Mortgage rates usually follow within weeks."})
    return out

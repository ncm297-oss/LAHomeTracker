"""Parity between tracker/buyrent.py and docs/calc.js (fixture from tests/gen_parity.js)."""
import json
import re
from pathlib import Path

import pytest

from tracker import buyrent

FIX = Path(__file__).parent / "fixtures" / "buyrent_parity.json"


def _snake(k: str) -> str:
    return re.sub(r"([A-Z])", lambda m: "_" + m.group(1).lower(), k)


@pytest.mark.parametrize("case", json.loads(FIX.read_text()))
def test_parity(case):
    p = buyrent.Params(**{_snake(k): v for k, v in case["input"].items()})
    assert buyrent.final_diff(p) == pytest.approx(case["finalDiff"], abs=1.0)
    if case["breakevenPrice"] is not None:
        assert buyrent.breakeven_price(p) == pytest.approx(case["breakevenPrice"], rel=1e-4)
    if case["rentEquivalent"] is not None:
        assert buyrent.rent_equivalent(p) == pytest.approx(case["rentEquivalent"], rel=1e-4)

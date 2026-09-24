"""Load config.yaml and .env, resolve project paths."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DOCS_DATA_DIR = ROOT / "docs" / "data"
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = DATA_DIR / "tracker.sqlite"
CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

load_dotenv(ROOT / ".env")

_config: dict | None = None


def load() -> dict:
    global _config
    if _config is None:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            _config = yaml.safe_load(f)
    return _config


def secret(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def zip_to_neighborhood(cfg: dict | None = None) -> dict[str, str]:
    cfg = cfg or load()
    out: dict[str, str] = {}
    for key, nb in cfg["neighborhoods"].items():
        for z in nb["zips"]:
            out[str(z)] = key
    return out


def neighborhood_for(zip_code: str | None, city: str | None, cfg: dict | None = None) -> str | None:
    cfg = cfg or load()
    if zip_code:
        key = zip_to_neighborhood(cfg).get(str(zip_code)[:5])
        if key:
            return key
    if city:
        c = city.strip().lower()
        for key, nb in cfg["neighborhoods"].items():
            if nb["name"].lower() == c:
                return key
    return None

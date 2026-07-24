"""The database is data/innovations.jsonl: one JSON record per line, append-only."""
import hashlib
import json
import re
from datetime import date, timedelta

from . import config


def record_id(url: str) -> str:
    return hashlib.sha1(url.strip().encode()).hexdigest()[:12]


def load_records(db_path=config.DB_PATH) -> list:
    if not db_path.exists():
        return []
    records = []
    with open(db_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


class Deduper:
    """Skip items already in the DB: same URL ever, or same name recently
    (the same story reported by several outlets under different URLs)."""

    def __init__(self, records: list):
        self.urls = {r["url"] for r in records}
        cutoff = (date.today() - timedelta(days=config.DEDUP_WINDOW_DAYS)).isoformat()
        self.recent_names = {
            _norm_name(r["name"])
            for r in records
            if (r.get("date_added") or "") >= cutoff and r.get("name")
        }

    def is_new(self, url: str, name: str = "") -> bool:
        if url in self.urls:
            return False
        if name and _norm_name(name) in self.recent_names:
            return False
        return True

    def add(self, url: str, name: str = ""):
        self.urls.add(url)
        if name:
            self.recent_names.add(_norm_name(name))


def recent_record_names(records: list, days: int = 14) -> list:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return [r["name"] for r in records if (r.get("date_added") or "") >= cutoff and r.get("name")]


def append_records(records: list, db_path=config.DB_PATH):
    with open(db_path, "a", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

"""Backfill news_date for records that lack one.

The site sorts the feed by news_date and (since 2026-07-30) counts its "last 7
days" stat from it, but records built from evergreen pages (use-case
inventories, transparency registers) or from sweep-model summaries can end up
without a publication date — and then sort as if published the day they were
ingested.

The fill is a chain, most truthful first:
  1. the page's own date metadata (trafilatura reads article:published_time,
     JSON-LD datePublished and friends);
  2. date_added, the rule for records whose source states no date: the live
     pipeline only ingests day-old sources, so ingestion ~= publication there.
     (The record's `year` field stays purely informational — mixing bare years
     into news_date confused more than it dated.)

Only records without a usable news_date are touched.

Run from the repo root:  python scripts/backfill_news_dates.py [--dry-run]
"""
import argparse
import json
import re
import sys
from pathlib import Path

import requests
import trafilatura

sys.path.insert(0, str(Path(__file__).parents[1]))

from observatory import config, extract  # noqa: E402

HAS_DATE = re.compile(r"^\d{4}")
FULL_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def page_date(url: str) -> str:
    try:
        resp = requests.get(url, timeout=config.REQUEST_TIMEOUT, headers=extract.UA)
        resp.raise_for_status()
        meta = trafilatura.extract_metadata(resp.text)
    except Exception:
        return ""
    date = (meta.date if meta and meta.date else "") or ""
    return date if FULL_DATE.match(date) else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report the fills, write nothing")
    args = ap.parse_args()

    lines = config.DB_PATH.read_text(encoding="utf-8").splitlines()
    records = [json.loads(l) for l in lines if l.strip()]

    filled = {"page": 0, "date_added": 0}
    for r in records:
        if HAS_DATE.match(str(r.get("news_date") or "")):
            continue
        date = page_date(r.get("url", ""))
        how = "page"
        if not date:
            date, how = r.get("date_added", ""), "date_added"
        if not date:
            print(f"  ?? nothing to fill from: {r.get('name','')[:60]}")
            continue
        r["news_date"] = date
        filled[how] += 1
        print(f"  {how:10} -> {date:10}  {r.get('name','')[:60]}")

    print(f"\nfilled {sum(filled.values())}: {filled['page']} from the page, "
          f"{filled['date_added']} from date_added")
    if args.dry_run:
        print("(dry run: not writing)")
        return
    with open(config.DB_PATH, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"Wrote {config.DB_PATH} ({len(records)} records)")


if __name__ == "__main__":
    main()

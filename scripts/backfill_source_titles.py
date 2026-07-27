"""Backfill source_titles for records that predate headline tracking.

The pipeline now stores each source URL's headline as it processes the article,
but every record before 2026-07-27 carries bare URLs. This fetches each cited
page once and keeps its og:title/<title>, so the frontend's source rows can show
"outlet + headline" for the back catalogue too. No LLM involved — plain HTTP.

Failures are left alone rather than guessed at: a URL that is paywalled, dead,
or behind a bot wall simply keeps its outlet-only rendering.

Run from the repo root:  python scripts/backfill_source_titles.py [--dry-run]
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

# Interstitials and error pages that arrive with HTTP 200 and a real <title>.
JUNK = re.compile(
    r"^(just a moment|access denied|attention required|are you a robot|page not found"
    r"|404|403|error|subscribe|sign in|log in)\b", re.I)


def page_title(url: str) -> str:
    try:
        resp = requests.get(url, timeout=config.REQUEST_TIMEOUT, headers=extract.UA)
        resp.raise_for_status()
        html = resp.text
    except Exception:
        return ""
    meta = trafilatura.extract_metadata(html)
    title = (meta.title if meta and meta.title else "")
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    title = extract.clean_headline(title)
    if len(title) < 15 or JUNK.match(title):
        return ""
    return title


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="fetch and report, write nothing")
    args = ap.parse_args()

    lines = config.DB_PATH.read_text(encoding="utf-8").splitlines()
    records = [json.loads(l) for l in lines if l.strip()]

    fetched, kept, failed = 0, 0, 0
    for record in records:
        titles = record.get("source_titles") or {}
        for url in [record.get("url", ""), *(record.get("sources") or [])]:
            if not url or titles.get(url):
                continue
            fetched += 1
            title = page_title(url)
            if title:
                titles[url] = title
                kept += 1
                print(f"  ok: {url[:60]} -> {title[:70]}")
            else:
                failed += 1
                print(f"  no title: {url[:80]}")
        if titles:
            record["source_titles"] = titles

    print(f"\n{fetched} URLs fetched, {kept} titles stored, {failed} without a usable title")
    if args.dry_run:
        print("(dry run: not writing)")
        return
    with open(config.DB_PATH, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"Wrote {config.DB_PATH} ({len(records)} records)")


if __name__ == "__main__":
    main()

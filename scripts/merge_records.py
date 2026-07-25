"""Merge duplicate records by id, using the same code path the pipeline uses.

  python scripts/merge_records.py --keep <id> --dup <id> [--dup <id> ...] [--no-llm]

The kept record keeps its id and date_added, so links already posted to Slack
(`#r=<id>`) keep resolving. Its canonical url, news_date and sources are
recomputed by observatory.merge. Set LLM_API_KEY to have the prose rewritten from
the duplicates; --no-llm falls back to a deterministic field fill.
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from observatory import config, db, merge  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", required=True, help="id of the record to keep")
    parser.add_argument("--dup", action="append", default=[], help="id to fold in (repeatable)")
    parser.add_argument("--no-llm", action="store_true", help="skip the enrichment call")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    records = db.load_records()
    by_id = {r["id"]: r for r in records}
    missing = [i for i in [args.keep, *args.dup] if i not in by_id]
    if missing:
        sys.exit(f"unknown id(s): {', '.join(missing)}")
    if not args.dup:
        sys.exit("nothing to merge: pass at least one --dup")

    keeper = by_id[args.keep]
    dups = [by_id[i] for i in args.dup]
    before = {"url": keeper["url"], "news_date": keeper.get("news_date"),
              "sources": list(keeper.get("sources") or [])}

    enrich = not args.no_llm and bool(config.LLM_API_KEY)
    if not enrich:
        print("(no LLM enrichment: filling fields deterministically)")
    merge.merge(keeper, dups, date.today().isoformat(), enrich=enrich)

    print(f"\nkeeper {keeper['id']} — {keeper['name']}")
    print(f"  url:        {before['url']}\n           -> {keeper['url']}")
    print(f"  news_date:  {before['news_date']} -> {keeper.get('news_date')}")
    print(f"  sources:    {len(before['sources'])} -> {len(keeper.get('sources') or [])}")
    for url in keeper.get("sources") or []:
        print(f"              {url}")
    for field in merge.TEXT_FIELDS:
        value = (keeper.get(field) or "").strip()
        print(f"  {field}: {value[:150] or '(empty)'}")
    print(f"  status: {keeper.get('status')}  autonomy_level: {keeper.get('autonomy_level')}")

    if args.dry_run:
        print("\n(dry run: not writing)")
        return

    dup_ids = set(args.dup)
    kept = [r for r in records if r["id"] not in dup_ids]
    db.write_records(kept)
    print(f"\nDatabase: {len(records)} -> {len(kept)} records")


if __name__ == "__main__":
    main()

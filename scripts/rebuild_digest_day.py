"""Rebuild one day's digest archive entry from the database.

The archive (data/digests.jsonl) is written once per run, and write_archive
replaces any existing entry for the same date so re-running a failed harvest does
not duplicate the day. That is right for a re-run, and wrong for two genuinely
different harvests on one date: on 2026-07-28 a manual test run replaced the
scheduled run's entry, and the day's note lost 7 of its 16 records while the
records themselves stayed in the database.

This rebuilds the entry from the records' own date_added and updated fields — the
cleaned truth — the same basis used when the archive was first reconstructed in
ef91d5b. Ledes are hand-written in the editorial register, so an existing lede is
preserved unless --lede is given.

    python scripts/rebuild_digest_day.py --date 2026-07-28 [--lede "..."] \
        [--scanned N] [--assessed N] [--dry-run]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from observatory import db, digest  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="YYYY-MM-DD to rebuild")
    ap.add_argument("--lede", default=None, help="replace the lede (kept as-is if omitted)")
    ap.add_argument("--scanned", type=int, default=None)
    ap.add_argument("--assessed", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    records = db.load_records()
    new = [r for r in records if r.get("date_added") == args.date]
    # A record added and enriched on the same day belongs under "new", not both.
    updated = [r for r in records
               if r.get("updated") == args.date and r.get("date_added") != args.date]
    if not new and not updated:
        sys.exit(f"no records dated {args.date}")

    existing = {}
    if digest.ARCHIVE_PATH.exists():
        for line in digest.ARCHIVE_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if row.get("date") == args.date:
                    existing = row

    row = digest.archive_row(
        new, args.date, updated,
        lede=args.lede if args.lede is not None else existing.get("lede", ""),
        scanned=args.scanned if args.scanned is not None else existing.get("scanned", 0),
        assessed=args.assessed if args.assessed is not None else existing.get("assessed", 0),
    )

    print(f"{args.date}: {len(existing.get('new') or [])} -> {len(row['new'])} new, "
          f"{len(existing.get('updated') or [])} -> {len(row['updated'])} updated")
    was = {x["id"] for x in existing.get("new") or []}
    for item in row["new"]:
        print(f"   {'+ ' if item['id'] not in was else '  '}{item['name'][:66]}")
    if args.dry_run:
        print("\n(dry run: not writing)")
        return
    digest.write_archive(row)
    print(f"\nwrote {digest.ARCHIVE_PATH}")


if __name__ == "__main__":
    main()

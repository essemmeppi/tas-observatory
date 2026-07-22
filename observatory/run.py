"""Daily observatory run: ingest -> filter/extract -> dedupe -> append -> Slack digest.

Usage: python -m observatory.run [--dry-run] [--no-slack] [--no-x] [--max-items N]
"""
import argparse
from datetime import date, timedelta

from . import config, db, digest, extract, llm, sources


def gather_items(no_x: bool) -> list:
    print("Fetching feeds")
    items = sources.fetch_all_feeds()
    if not no_x:
        print("Running X sweep")
        today = date.today()
        # The sweep occasionally returns an empty set on a first attempt; retry once.
        for attempt in range(2):
            try:
                got = sources.fetch_x_sweep(
                    from_date=(today - timedelta(days=1)).isoformat(),
                    to_date=today.isoformat(),
                )
            except Exception as e:
                print(f"  warning: x sweep failed ({e})")
                break
            if got:
                items += got
                break
    return items


def process_item(item: dict, deduper: db.Deduper, run_date: str) -> dict | None:
    url, title = item["url"], item.get("title", "")
    if not deduper.is_new(url, title):
        return None

    text = item.get("prefetched_text") or extract.extract_text(url)
    if not text:
        print(f"  no text: {title[:70]}")
        return None

    assessment = llm.assess_article(text, url, item.get("published", ""))
    if not assessment:
        print(f"  not relevant: {title[:70]}")
        return None
    if not deduper.is_new(url, assessment.get("name", "")):
        print(f"  duplicate story: {title[:70]}")
        return None

    record = {
        "id": db.record_id(url),
        "name": assessment.get("name", "") or title,
        "organisation": assessment.get("organisation", ""),
        "countries": assessment.get("countries") or [],
        "description": assessment.get("description", ""),
        "novelty": assessment.get("novelty", ""),
        "stakeholders": assessment.get("stakeholders", ""),
        "year": str(assessment.get("year", "")),
        "url": url,
        "source": item["source"],
        "date_added": run_date,
        "agentic": bool(assessment.get("agentic")),
        "tags": assessment.get("tags") or [],
        "layers": assessment.get("layers") or [],
    }
    deduper.add(url, record["name"])
    print(f"  ADDED [{'agentic' if record['agentic'] else 'ai-gov'}]: {record['name']}")
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="don't write the DB or post to Slack")
    parser.add_argument("--no-slack", action="store_true")
    parser.add_argument("--no-x", action="store_true", help="skip the X sweep")
    parser.add_argument("--max-items", type=int, default=config.MAX_ITEMS_PER_RUN)
    args = parser.parse_args()

    run_date = date.today().isoformat()
    existing = db.load_records()
    deduper = db.Deduper(existing)
    print(f"DB has {len(existing)} records")

    items = gather_items(no_x=args.no_x)
    # Drop exact-URL duplicates across sources before spending LLM calls.
    fresh = [i for i in items if deduper.is_new(i["url"], i.get("title", ""))]
    print(f"{len(items)} items fetched, {len(fresh)} new, processing up to {args.max_items}")

    new_records = []
    for item in fresh[: args.max_items]:
        try:
            record = process_item(item, deduper, run_date)
        except Exception as e:
            print(f"  error on {item['url']}: {e}")
            continue
        if record:
            new_records.append(record)

    print(f"\n{len(new_records)} new records")
    if not new_records:
        return

    if args.dry_run:
        print("(dry run: not writing DB, not posting to Slack)")
        return

    db.append_records(new_records)
    print(f"Appended to {config.DB_PATH}")

    text = digest.build_digest(new_records, run_date)
    print("\n" + text)
    if not args.no_slack:
        if digest.post_to_slack(text):
            print("Posted digest to Slack")


if __name__ == "__main__":
    main()

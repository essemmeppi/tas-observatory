"""Daily observatory run: ingest -> filter/extract -> dedupe -> append -> Slack digest.

Usage: python -m observatory.run [--dry-run] [--no-slack] [--no-x] [--max-items N]
"""
import argparse
import time
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
        print("Running web sweep")
        try:
            items += sources.fetch_web_sweep()
        except Exception as e:
            print(f"  warning: web sweep failed ({e})")
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
    if config.AGENTIC_ONLY and not assessment.get("agentic"):
        print(f"  not agentic: {title[:70]}")
        return None
    if not deduper.is_new(url, assessment.get("name", "")):
        print(f"  duplicate story: {title[:70]}")
        return None

    record = {
        "id": db.record_id(url),
        "name": assessment.get("name", "") or title,
        "organisation": assessment.get("organisation", ""),
        "countries": assessment.get("countries") or [],
        "country_codes": assessment.get("country_codes") or [],
        "description": assessment.get("description", ""),
        "novelty": assessment.get("novelty", ""),
        "stakeholders": assessment.get("stakeholders", ""),
        "agentic_rationale": assessment.get("agentic_rationale", ""),
        "tech_details": assessment.get("tech_details", ""),
        "providers": assessment.get("providers") or [],
        "autonomy_level": assessment.get("autonomy_level"),
        "status": assessment.get("status", "unclear"),
        "news_date": assessment.get("news_date", ""),
        "year": str(assessment.get("year", "")),
        "url": url,
        "sources": [],
        "source": item["source"],
        "date_added": run_date,
        "agentic": bool(assessment.get("agentic")),
        "tags": assessment.get("tags") or [],
        "layers": assessment.get("layers") or [],
        "functions": assessment.get("functions") or [],
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

    # Stop early rather than hit the workflow's hard 45-min kill, which would
    # lose the whole harvest (the commit step never runs on a killed job).
    deadline = time.monotonic() + config.TIME_BUDGET_MIN * 60
    new_records = []
    for item in fresh[: args.max_items]:
        if time.monotonic() > deadline:
            print(f"  time budget ({config.TIME_BUDGET_MIN} min) reached, stopping early")
            break
        try:
            record = process_item(item, deduper, run_date)
        except Exception as e:
            print(f"  error on {item['url']}: {e}")
            continue
        if record:
            new_records.append(record)

    # Editorial dedupe: one call over the whole batch catches the same story
    # reported by several outlets under different names, and re-tells of
    # recently stored records. Non-fatal: on failure we keep all records.
    if len(new_records) > 1:
        try:
            recent_names = db.recent_record_names(existing, days=14)
            verdict = llm.dedupe_batch(new_records, recent_names)
            drop = set(verdict["already_known"])
            for group in verdict["merge_groups"]:
                keep, dups = group[0], group[1:]
                if keep in drop or keep >= len(new_records):
                    continue
                for d in dups:
                    if 0 <= d < len(new_records):
                        new_records[keep]["sources"].append(new_records[d]["url"])
                        drop.add(d)
            if drop:
                dropped = [new_records[i]["name"] for i in sorted(drop) if i < len(new_records)]
                print(f"  editorial dedupe removed {len(dropped)}: " + "; ".join(dropped))
                new_records = [r for i, r in enumerate(new_records) if i not in drop]
        except Exception as e:
            print(f"  warning: editorial dedupe failed ({e}), keeping all")

    print(f"\n{len(new_records)} new records")
    if not new_records:
        return

    if args.dry_run:
        print("(dry run: not writing DB, not posting to Slack)")
        return

    db.append_records(new_records)
    print(f"Appended to {config.DB_PATH}")

    # Digest problems must never fail the run: the DB is already written and
    # a non-zero exit would skip the commit step and lose the harvest.
    try:
        text = digest.build_digest(new_records, run_date)
        print("\n" + text)
        if not args.no_slack and digest.post_to_slack(text):
            print("Posted digest to Slack")
    except Exception as e:
        print(f"  warning: digest/slack failed ({e})")


if __name__ == "__main__":
    main()

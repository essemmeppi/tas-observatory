"""Daily observatory run: ingest -> filter/extract -> dedupe -> merge/append -> Slack digest.

Usage: python -m observatory.run [--dry-run] [--no-slack] [--no-x] [--max-items N]
"""
import argparse
import collections
import itertools
import sys
import time
from datetime import date, timedelta

from . import config, db, digest, extract, llm, merge, sources, urls

DEGRADED_MARKER = config.ROOT / ".degraded"


class ExtractionFailed(RuntimeError):
    """The gate approved an article and extraction still could not parse it.

    Distinct from a verdict: the article is known to be wanted, so this counts
    towards the run's error tally rather than passing quietly as "not relevant".
    """


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


def _fair_order(items: list) -> list:
    """Each source's best item, then each source's second, and so on.

    Ordering by tier alone put whole languages at the back: non-English outlets
    are mostly absent from the tier list, so they defaulted to the lowest tier and
    the run's budget expired long before reaching them. The result was a database
    that is 70% US/UK with one record from a non-English domain out of 127.

    Round-robin across sources means a truncated run trims every source's tail
    equally instead of cutting whole languages. Tier still decides order *within*
    each round, so the best available source is always assessed first.
    """
    by_source = collections.defaultdict(list)
    for item in items:
        by_source[item["source"]].append(item)
    return [i for rnd in itertools.zip_longest(*by_source.values())
            for i in sorted((x for x in rnd if x), key=urls.tier_for)]


def prepare_items(items: list, deduper: db.Deduper) -> list:
    """Drop already-known and repeated items, then order the queue fairly.

    Everything here works on the feed metadata alone — no redirect decoding, no
    article fetch — so the expensive steps only ever run on items that survive.
    """
    fresh, seen_urls, seen_titles = [], set(), set()
    for item in items:
        url_key = urls.canonical_url(item["url"])
        # Two editions of the same Google News story carry different redirect
        # tokens but the identical "Headline - Outlet" title, so the title is the
        # only key that catches them before decoding.
        title_key = db._norm_name(item.get("title", ""))
        if not url_key or url_key in seen_urls:
            continue
        if title_key and title_key in seen_titles:
            continue
        if deduper.known_url(item["url"]):
            continue
        seen_urls.add(url_key)
        if title_key:
            seen_titles.add(title_key)
        fresh.append(item)
    return _fair_order(fresh)


def process_item(item: dict, deduper: db.Deduper, run_date: str) -> dict | None:
    """Assess one article into a record, or None.

    None means the link would not resolve, yielded no text, or the model judged it
    irrelevant or non-agentic. Duplicates are *not* handled here — they are folded
    into the records they repeat by resolve_duplicates at the end of the run, which
    can merge and enrich rather than merely discard.
    """
    url, title = item["url"], item.get("title", "")
    # Never spend a call twice on one URL, whatever the earlier verdict was.
    deduper.add(url)

    if not item.get("prefetched_text"):
        # Decode the Google News redirect only now, for an item we intend to
        # assess. Doing this for all ~375 feed entries up front was most of the
        # run's wall-clock, spent mostly on articles that were then discarded.
        resolved = sources.resolve_url(url)
        if not resolved:
            print(f"  could not resolve link: {title[:70]}")
            return None
        if resolved != url:
            # Check before recording: the real URL may be one we already hold,
            # with only the redirect token looking new.
            already_known = deduper.known_url(resolved)
            deduper.add(resolved)
            if already_known:
                print(f"  already in DB once resolved: {title[:60]}")
                return None
        url = resolved

    text = item.get("prefetched_text") or extract.extract_text(url)
    if not text:
        print(f"  no text: {title[:70]}")
        return None

    published = item.get("published", "")
    # Two stages on purpose. The gate is small; extraction carries the 12 layers,
    # the 70 government functions and a dozen generated prose fields, and ~83% of
    # screened articles are rejected — so it only runs on what survives.
    screen = llm.screen_article(text, url, published)
    if not screen.get("relevant"):
        print(f"  not relevant: {title[:70]}")
        return None
    if config.AGENTIC_ONLY and not screen.get("agentic"):
        print(f"  not agentic (gate): {title[:70]}")
        return None

    assessment = llm.extract_record(text, url, published)
    if not assessment:
        # Not a verdict — a defect. The gate already judged this relevant and
        # agentic, so losing it costs us a story we know we wanted; on 2026-07-26
        # that was a primary-source government roadmap. Log the URL so it can be
        # recovered, and let the caller count it as an error.
        raise ExtractionFailed(f"extraction failed after retries: {url}")
    if config.AGENTIC_ONLY and not assessment.get("agentic"):
        # One decision refined, not two: the full assessment has the layer and
        # function taxonomies in front of it, so it overrules the cheap gate.
        print(f"  not agentic (full assessment): {title[:70]}")
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
        # Headline keyed by URL, so the frontend can caption each source link.
        # The record's own fields describe the initiative, not the article.
        "source_titles": {url: extract.clean_headline(title)} if title else {},
        "source": item["source"],
        "date_added": run_date,
        "agentic": bool(assessment.get("agentic")),
        "tags": assessment.get("tags") or [],
        "types": assessment.get("types") or [],
        "layers": assessment.get("layers") or [],
        "functions": assessment.get("functions") or [],
    }
    print(f"  ADDED [{'agentic' if record['agentic'] else 'ai-gov'}]: {record['name']}")
    return record


def _name_target(candidate: dict, pool: list):
    """The stored record this candidate re-tells, by name, or None.

    Both records carry countries here, so the similarity branch of names_match is
    available — unlike the headline check in Deduper, which has no country to go on.
    """
    return next(
        (r for r in pool if db.names_match(
            candidate.get("name", ""), r.get("name", ""),
            candidate.get("countries"), r.get("countries"))),
        None,
    )


def _fallback_resolve(candidates: list, existing: list, run_date: str, enrich: bool = True) -> tuple:
    """Name-match-only resolution, for when the LLM verdict is unavailable.

    Catches an exact or contained name and nothing cleverer — it would have
    caught the hrreporter re-tell but not the Globe and Mail one — so any run
    that lands here is reported as degraded.
    """
    survivors, touched = [], []
    for candidate in candidates:
        target = _name_target(candidate, existing)
        if target is None:
            survivors.append(candidate)
            continue
        merge.merge(target, [candidate], run_date, enrich=enrich)
        touched.append(target)
        print(f"  name-match merge into '{target['name'][:50]}': {candidate['name'][:50]}")
    return survivors, touched, False


def resolve_duplicates(candidates: list, existing: list, run_date: str) -> tuple:
    """Fold re-tells into the records they repeat.

    Returns (records_to_insert, existing_records_touched, dedupe_ran). Within-batch
    groups resolve first: otherwise A can merge into B while B independently
    merges into a stored record, orphaning A's sources.
    """
    recent = db.recent_records(existing)
    try:
        verdict = llm.dedupe_batch(candidates, recent)
    except llm.BudgetExhausted:
        raise
    except Exception as e:
        print(f"  dedupe unavailable ({e}); falling back to name matching")
        return _fallback_resolve(candidates, existing, run_date)

    dropped = set()
    for group in verdict["merge_groups"]:
        idxs = [i for i in group
                if isinstance(i, int) and 0 <= i < len(candidates) and i not in dropped]
        if len(idxs) < 2:
            continue
        keeper, dups = candidates[idxs[0]], [candidates[i] for i in idxs[1:]]
        merge.merge(keeper, dups, run_date)
        dropped.update(idxs[1:])
        print(f"  same-day merge: {keeper['name'][:55]} "
              f"(+{len(dups)} outlet{'s' if len(dups) > 1 else ''})")

    touched = []
    for hit in verdict["already_known"]:
        i, j = hit["candidate"], hit["existing"]
        if not (0 <= i < len(candidates)) or i in dropped:
            continue
        if j is not None and 0 <= j < len(recent):
            target = recent[j]
        else:
            # Flagged as a re-tell but not of what. Try the free name check; if
            # that cannot place it either, keep the record rather than discard a
            # story on an unattributed verdict.
            target = _name_target(candidates[i], existing)
        if target is None:
            print(f"  unattributed re-tell, keeping: {candidates[i]['name'][:55]}")
            continue
        merge.merge(target, [candidates[i]], run_date)
        dropped.add(i)
        touched.append(target)
        print(f"  merged into '{target['name'][:50]}': {candidates[i]['name'][:50]}")

    survivors = [r for i, r in enumerate(candidates) if i not in dropped]
    print(f"  dedupe: {len(candidates)} candidates, {len(dropped)} merged away, {len(survivors)} new")
    return survivors, touched, True


def _finish(degraded: str | None):
    """A degraded run keeps its harvest but must not look like a clean one."""
    if not degraded:
        DEGRADED_MARKER.unlink(missing_ok=True)
        return
    DEGRADED_MARKER.write_text(degraded + "\n", encoding="utf-8")
    print(f"\n::warning::run degraded: {degraded}")
    sys.exit(1)


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
    DEGRADED_MARKER.unlink(missing_ok=True)
    degraded = None

    items = gather_items(no_x=args.no_x)
    fresh = prepare_items(items, deduper)
    print(f"{len(items)} items fetched, {len(fresh)} new, processing up to {args.max_items}")

    # Stop early rather than hit the workflow's hard 80-min kill, which would
    # lose the whole harvest (the commit step never runs on a killed job).
    deadline = time.monotonic() + config.TIME_BUDGET_MIN * 60
    queue = fresh[: args.max_items]
    new_records, touched, processed, errors = [], [], 0, 0
    for item in queue:
        if time.monotonic() > deadline:
            degraded = f"time budget ({config.TIME_BUDGET_MIN} min) reached"
            print(f"  {degraded}, stopping early")
            break
        try:
            record = process_item(item, deduper, run_date)
        except llm.BudgetExhausted as e:
            # Every further call would fail identically. On 2026-07-25 carrying
            # on produced 106 useless 402s and left the dedupe pass unfunded.
            degraded = f"LLM budget exhausted ({e})"
            print(f"  {degraded}; stopping the loop")
            break
        except Exception as e:
            # Log the headline, not the URL: an undecoded Google News link is a
            # 300-character token that tells you nothing about what failed.
            print(f"  error on '{item.get('title', '')[:60]}': {e}")
            processed += 1
            errors += 1
            continue
        processed += 1
        if record:
            new_records.append(record)

    unassessed = len(queue) - processed
    # A handful of dead links is normal; most of the batch failing is not, and it
    # should not be able to pass for a clean run either.
    if not degraded and errors >= max(3, processed // 2):
        degraded = f"{errors} of {processed} articles failed to assess"
    dedupe_ran = True
    if new_records:
        try:
            new_records, merged_into, dedupe_ran = resolve_duplicates(new_records, existing, run_date)
        except llm.BudgetExhausted as e:
            print(f"  dedupe skipped: LLM budget exhausted ({e})")
            degraded = degraded or f"LLM budget exhausted ({e})"
            new_records, merged_into, dedupe_ran = _fallback_resolve(
                new_records, existing, run_date, enrich=False)
        touched += merged_into
    else:
        print("  dedupe: no new records to check")

    # Same record can be touched twice (a source attached, then a merge).
    unique_touched = list({id(r): r for r in touched}.values())
    print(f"\n{len(new_records)} new records, {len(unique_touched)} existing records enriched")

    if args.dry_run:
        print("(dry run: not writing DB, not posting to Slack)")
        _finish(degraded)
        return

    if new_records or unique_touched:
        db.write_records(existing + new_records)
        print(f"Wrote {config.DB_PATH} ({len(existing) + len(new_records)} records)")

    # Digest problems must never fail the run early: the DB is already written
    # and an exception here would skip the commit step and lose the harvest.
    try:
        if new_records or unique_touched:
            # One lede, two renderings: the Slack message and the site's digest
            # archive carry the same text, so it is only generated (and only
            # has to be got right) once.
            lede = llm.write_digest_lede(new_records) if (config.LLM_API_KEY and new_records) else None
            text = digest.build_digest(
                new_records, run_date,
                enriched=unique_touched,
                degraded=degraded,
                unassessed=unassessed,
                dedupe_ran=dedupe_ran,
                scanned=len(fresh), assessed=processed,
                lede=lede,
            )
            print("\n" + text)
            digest.write_archive(digest.archive_row(
                new_records, run_date, unique_touched, lede,
                scanned=len(fresh), assessed=processed))
            print(f"Wrote digest archive entry for {run_date}")
            if not args.no_slack and digest.post_to_slack(text):
                print("Posted digest to Slack")
    except Exception as e:
        print(f"  warning: digest/slack failed ({e})")

    _finish(degraded)


if __name__ == "__main__":
    main()

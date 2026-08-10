"""Import a list of URLs through the pipeline's gate/extract/dedupe stages.

The nightly sources only look one day back, so initiatives collected in earlier
monitoring efforts can never enter on their own. This feeds a plain list of URLs
through the same run.process_item() -> run.resolve_duplicates() path the nightly
run uses: the gate stays the selector (AGENTIC_ONLY applies as usual), duplicates
merge into the records they repeat, and the record schema is byte-identical.

X/Twitter posts cannot be fetched directly, and some government sites sit behind
bot walls that serve interstitials instead of the article. Both go to the sweep
model (Grok via OpenRouter, the same one behind the nightly X/web sweeps), which
reads them through its own browsing and returns a summary used as
prefetched_text. Pages neither we nor the sweep model can read are reported at
the end for a human to source alternates.

Every URL's outcome is appended to a state file next to the list
(<list>.state.jsonl), so an interrupted or partial run resumes where it left
off and a re-run with replacement URLs only processes what is new. URLs whose
page could not be read anywhere are NOT checkpointed — a later run retries them.

Run from the repo root:
  python scripts/import_urls.py --file data/imports/2026-07-29-legacy-urls.txt [--max N] [--dry-run]
"""
import argparse
import contextlib
import io
import json
import re
import sys
import urllib.parse
from datetime import date
from pathlib import Path

import requests
import trafilatura

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parent))

from observatory import config, db, extract, llm, run, urls  # noqa: E402
from backfill_source_titles import JUNK  # noqa: E402

# The whole DB, expressed in the units resolve_duplicates understands.
FULL_HISTORY_DAYS = 36500
DEDUPE_CHUNK = 30
GROK_BATCH = 8

X_HOSTS = {"x.com", "twitter.com", "mobile.twitter.com"}

GROK_READ_PROMPT = (
    "Open and read each of the following URLs (news articles, official government "
    "pages, or X posts).\n\n{targets}\n\n"
    "Return ONLY a JSON array with one element per URL, in the same order:\n"
    '{{"url": "<the URL exactly as given>", '
    '"title": "<the page or post\'s headline>", '
    '"summary": "<3-5 sentences: what happened or what the page describes, which government '
    "body, which country, any named systems, models or vendors, and any dates or figures "
    'stated. Report only what the page actually says.>"}}\n'
    'Use {{"url": "...", "title": "", "summary": ""}} for a URL you cannot read.'
)


def is_x_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return host.removeprefix("www.") in X_HOSTS


def fetch_page(url: str) -> tuple[str, str | None]:
    """One fetch for both the page title and the article text.

    Same extraction and same 200-char floor as extract.extract_text, plus the
    og:title/<title> logic of backfill_source_titles — merged here so each URL
    costs one request instead of two. Interstitial titles ("just a moment",
    "access denied") are discarded so a bot wall cannot masquerade as a headline.
    """
    try:
        resp = requests.get(url, timeout=config.REQUEST_TIMEOUT, headers=extract.UA)
        resp.raise_for_status()
        html = resp.text
    except Exception:
        return "", None
    meta = trafilatura.extract_metadata(html)
    title = (meta.title if meta and meta.title else "")
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    title = extract.clean_headline(title)
    if len(title) < 15 or JUNK.match(title):
        title = ""
    text = trafilatura.extract(html, include_comments=False)
    if not text or len(text) < 200:
        return title, None
    return title, text[: config.MAX_ARTICLE_CHARS]


def _match_key(url: str) -> str:
    """Key for pairing the sweep model's echo of a URL back to our input."""
    m = re.search(r"/status/(\d+)", url)
    return m.group(1) if m else (urls.canonical_url(url) or url)


def grok_read(targets: list) -> dict:
    """url -> {"title", "summary"} via the sweep model; unreadable URLs map to None."""
    if not targets:
        return {}
    if not config.XSWEEP_MODEL or "openrouter" not in config.LLM_BASE_URL:
        print(f"  sweep-model read needs XSWEEP_MODEL + OpenRouter; skipping {len(targets)} URLs")
        return {u: None for u in targets}

    results = {}
    for i in range(0, len(targets), GROK_BATCH):
        batch = targets[i : i + GROK_BATCH]
        prompt = GROK_READ_PROMPT.format(targets="\n".join(batch))
        try:
            resp = requests.post(
                f"{config.LLM_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                json={
                    "model": config.XSWEEP_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "plugins": [{"id": "web", "engine": "native"}],
                },
                timeout=300,
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"] or ""
            match = re.search(r"\[.*\]", text, re.DOTALL)
            stories = json.loads(match.group(0)) if match else []
        except Exception as e:
            print(f"  sweep-model batch failed ({e}); {len(batch)} URLs left unread")
            stories = []

        by_key = {}
        for s in stories:
            if isinstance(s, dict) and s.get("url"):
                by_key[_match_key(s["url"])] = s
        for url in batch:
            s = by_key.get(_match_key(url))
            if s and (s.get("summary") or "").strip():
                results[url] = {"title": (s.get("title") or "").strip(),
                                "summary": s["summary"].strip()}
            else:
                results[url] = None
        done = sum(1 for u in batch if results[u])
        print(f"  sweep-model read: batch of {len(batch)}, {done} readable")
    return results


def load_state(state_path: Path) -> list:
    if not state_path.exists():
        return []
    return [json.loads(l) for l in state_path.read_text(encoding="utf-8").splitlines() if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="text file with one URL per line (# comments allowed)")
    ap.add_argument("--max", type=int, default=None, help="process at most N URLs this run")
    ap.add_argument("--dry-run", action="store_true", help="assess but don't write the DB (state is still saved)")
    args = ap.parse_args()

    list_path = Path(args.file)
    state_path = list_path.with_name(list_path.stem + ".state.jsonl")
    run_date = date.today().isoformat()

    existing = db.load_records()
    deduper = db.Deduper(existing)
    db_ids = {r["id"] for r in existing}
    print(f"DB has {len(existing)} records")

    state = load_state(state_path)
    processed = {urls.canonical_url(e["url"]) for e in state}
    # Accepted in an earlier (interrupted or dry) run but never committed. A
    # missing id alone is not enough: a record dedupe merged *away* also has no
    # id in the DB, but its URL lives on as a source of the record it merged
    # into — resurrecting it would duplicate what the merge already kept
    # (2026-08-08: a second Diia.AI record). known_url covers sources, so it
    # separates the two cases.
    carried = [e["record"] for e in state
               if e["outcome"] == "added" and e["record"]["id"] not in db_ids
               and not deduper.known_url(e["record"]["url"])]
    for r in carried:
        # Records checkpointed before the country vocabulary was pinned
        # (llm.canonical_country) may carry variants like "United States of
        # America"; fold them so the site's country filter stays one list.
        r["countries"] = list(dict.fromkeys(
            llm.canonical_country(c) for c in (r.get("countries") or [])))
    if state:
        print(f"state: {len(state)} URLs already processed, {len(carried)} accepted records carried over")

    raw = [l.strip() for l in list_path.read_text(encoding="utf-8").splitlines()]
    queue, seen, skipped_known = [], set(), 0
    for url in raw:
        if not url or url.startswith("#"):
            continue
        key = urls.canonical_url(url)
        if not key or key in seen:
            continue
        seen.add(key)
        if key in processed:
            continue
        if deduper.known_url(url):
            skipped_known += 1
            continue
        queue.append(url)
    if args.max is not None:
        queue = queue[: args.max]
    print(f"{len(seen)} distinct URLs in list, {skipped_known} already in DB, {len(queue)} to process")

    # Phase 1: direct fetch for everything fetchable. X posts and pages behind
    # bot walls fall through to the sweep model in phase 2.
    items_by_url, needs_grok = {}, []
    web_queue = [u for u in queue if not is_x_url(u)]
    for n, url in enumerate(web_queue, 1):
        title, text = fetch_page(url)
        if text:
            items_by_url[url] = {"title": title, "url": url, "published": "",
                                 "source": "manual_import", "prefetched_text": text}
        else:
            needs_grok.append(url)
        if n % 25 == 0 or n == len(web_queue):
            print(f"  fetched {n}/{len(web_queue)} pages, {len(needs_grok)} unreadable so far")

    x_urls = [u for u in queue if is_x_url(u)]
    if x_urls or needs_grok:
        print(f"Reading {len(x_urls)} X posts and {len(needs_grok)} blocked pages via the sweep model")
    unreadable = []
    for url, got in grok_read(x_urls + needs_grok).items():
        if got:
            items_by_url[url] = {
                "title": got["title"], "url": url, "published": "",
                "source": "manual_import_x" if is_x_url(url) else "manual_import_grok",
                "prefetched_text": got["summary"],
            }
        else:
            unreadable.append(url)

    # Phase 3: the pipeline proper, one URL at a time, checkpointing each verdict.
    accepted, counts = list(carried), {"added": 0, "rejected": 0, "error": 0}
    budget_dead = False
    with open(state_path, "a", encoding="utf-8") as state_fh:

        def note(url, outcome, reason="", record=None):
            counts[outcome] = counts.get(outcome, 0) + 1
            entry = {"url": url, "outcome": outcome, "reason": reason, "date": run_date}
            if record is not None:
                entry["record"] = record
            state_fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            state_fh.flush()

        todo = [u for u in queue if u in items_by_url]
        for n, url in enumerate(todo, 1):
            print(f"[{n}/{len(todo)}] {url[:90]}")
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    record = run.process_item(items_by_url[url], deduper, run_date)
            except llm.BudgetExhausted as e:
                print(buf.getvalue(), end="")
                print(f"  LLM budget exhausted ({e}); stopping — state is saved, re-run to resume")
                budget_dead = True
                break
            except Exception as e:
                print(buf.getvalue(), end="")
                note(url, "error", str(e)[:200])
                print(f"  error: {e}")
                continue
            out = buf.getvalue()
            print(out, end="")
            if record:
                note(url, "added", record=record)
                accepted.append(record)
            else:
                reason = ("not agentic" if "not agentic" in out
                          else "not relevant" if "not relevant" in out else "rejected")
                note(url, "rejected", reason)

    print(f"\nassessed this run: {counts['added']} added, {counts['rejected']} rejected, "
          f"{counts['error']} errors; {len(unreadable)} URLs unreadable")
    if budget_dead:
        sys.exit(1)

    # Chunked so one flaky verdict cannot sink the whole batch, with earlier
    # survivors joining the comparison pool for later chunks.
    survivors, touched = [], []
    if accepted:
        pool = existing[:]
        for i in range(0, len(accepted), DEDUPE_CHUNK):
            chunk = accepted[i : i + DEDUPE_CHUNK]
            try:
                got, hit, _ = run.resolve_duplicates(chunk, pool, run_date,
                                                     window_days=FULL_HISTORY_DAYS)
            except llm.BudgetExhausted as e:
                print(f"  dedupe hit budget wall ({e}); falling back to name matching")
                got, hit, _ = run._fallback_resolve(chunk, pool, run_date, enrich=False)
            survivors.extend(got)
            pool.extend(got)
            touched.extend(hit)

    unique_touched = list({id(r): r for r in touched}.values())
    print(f"\n{len(survivors)} new records, {len(unique_touched)} existing records enriched")
    if unreadable:
        print(f"\n{len(unreadable)} URLs unreadable everywhere (not checkpointed; a re-run retries them):")
        for u in unreadable:
            print(f"  {u}")

    if args.dry_run:
        print("(dry run: not writing the DB)")
        return
    if survivors or unique_touched:
        db.write_records(existing + survivors)
        print(f"Wrote {config.DB_PATH} ({len(existing) + len(survivors)} records)")


if __name__ == "__main__":
    main()

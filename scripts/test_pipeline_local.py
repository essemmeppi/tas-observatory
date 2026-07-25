"""Local smoke test for the observatory pipeline with the LLM step mocked.

Two halves:
  - offline checks (no network, no API key): URL canonicalisation, source tiers,
    name matching, merge semantics, duplicate resolution, digest formatting.
  - a live pass over the real feeds, skipped with --offline.

Run: python scripts/test_pipeline_local.py [--offline]
"""
import argparse
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from observatory import db, digest, llm, merge, run, sources, urls  # noqa: E402

RUN_DATE = "2026-07-25"


def check(label: str, condition: bool, detail: str = ""):
    if not condition:
        raise AssertionError(f"{label}{': ' + detail if detail else ''}")
    print(f"ok: {label}")


def test_canonical_urls():
    same = [
        ("https://www.example.com/a/", "http://example.com/a"),
        ("https://amp.dw.com/de/x-1", "https://dw.com/de/x-1"),
        ("https://kfor.com/news/x/amp/", "https://www.kfor.com/news/x"),
        ("https://m.economictimes.com/tech/x.cms", "https://economictimes.com/tech/x.cms"),
        ("https://site.com/a?utm_source=news&utm_medium=rss", "https://site.com/a"),
        ("https://site.com/a?ref=latest-headlines", "https://site.com/a"),
    ]
    for left, right in same:
        check(f"canonical: {left[:44]} == {right[:34]}",
              urls.canonical_url(left) == urls.canonical_url(right),
              f"{urls.canonical_url(left)} != {urls.canonical_url(right)}")
    # An id in the query string is content, not tracking, and must survive.
    check("canonical keeps content query params",
          "idxno=12376" in urls.canonical_url("https://www.thelec.net/news/articleView.html?idxno=12376"))
    check("canonical distinguishes different articles",
          urls.canonical_url("https://site.com/a") != urls.canonical_url("https://site.com/b"))


def test_tiers():
    expected = {
        "https://www.gov.uk/government/news/x": 1,
        "https://hmrc.gov.uk/x": 1,
        "https://www.canada.ca/en/x": 1,
        "https://digital-strategy.ec.europa.eu/en/x": 1,
        "https://www.theglobeandmail.com/politics/x": 2,
        "https://www.meritalk.com/articles/x": 3,
        "https://www.hrreporter.com/focus-areas/x": 3,
        "https://www.miragenews.com/x-1715429/": 4,
        "https://247wallst.com/investing/x": 5,
        "https://some-unlisted-blog.example/x": urls.DEFAULT_TIER,
    }
    for url, want in expected.items():
        got = urls.tier(url)
        check(f"tier {want}: {urls.domain(url)}", got == want, f"got {got}")


def test_name_matching():
    # The pair that slipped through on 2026-07-25.
    check("containment catches the hrreporter re-tell",
          db.names_match("Public consultation on AI transparency and agentic AI regulation",
                         "Public Consultation on AI Transparency"))
    check("equality still matches", db.names_match("VA Agentforce Expansion", "va agentforce expansion"))
    # Honest about the limit: these two need the LLM pass, not string matching.
    check("containment does NOT catch the VA rename",
          not db.names_match("VA Agentforce Enterprise License Agreement", "VA Agentforce Expansion"))
    check("short fragments do not match",
          not db.names_match("AI Strategy", "AI Strategy for the Public Sector of Ruritania"))
    check("unrelated names do not match",
          not db.names_match("Ajman Trade Licence Renewal", "K-Digital Training AI Campus"))


def _record(rid, url, name, news_date, **extra):
    base = {
        "id": rid, "url": url, "name": name, "news_date": news_date, "sources": [],
        "organisation": "Org", "countries": ["Testland"], "description": "", "novelty": "",
        "stakeholders": "", "agentic_rationale": "", "tech_details": "", "providers": [],
        "autonomy_level": None, "status": "unclear", "year": "2026", "layers": [],
        "functions": [], "tags": [], "agentic": True, "date_added": "2026-07-24",
    }
    base.update(extra)
    return base


def test_pick_canonical():
    # Oldest source wins when it is not the weakest of the set.
    pool = [
        {"url": "https://www.hrreporter.com/a", "tier": 3, "news_date": "2026-07-24"},
        {"url": "https://www.theglobeandmail.com/b", "tier": 2, "news_date": "2026-07-25"},
        {"url": "https://www.miragenews.com/c", "tier": 4, "news_date": "2026-07-24"},
    ]
    check("canonical = oldest that is not the weakest",
          merge.pick_canonical(pool)["url"].endswith("hrreporter.com/a"))
    # But an oldest source that is the weakest of the set gives way to the best.
    pool2 = [
        {"url": "https://www.miragenews.com/c", "tier": 4, "news_date": "2026-07-23"},
        {"url": "https://www.theglobeandmail.com/b", "tier": 2, "news_date": "2026-07-25"},
    ]
    check("weakest oldest is demoted to the best tier",
          merge.pick_canonical(pool2)["url"].endswith("theglobeandmail.com/b"))


def test_merge_semantics():
    keeper = _record("keep1", "https://www.miragenews.com/c", "Consultation", "2026-07-24")
    better = _record("dup1", "https://www.theglobeandmail.com/b", "Consultation, expanded",
                     "2026-07-25", description="Rich Globe and Mail summary.",
                     agentic_rationale="Quotes the discussion paper.", autonomy_level=4,
                     status="announced", providers=["OpenAI"], functions=["f1", "f2"])
    weaker = _record("dup2", "https://247wallst.com/x", "Consultation, markets take",
                     "2026-07-26", description="Thin markets summary.",
                     tech_details="Some tech detail.", functions=["f3", "f4"])

    merge.merge(keeper, [better, weaker], RUN_DATE, enrich=False)

    check("id is frozen across a merge", keeper["id"] == "keep1")
    check("news_date is the earliest of the group", keeper["news_date"] == "2026-07-24",
          keeper["news_date"])
    check("best-tier text wins over a weaker report",
          keeper["description"] == "Rich Globe and Mail summary.", keeper["description"])
    check("a weaker report still fills an empty field",
          keeper["tech_details"] == "Some tech detail.")
    check("null autonomy_level is filled", keeper["autonomy_level"] == 4)
    check("unclear status is upgraded", keeper["status"] == "announced")
    check("providers are unioned", keeper["providers"] == ["OpenAI"])
    check("functions stay capped at 3", len(keeper["functions"]) == 3, str(keeper["functions"]))
    # The oldest source keeps the canonical slot: it is tier 4, but the weakest
    # of this set is 247wallst at tier 5, so the demotion rule does not fire.
    # Moving the link is deliberately conservative even when better text wins.
    check("canonical url stays with the oldest non-weakest source",
          keeper["url"].endswith("miragenews.com/c"), keeper["url"])
    check("sources exclude the canonical url",
          all(not u.endswith("miragenews.com/c") for u in keeper["sources"]))
    check("sources are tier-ordered", keeper["sources"][0].endswith("theglobeandmail.com/b"),
          str(keeper["sources"]))
    check("updated is stamped", keeper["updated"] == RUN_DATE)


def test_sources_cap():
    keeper = _record("k", "https://a.example/1", "Thing", "2026-07-01")
    dups = [_record(f"d{i}", f"https://outlet{i}.example/x", "Thing variant", "2026-07-02")
            for i in range(8)]
    merge.merge(keeper, dups, RUN_DATE, enrich=False)
    check(f"sources capped at {merge.MAX_SOURCES}",
          len(keeper["sources"]) == merge.MAX_SOURCES, str(len(keeper["sources"])))


def test_attach_source():
    keeper = _record("k", "https://www.meritalk.com/a", "Thing", "2026-07-01")
    check("attach_source records a new link",
          merge.attach_source(keeper, "https://www.gov.uk/news/a") and len(keeper["sources"]) == 1)
    check("attach_source ignores a URL variant already on file",
          not merge.attach_source(keeper, "https://gov.uk/news/a/"))
    check("attach_source ignores the canonical url itself",
          not merge.attach_source(keeper, "https://meritalk.com/a"))


def test_resolve_duplicates():
    existing = [_record("old1", "https://www.meritalk.com/va", "VA Agentforce Expansion",
                        "2026-07-24", date_added="2026-07-24")]
    candidates = [
        _record("new1", "https://247wallst.com/va", "VA Agentforce Enterprise License Agreement",
                "2026-07-25", date_added=RUN_DATE, status="implemented"),
        _record("new2", "https://www.gulftoday.ae/ajman", "Ajman headless licence renewal",
                "2026-07-25", date_added=RUN_DATE),
        _record("new3", "https://www.thenationalnews.com/ajman", "Ajman Agentic AI Trade Licence",
                "2026-07-24", date_added=RUN_DATE),
    ]
    verdict = {
        "merge_groups": [[1, 2]],                               # the two Ajman reports
        "already_known": [{"candidate": 0, "existing": 0}],     # the VA re-tell
    }
    with patch.object(llm, "dedupe_batch", return_value=verdict):
        survivors, touched, ran = run.resolve_duplicates(candidates, existing, RUN_DATE)
    check("dedupe ran", ran)
    check("same-day pair collapsed to one record", len(survivors) == 1, str(len(survivors)))
    check("the surviving record is the Ajman story", "Ajman" in survivors[0]["name"])
    check("both Ajman outlets are on the survivor",
          len(survivors[0]["sources"]) == 1, str(survivors[0]["sources"]))
    check("Ajman keeps the earliest news_date", survivors[0]["news_date"] == "2026-07-24")
    check("the VA re-tell merged into the stored record",
          len(touched) == 1 and touched[0]["id"] == "old1")
    check("the stored VA record gained the new source",
          any("247wallst" in u for u in touched[0]["sources"]))
    check("the stored VA record kept its id and url",
          touched[0]["id"] == "old1" and "meritalk" in touched[0]["url"])


def test_resolve_falls_back_when_dedupe_dies():
    existing = [_record("old1", "https://www.miragenews.com/c",
                        "Public Consultation on AI Transparency", "2026-07-24",
                        date_added="2026-07-24")]
    candidates = [
        _record("new1", "https://www.hrreporter.com/c",
                "Public consultation on AI transparency and agentic AI regulation",
                "2026-07-24", date_added=RUN_DATE),
        _record("new2", "https://www.gov.uk/unrelated", "Something Entirely Different Happened",
                "2026-07-25", date_added=RUN_DATE),
    ]
    with patch.object(llm, "dedupe_batch", side_effect=RuntimeError("no parseable JSON")):
        survivors, touched, ran = run.resolve_duplicates(candidates, existing, RUN_DATE)
    check("a dead dedupe call reports the run as degraded", not ran)
    check("the containment fallback still catches the contained name",
          len(touched) == 1 and touched[0]["id"] == "old1")
    check("the unrelated record survives the fallback",
          len(survivors) == 1 and survivors[0]["id"] == "new2")


def test_budget_exhaustion_propagates():
    with patch.object(llm, "dedupe_batch", side_effect=llm.BudgetExhausted("402")):
        try:
            run.resolve_duplicates([_record("n", "https://a.example/1", "Thing", RUN_DATE)],
                                   [], RUN_DATE)
        except llm.BudgetExhausted:
            check("budget exhaustion propagates out of dedupe", True)
        else:
            raise AssertionError("BudgetExhausted was swallowed")


def test_degraded_marker():
    run.DEGRADED_MARKER.unlink(missing_ok=True)
    run._finish(None)
    check("a clean run leaves no marker", not run.DEGRADED_MARKER.exists())
    try:
        run._finish("LLM budget exhausted (402)")
    except SystemExit as e:
        check("a degraded run exits non-zero", e.code == 1, str(e.code))
    else:
        raise AssertionError("a degraded run exited cleanly")
    check("a degraded run leaves the marker the workflow reads",
          run.DEGRADED_MARKER.exists() and "402" in run.DEGRADED_MARKER.read_text())
    run.DEGRADED_MARKER.unlink(missing_ok=True)


def test_digest():
    items = [_record("n1", "https://www.gov.uk/a", "A Thing", RUN_DATE,
                     description="It does things.", sources=["https://www.bbc.co.uk/a"])]
    enriched = [_record("e1", "https://www.meritalk.com/b", "An Older Thing", "2026-07-20",
                        sources=["https://247wallst.com/b"])]
    with patch.object(digest.llm, "write_digest_lede", return_value="Test lede."):
        with patch.object(digest.config, "LLM_API_KEY", "x"):
            clean = digest.build_digest(items, RUN_DATE, enriched=enriched)
            degraded = digest.build_digest(items, RUN_DATE, degraded="LLM budget exhausted",
                                           unassessed=106, dedupe_ran=False)
    check("digest renders the lede and items", "Test lede." in clean and "A Thing" in clean)
    check("digest lists records improved by new reporting", "Updated from new reporting" in clean)
    check("a clean digest carries no warning", "Incomplete run" not in clean)
    check("a degraded digest warns up front", "Incomplete run" in degraded)
    check("the warning counts unassessed articles", "106 articles never assessed" in degraded)
    check("the warning flags the skipped duplicate check", "duplicate check did not run" in degraded)


def test_real_db():
    records = db.load_records()
    check(f"DB loads ({len(records)} records)", len(records) > 100, str(len(records)))
    ids = [r["id"] for r in records]
    check("record ids are unique", len(ids) == len(set(ids)))
    # Two records citing one article means a duplicate slipped through. A
    # trailing-slash variant of the same executivegov URL did exactly that in
    # August 2025, before URLs were compared canonically.
    owner, clashes = {}, []
    for r in records:
        for url in [r["url"], *(r.get("sources") or [])]:
            key = urls.canonical_url(url)
            if key in owner:
                clashes.append(f"{key} claimed by {owner[key]} and {r['id']}")
            owner[key] = r["id"]
    check(f"no URL is claimed by two records ({len(owner)} URLs checked)",
          not clashes, "; ".join(clashes[:5]))
    deduper = db.Deduper(records)
    check("dedup by URL works", not deduper.is_new(records[0]["url"]))
    check("a fresh URL is not deduped", deduper.is_new("https://example.org/brand-new"))
    check("a merged record's extra source is known too",
          not deduper.is_new("https://247wallst.com/investing/2026/07/24/"
                             "servicenow-surges-6-salesforce-climbs-4-as-government-ai-deals-"
                             "lift-enterprise-software/"))


def fake_screen(agentic=True):
    return lambda text, url, published="": {"relevant": True, "agentic": agentic, "subject": "x"}


def fake_assess(text, url, published, agentic=True):
    return {
        "relevant": True,
        "agentic": agentic,
        "name": f"Test item for {url[:40]}",
        "organisation": "Test Org",
        "countries": ["Testland"],
        "description": text[:120].replace("\n", " "),
        "novelty": "n/a",
        "stakeholders": "n/a",
        "year": "2026",
        "tags": ["pilot"],
    }


def test_live_feeds():
    records = db.load_records()
    deduper = db.Deduper(records)
    items = sources.fetch_all_feeds()
    check(f"feeds fetched ({len(items)} items)", len(items) > 10, str(len(items)))
    check("every item has an http URL", all(i["url"].startswith("http") for i in items))

    fresh = run.prepare_items(items, deduper)
    print(f"ok: {len(items)} items -> {len(fresh)} after URL and title dedup")
    canon = [urls.canonical_url(i["url"]) for i in fresh]
    check("no repeated URL survives preparation", len(canon) == len(set(canon)))
    titles = [db._norm_name(i.get("title", "")) for i in fresh if i.get("title")]
    check("no repeated title survives preparation", len(titles) == len(set(titles)))
    # tier_for, not tier: a Google News item's URL is a news.google.com redirect,
    # and the publisher from the feed is what it should be ranked on.
    tiers = [urls.tier_for(i) for i in fresh]
    check("best sources are queued first", tiers == sorted(tiers), f"{tiers[:12]}")
    check("the queue is not all one tier", len(set(tiers)) > 1, str(set(tiers)))

    with patch.object(run.llm, "screen_article", side_effect=fake_screen()), \
         patch.object(run.llm, "extract_record", side_effect=fake_assess):
        built = [r for r in (run.process_item(i, deduper, RUN_DATE) for i in fresh[:4]) if r]
    check("records are built from live feeds", bool(built), "extraction or link decoding failing?")
    for r in built:
        check(f"record is well-formed ({r['id']})",
              bool(r["id"] and r["url"] and r["source"]) and r["date_added"] == RUN_DATE)
        check(f"the stored URL is a real article, not a redirect ({r['id']})",
              "news.google.com" not in r["url"], r["url"])

    check("a second pass over the same item is deduped",
          run.process_item(fresh[0], deduper, RUN_DATE) is None)

    # The gate must reject without ever reaching extraction — that is the whole
    # point of splitting them, since ~83% of screened articles are rejected.
    extract_calls = []
    with patch.object(run.llm, "screen_article", side_effect=fake_screen(agentic=False)), \
         patch.object(run.llm, "extract_record", side_effect=lambda *a: extract_calls.append(a)), \
         patch.object(run.config, "AGENTIC_ONLY", True):
        check("the agentic-only gate drops a non-agentic item",
              run.process_item(fresh[5], deduper, RUN_DATE) is None)
    check("a gated-out item never reaches extraction", not extract_calls, str(extract_calls))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="skip the live feed pass")
    args = parser.parse_args()

    offline = [
        test_canonical_urls, test_tiers, test_name_matching, test_pick_canonical,
        test_merge_semantics, test_sources_cap, test_attach_source,
        test_resolve_duplicates, test_resolve_falls_back_when_dedupe_dies,
        test_budget_exhaustion_propagates, test_degraded_marker, test_digest, test_real_db,
    ]
    for fn in offline:
        print(f"\n-- {fn.__name__}")
        fn()

    if args.offline:
        print("\nAll offline checks passed (live feed pass skipped).")
        return
    print("\n-- test_live_feeds")
    test_live_feeds()
    print("\nAll checks passed.")


if __name__ == "__main__":
    main()

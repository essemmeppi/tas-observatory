"""Local smoke test for the observatory pipeline with the LLM step mocked.

Two halves:
  - offline checks (no network, no API key): URL canonicalisation, source tiers,
    name matching, merge semantics, duplicate resolution, digest formatting.
  - a live pass over the real feeds, skipped with --offline.

Run: python scripts/test_pipeline_local.py [--offline]
"""
import argparse
import collections
import itertools
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from observatory import config, db, digest, llm, merge, run, sources, urls  # noqa: E402

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
        # A vendor blog is primary for technical detail but commercially
        # interested, so it ranks with the trade press, not with governments.
        "https://cloud.google.com/blog/topics/public-sector/x": 3,
        "https://www.anthropic.com/news/x": 3,
        "https://www.lemonde.fr/politique/x": 2,
        "https://www.agendadigitale.eu/x": 3,
        "https://netzpolitik.org/2026/x": 3,
        "https://cnil.fr/fr/x": 1,
        "https://www.agid.gov.it/rss.xml": 1,
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

    # Licence/License: one character apart, so neither equality nor containment
    # sees it. Needs the similarity branch, which needs a shared country.
    uae, us = ["United Arab Emirates"], ["United States"]
    check("similarity catches the Ajman Licence/License pair",
          db.names_match("Ajman Agentic AI Trade Licence Renewal",
                         "Ajman Agentic AI Trade License Renewal", uae, uae))
    check("similarity needs a shared country",
          not db.names_match("Ajman Agentic AI Trade Licence Renewal",
                             "Ajman Agentic AI Trade License Renewal", uae, us))
    # Two real deployments of one product, in different countries: not a duplicate.
    check("same product in two countries is not a duplicate",
          not db.names_match("Microsoft 365 Copilot", "Microsoft 365 Copilot Chat", ["United Kingdom"], us))
    # And the limit worth being explicit about: no string method links these two
    # reports of the Warner package. That is what the LLM pass is for.
    check("similarity does NOT catch a full rename",
          not db.names_match("Framework for America's AI Future",
                             "Federal AI Oversight Legislative Package (AI AGENT Act)", us, us))

    # The real pair that sat in the database as a duplicate on 2026-07-28. The old
    # 18-char/3-token floors meant containment was never even tested, because
    # "genesis mission" is 15 chars and 2 tokens.
    check("containment catches a two-word initiative name",
          db.names_match("Science: A New Golden Age / Genesis Mission", "Genesis Mission"))
    check("the short-fragment decoy is still rejected",
          not db.names_match("AI Strategy", "AI Strategy for the Public Sector of Ruritania"))
    # 10 chars would start matching this; 12 is the floor precisely because of it.
    check("a bare product name does not swallow longer records",
          not db.names_match("Agentic AI", "Agentic AI for Public Service Delivery"))


def test_country_normalisation():
    for raw, want in [("United States of America", "United States"), ("USA", "United States"),
                      ("U.S.A.", "United States"), ("UK", "United Kingdom"),
                      ("Britain", "United Kingdom"), ("UAE", "United Arab Emirates"),
                      ("Republic of Korea", "South Korea"), ("Türkiye", "Turkey"),
                      ("United States", "United States")]:
        got = llm.canonical_country(raw)
        check(f"country: {raw!r} -> {want!r}", got == want, f"got {got!r}")
    # An unrecognised country must pass through untouched, never be dropped.
    check("an unknown country survives normalisation",
          llm.canonical_country("Genovia") == "Genovia")
    cleaned = llm._clean_assessment(
        {"countries": ["United States of America", "USA", "France"], "status": "pilot"})
    check("_clean_assessment folds and dedupes countries",
          cleaned["countries"] == ["United States", "France"], str(cleaned["countries"]))


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


def test_fair_order():
    """The regression this guards: a truncated queue containing no French item."""
    items = (
        [{"url": f"https://lemonde.fr/a{i}", "publisher": "https://lemonde.fr",
          "source": "google_news:FR", "title": f"fr {i}"} for i in range(25)]
        + [{"url": f"https://elpais.com/a{i}", "publisher": "https://elpais.com",
            "source": "google_news:ES", "title": f"es {i}"} for i in range(25)]
        + [{"url": f"https://fedscoop.com/a{i}", "publisher": "",
            "source": "rss:fedscoop.com", "title": f"us {i}"} for i in range(10)]
        + [{"url": f"https://www.gov.uk/a{i}", "publisher": "",
            "source": "rss:gds.blog.gov.uk", "title": f"uk {i}"} for i in range(10)]
    )
    ordered = run._fair_order(items)
    check("fair order keeps every item", len(ordered) == len(items))
    first_round = {i["source"] for i in ordered[:4]}
    check("every source appears in the first round", len(first_round) == 4, str(first_round))
    # Truncation is the real test: the old tier sort put all 50 non-English items
    # behind every English one, so a 20-item prefix contained none of them.
    prefix = {i["source"] for i in ordered[:20]}
    check("a truncated prefix still contains every source", len(prefix) == 4, str(prefix))
    for src in ("google_news:FR", "google_news:ES"):
        n = sum(1 for i in ordered[:20] if i["source"] == src)
        check(f"{src} gets a fair share of a 20-item prefix ({n})", n >= 4, str(n))
    # Within a round, the better tier still leads: gov.uk (1) before fedscoop (3).
    tiers = [urls.tier_for(i) for i in ordered[:4]]
    check("better tiers lead within a round", tiers == sorted(tiers), str(tiers))


def test_gate_model_split():
    """The gate runs on its own model with reasoning off; extraction must not."""
    seen = []

    def spy(messages, model, json_mode=False, max_tokens=1600, reasoning=None):
        seen.append({"model": model, "reasoning": reasoning, "max_tokens": max_tokens})
        return '{"relevant": true, "agentic": true, "subject": "x", "name": "X"}'

    with patch.object(llm, "_chat", side_effect=spy), \
         patch.object(llm.config, "GATE_MODEL", "fast/model"), \
         patch.object(llm.config, "LLM_MODEL", "slow/model"), \
         patch.object(llm.config, "GATE_REASONING", False):
        llm.screen_article("text", "https://a.example/1")
        llm.extract_record("text", "https://a.example/1", "")

    gate, extract_call = seen[0], seen[1]
    check("the gate uses GATE_MODEL", gate["model"] == "fast/model", gate["model"])
    check("extraction still uses LLM_MODEL",
          extract_call["model"] == "slow/model", extract_call["model"])
    # A reasoning model spends its latency on hidden thinking tokens, which is the
    # whole cost of the gate; extraction is where thinking is actually worth paying for.
    check("the gate disables reasoning", gate["reasoning"] is False, str(gate["reasoning"]))
    check("extraction leaves reasoning alone",
          extract_call["reasoning"] is None, str(extract_call["reasoning"]))
    check("the gate stays on a small token budget", gate["max_tokens"] == 200,
          str(gate["max_tokens"]))

    seen.clear()
    with patch.object(llm, "_chat", side_effect=spy), \
         patch.object(llm.config, "GATE_REASONING", True):
        llm.screen_article("text", "https://a.example/1")
    check("GATE_REASONING=1 re-enables reasoning", seen[0]["reasoning"] is True)


def test_reasoning_body():
    """`reasoning: {enabled: false}` must actually reach the request body."""
    bodies = []
    class FakeResp:
        status_code = 200
        text = ""
        def raise_for_status(self): pass
        def json(self): return {"choices": [{"message": {"content": "{}"}}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        bodies.append(json)
        return FakeResp()

    with patch.object(llm.requests, "post", side_effect=fake_post), \
         patch.object(llm.config, "LLM_API_KEY", "x"):
        llm._chat([{"role": "user", "content": "hi"}], model="m", reasoning=False)
        llm._chat([{"role": "user", "content": "hi"}], model="m", reasoning=None)
    check("reasoning=False sends the disable flag",
          bodies[0].get("reasoning") == {"enabled": False}, str(bodies[0].get("reasoning")))
    check("reasoning=None sends no reasoning field", "reasoning" not in bodies[1])


def test_extraction_call_shape():
    """Extraction lost 10 gate-approved articles on 2026-07-28 to truncated JSON,
    one error naming the cut-off exactly: "line 187 column 1 (char 1023)". That is
    far short of what 1600 tokens allows, so a reasoning model's hidden thinking was
    eating the budget. Reasoning stays on here by choice, so the ceiling carries the
    fix and has to be big enough for thinking plus a ~16-field schema."""
    seen = {}

    def capture(messages, model, json_mode=False, max_tokens=1600, reasoning=None):
        seen.update(max_tokens=max_tokens, reasoning=reasoning)
        return '{"relevant": true, "agentic": true, "name": "X", "status": "pilot"}'

    with patch.object(llm, "_chat", side_effect=capture):
        llm.extract_record("text", "https://a.example/1", "")
    check(f"extraction raises the token ceiling (got {seen.get('max_tokens')})",
          seen.get("max_tokens") == llm.EXTRACT_MAX_TOKENS and llm.EXTRACT_MAX_TOKENS >= 3000)
    check("extraction still leaves reasoning to the model",
          seen.get("reasoning") is None, str(seen.get("reasoning")))


def test_extraction_retry():
    good = '{"relevant": true, "agentic": true, "name": "X", "status": "pilot"}'
    calls = []

    def flaky(messages, model, json_mode=False, max_tokens=1600, reasoning=None):
        calls.append(1)
        return "not json at all" if len(calls) == 1 else good

    with patch.object(llm, "_chat", side_effect=flaky):
        rec = llm.extract_record("text", "https://a.example/1", "")
    check("extraction retries once and succeeds on the second attempt",
          rec is not None and len(calls) == 2, f"{rec} after {len(calls)} calls")

    calls.clear()
    with patch.object(llm, "_chat", side_effect=lambda *a, **k: calls.append(1) or "garbage"):
        rec = llm.extract_record("text", "https://a.example/1", "")
    check("extraction gives up after two attempts",
          rec is None and len(calls) == 2, f"{rec} after {len(calls)} calls")

    calls.clear()

    def broke(*a, **k):
        calls.append(1)
        raise llm.BudgetExhausted("402")

    with patch.object(llm, "_chat", side_effect=broke):
        try:
            llm.extract_record("text", "https://a.example/1", "")
        except llm.BudgetExhausted:
            check("a budget error is not retried", len(calls) == 1, str(len(calls)))
        else:
            raise AssertionError("BudgetExhausted was swallowed")


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

    # One record saying "United States of America" beside 77 saying "United States"
    # forked the site's country dropdown and its choropleth. Two spellings that
    # normalise to the same country mean the vocabulary has drifted again.
    by_canon = collections.defaultdict(set)
    for r in records:
        for c in r.get("countries") or []:
            by_canon[llm.canonical_country(c)].add(c)
    forks = {k: sorted(v) for k, v in by_canon.items() if len(v) > 1}
    check(f"one spelling per country ({len(by_canon)} countries)", not forks, str(forks))
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
    # Same-story duplicates, not just same-URL ones. Two Ajman records and two
    # Warner records sat in the database for a day because nothing scanned for
    # this; the URL check above cannot see them, since the outlets differ.
    # Time-bounded, matching the pipeline's own policy: a re-tell within days is a
    # duplicate, but the same initiative resurfacing months later is a new
    # development. "Gemini for Government" legitimately appears for the Aug 2025
    # GSA OneGov deal and again in Apr 2026 — collapsing those would flatten the
    # timeline rather than clean it.
    near = []
    for a, b in itertools.combinations(records, 2):
        gap = abs((date.fromisoformat(a["date_added"]) - date.fromisoformat(b["date_added"])).days)
        if gap > config.DEDUP_WINDOW_DAYS:
            continue
        if db.names_match(a.get("name", ""), b.get("name", ""),
                          a.get("countries"), b.get("countries")):
            near.append(f"{a['id']}/{b['id']} ({gap}d) "
                        f"{a.get('name','')[:30]!r} ~ {b.get('name','')[:30]!r}")
    check(f"no two records within {config.DEDUP_WINDOW_DAYS} days describe the same initiative "
          f"({len(records) * (len(records) - 1) // 2} pairs compared)",
          not near, "; ".join(near[:6]))

    deduper = db.Deduper(records)
    check("dedup by URL works", not deduper.is_new(records[0]["url"]))
    # The name half of the old check is gone: a record whose *name* matches but
    # whose URL is new now reaches the end-of-run dedupe, which can merge and
    # enrich, instead of being skipped early with only its link kept.
    check("a new URL with a familiar name is no longer skipped early",
          deduper.is_new("https://example.org/fresh-take-on-" + records[0]["id"]))
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

    # The queue is round-robin across sources, not globally tier-sorted, so that a
    # truncated run does not cut whole languages. Check what that should mean:
    # every source represented up front, and tiers ordered *within* the first round.
    n_sources = len({i["source"] for i in fresh})
    first_round = fresh[:n_sources]
    check(f"all {n_sources} sources appear in the first round",
          len({i["source"] for i in first_round}) == n_sources)
    # tier_for, not tier: a Google News item's URL is a news.google.com redirect,
    # and the publisher from the feed is what it should be ranked on.
    tiers = [urls.tier_for(i) for i in first_round]
    check("better tiers lead within the first round", tiers == sorted(tiers), str(tiers))
    check("the queue is not all one tier", len(set(tiers)) > 1, str(set(tiers)))

    # The regression that motivated fair ordering: non-English sources used to sit
    # so far back that a truncated run never reached any of them.
    NON_EN = (":FR", ":ES", ":DE", ":IT", ":BR", "agid", "cnil", "agendadigitale",
              "forumpa", "netzpolitik", "egovernment")
    non_en = sum(1 for i in fresh[:60] if any(m in i["source"] for m in NON_EN))
    check(f"non-English sources reach the first 60 of the queue ({non_en})", non_en >= 10, str(non_en))

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
        test_merge_semantics, test_sources_cap, test_fair_order, test_gate_model_split, test_reasoning_body,
        test_extraction_retry,
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

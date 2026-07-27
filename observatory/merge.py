"""Folding a duplicate report into the record we already have.

The pipeline used to *drop* duplicates, which quietly threw away the better
reporting: the Canada AI-transparency consultation was first stored from a
syndicated press release with an empty `agentic_rationale`, and the Globe and
Mail write-up that arrived a day later — quoting the discussion paper directly —
would have been discarded as a re-tell. So a duplicate is merged, not dropped.
"""
from . import llm, urls

MAX_SOURCES = 4

TEXT_FIELDS = ("description", "novelty", "stakeholders", "agentic_rationale", "tech_details")
LIST_FIELDS = ("countries", "country_codes", "providers", "layers", "functions", "tags")
FAR_FUTURE = "9999-12-31"


def _sources_of(record: dict) -> list:
    """Every URL a record stands on, canonical link first."""
    return [record.get("url", "")] + list(record.get("sources") or [])


def _pool(group: list) -> list:
    """Distinct sources across a merge group, as {url, tier, news_date, title}."""
    seen, pool = set(), []
    for record in group:
        for url in _sources_of(record):
            key = urls.canonical_url(url)
            if not key or key in seen:
                continue
            seen.add(key)
            pool.append({
                "url": url,
                "tier": urls.tier(url),
                "news_date": record.get("news_date") or "",
                "title": (record.get("source_titles") or {}).get(url, ""),
            })
    return pool


def pick_canonical(pool: list) -> dict:
    """The oldest source, unless it is the weakest of the set.

    Oldest wins because the canonical link should point at the outlet that broke
    the story. The exception matters: the first report is often a press-release
    aggregator, and once a national outlet covers the same story it makes the
    better citation.
    """
    if not pool:
        return {}
    oldest = min(pool, key=lambda s: (s["news_date"] or FAR_FUTURE, s["tier"]))
    best_tier = min(s["tier"] for s in pool)
    worst_tier = max(s["tier"] for s in pool)
    if oldest["tier"] == worst_tier and best_tier < worst_tier:
        return min(pool, key=lambda s: (s["tier"], s["news_date"] or FAR_FUTURE))
    return oldest


def _fill_deterministically(keeper: dict, dups: list) -> None:
    """No-LLM fallback: fill gaps, and prefer text from a better-tier source.

    Weakest source first, so that when several reports can replace a field the
    most authoritative one is the last to write and therefore the one that sticks.
    """
    keeper_tier = urls.tier(keeper.get("url", ""))
    for dup in sorted(dups, key=lambda d: -urls.tier(d.get("url", ""))):
        dup_tier = urls.tier(dup.get("url", ""))
        for field in TEXT_FIELDS:
            incoming = (dup.get(field) or "").strip()
            if not incoming:
                continue
            if not (keeper.get(field) or "").strip() or dup_tier < keeper_tier:
                keeper[field] = incoming
        if keeper.get("autonomy_level") is None and dup.get("autonomy_level") is not None:
            keeper["autonomy_level"] = dup["autonomy_level"]
        if keeper.get("status") in (None, "", "unclear") and dup.get("status") not in (None, "", "unclear"):
            keeper["status"] = dup["status"]
        if not keeper.get("year") and dup.get("year"):
            keeper["year"] = dup["year"]


def merge(keeper: dict, dups: list, run_date: str, enrich: bool = True) -> dict:
    """Fold `dups` into `keeper` in place and return it.

    `keeper` keeps its identity — `id` and `date_added` never move, so the
    `#r=<id>` links already posted to Slack keep resolving (the frontend looks
    records up by id, never by URL).
    """
    if not dups:
        return keeper

    group = [keeper] + dups
    pool = _pool(group)

    # The date shown on the card is when the story broke, not when whichever
    # outlet ended up canonical happened to publish.
    dates = [r.get("news_date") for r in group if r.get("news_date")]
    if dates:
        keeper["news_date"] = min(dates)

    enriched = llm.merge_records(keeper, dups) if enrich else None
    if enriched:
        for field in TEXT_FIELDS:
            if (enriched.get(field) or "").strip():
                keeper[field] = enriched[field].strip()
        if enriched.get("autonomy_level") is not None:
            keeper["autonomy_level"] = enriched["autonomy_level"]
        if enriched.get("status"):
            keeper["status"] = enriched["status"]
    else:
        _fill_deterministically(keeper, dups)

    for field in LIST_FIELDS:
        merged = list(keeper.get(field) or [])
        for dup in dups:
            for value in dup.get(field) or []:
                if value not in merged:
                    merged.append(value)
        if field == "functions":
            merged = merged[:3]
        keeper[field] = merged

    if any(r.get("agentic") for r in group):
        keeper["agentic"] = True

    canonical = pick_canonical(pool)
    if canonical:
        keeper["url"] = canonical["url"]
    canonical_key = urls.canonical_url(keeper.get("url", ""))
    others = sorted(
        (s for s in pool if urls.canonical_url(s["url"]) != canonical_key),
        key=lambda s: (s["tier"], s["news_date"] or FAR_FUTURE),
    )
    keeper["sources"] = [s["url"] for s in others][:MAX_SOURCES]
    # Re-key the headlines to exactly the URLs the record now cites: merged-away
    # duplicates bring their headlines in, and dropped sources take theirs out.
    titles = {s["url"]: s["title"] for s in pool if s.get("title")}
    kept = [keeper.get("url", ""), *keeper["sources"]]
    keeper["source_titles"] = {u: titles[u] for u in kept if u in titles}
    keeper["updated"] = run_date
    return keeper



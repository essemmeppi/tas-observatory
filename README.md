# TAS Observatory

Daily observatory of **agentic AI entering government**, run by [The Agentic State](https://agenticstate.org).

**Browse it:** [observatory.agenticstate.org](https://observatory.agenticstate.org) — filterable feed, framework-layer and country breakdowns, updated daily.

## How it works

One GitHub Action runs every morning ([.github/workflows/observatory.yml](.github/workflows/observatory.yml)):

1. **Ingest** — multilingual Google News RSS search queries (EN/FR/ES/DE/IT/PT + IN/SG editions, no Google account needed) + gov-tech and non-English digital-policy RSS feeds ([data/feeds.json](data/feeds.json)) + a daily X sweep via Grok's `x_search` tool.
2. **Prioritise** — items are deduplicated on canonical URL and headline (one story reaches us through several Google News editions), then the queue is built **round-robin across sources**, with the authority tiers in [data/source_tiers.json](data/source_tiers.json) deciding order *within* each round. Every source gets a share of the front of the queue, so a run cut short by time trims each source's tail rather than dropping whole languages — ordering purely by tier had left the non-English feeds unassessed. Google News redirect links are decoded lazily, only for items about to be assessed.
3. **Filter, then extract** — each surviving article's text is extracted (trafilatura) and put through two LLM calls:
   - a small **gate** (~220-token prompt): is this a concrete AI-in-government development, and is it *agentic*? Only agentic items pass (`AGENTIC_ONLY=0` widens scope to all AI-in-gov).
   - full **extraction**, only for what survives: name, organisation, countries, description, novelty, stakeholders, agentic rationale, tech details, providers, autonomy level, status, tags, the [Agentic State framework](https://agenticstate.org/paper.html) layers and the WEF government functions.

   The split matters because ~83% of screened articles are rejected. Carrying the 12 layers, the 70 government functions and a dozen generated prose fields on every call meant paying for output that was then discarded — it roughly halves the cost of a run.
4. **Dedupe & merge** — one LLM call at the end of the run compares the batch against itself and against the last 14 days of records. A duplicate is **merged, not dropped**: the better-sourced report improves the existing record's prose, its URL joins `sources`, and `news_date` keeps the earliest date so the card still shows when the story broke. A free name check over 60 days (equality, containment, or near-identical spelling within one country) is the fallback when that call fails.
5. **Store** — [data/innovations.jsonl](data/innovations.jsonl) is rewritten (one JSON object per line) and committed. Git history is the archive; no snapshot files.
6. **Digest** — a short Slack message (LLM-written lede + one line per item, agentic items first, plus any records improved by today's reporting) posted via incoming webhook.

If a run loses its LLM budget, hits its time budget, or fails to assess most of what it attempted, it keeps and commits what it harvested, says so at the top of the Slack digest, writes a `.degraded` marker and **fails the job** — a partial harvest must never look like a clean one.

The digest arrives each morning on a best-effort schedule. GitHub's `schedule` trigger has no SLA and has been observed firing 2–4 hours late, so the cron is set the night before to absorb that; it is not a guaranteed delivery time.

There are no servers: GitHub Actions runs the pipeline, the repo is the database, GitHub Pages serves the frontend ([index.html](index.html), a single static page reading the JSONL).

## Setup

GitHub repo → Settings → Secrets and variables → Actions:

| Name | Type | Purpose |
|---|---|---|
| `LLM_API_KEY` | secret | OpenRouter key — covers the assessment model AND the Grok X sweep (required) |
| `SLACK_WEBHOOK_URL` | secret | Slack incoming webhook for the digest (optional) |
| `LLM_BASE_URL` | variable | `https://openrouter.ai/api/v1` (any OpenAI-compatible API works) |
| `LLM_MODEL` | variable | assessment model, e.g. `moonshotai/kimi-k2.5` (default: `gpt-4o-mini`) |

The X sweep uses Grok through OpenRouter's web/x_search plugin (`XSWEEP_MODEL`, default `x-ai/grok-4.3`, ~$0.04/day); it runs only when `LLM_BASE_URL` is OpenRouter. Set `XSWEEP_MODEL=""` to disable.

Ingestion works out of the box via Google News queries in `data/feeds.json` (add/edit queries there; `when:1d` = last 24 hours). Google Alerts RSS URLs can optionally be added under `google_alerts`. Trigger the workflow manually once (Actions → Daily observatory → Run workflow) to check the digest.

## Local usage

```bash
pip install -r observatory/requirements.txt
export LLM_API_KEY=...           # plus LLM_BASE_URL / LLM_MODEL if not OpenAI
python -m observatory.run --dry-run  # full run without writing or posting
python scripts/test_pipeline_local.py            # smoke test, no API keys needed
python scripts/test_pipeline_local.py --offline   # same, without touching the network
```

The smoke test mocks the LLM, so it verifies the merge and prioritisation machinery but not the model's duplicate-*detection* accuracy — that needs a real run with credits.

## Data

`data/innovations.jsonl` — a curated database of AI-in-government records since January 2025, one per line:

```json
{"id": "…", "name": "…", "organisation": "…", "countries": ["…"], "description": "…",
 "novelty": "…", "stakeholders": "…", "agentic_rationale": "why this is agentic",
 "tech_details": "…", "providers": ["Anthropic", "…"], "autonomy_level": 4,
 "status": "pilot", "news_date": "YYYY-MM-DD", "year": "2026",
 "url": "…", "sources": ["further urls for the same story, best-ranked first"],
 "source_titles": {"<url>": "article headline, for the site's source rows"},
 "source": "google_news:<gl> | rss:<domain> | x_grok | web_grok", "date_added": "YYYY-MM-DD",
 "updated": "YYYY-MM-DD (only if later reporting was merged in)",
 "agentic": true, "tags": ["pilot", "…"], "types": ["deployment", "…"], "layers": ["workflows", "…"], "functions": ["f46", "…"]}
```

`status` and `functions` are collected and kept but no longer rendered on the public site (reader feedback: not important to the public view).

Controlled vocabularies live in [data/taxonomies.json](data/taxonomies.json): `layers` (the framework's 12 layers), `autonomy_level` (the vision paper's L0 manual → L5 fully autonomous ladder), `status` (anchored to EU JRC AI Watch lifecycle: announced / in-development / pilot / implemented / scaled / discontinued / unclear), and `types` (the form a development takes: deployment / strategy / regulation / procurement — primary first, max two; no OECD/EC/World Bank scheme spans both policy and deployments, so this is our enum with each value anchored to an established vocabulary, see the `_source` note). `functions` uses the 70 government functions from the WEF Agentic State report ([data/functions.json](data/functions.json)).

Same-story duplicates are merged rather than dropped, so later and better-sourced reporting improves the record instead of being discarded:

- `id` and `date_added` never change, so `#r=<id>` links already shared keep resolving.
- `news_date` is the **earliest** across all merged sources — the date the story broke.
- `url` is the oldest source, unless that source is the weakest of the set, in which case the highest-ranked one takes the slot. The rest land in `sources`, ranked by [source tier](data/source_tiers.json) and capped at four — the aim is the few most authoritative citations, not every outlet that covered it.
- Prose fields are rewritten from the combined reports when the LLM is available, and otherwise filled from the best-ranked report that has them.

To merge two records by hand: `python scripts/merge_records.py --keep <id> --dup <id>`.

Records with `"source": "google_alerts_legacy"` were seeded from the predecessor project ([GovServiceX](https://github.com/essemmeppi/GovServiceX)); keyword-flagged agentic ones have `"agentic": true`, the rest `null` (unclassified). To remove a bad entry, delete its line and commit.

# TAS Observatory

Daily observatory of **agentic AI entering government**, run by [The Agentic State](https://agenticstate.org).

**Browse it:** [essemmeppi.github.io/tas-observatory](https://essemmeppi.github.io/tas-observatory/site/) — filterable feed, framework-layer and country breakdowns, updated daily.

## How it works

One GitHub Action runs every morning ([.github/workflows/observatory.yml](.github/workflows/observatory.yml)):

1. **Ingest** — multilingual Google News RSS search queries (EN/FR/ES/DE/IT/PT + IN/SG editions, no Google account needed; redirect links decoded to real article URLs) + gov-tech RSS feeds ([data/feeds.json](data/feeds.json)) + a daily X sweep via Grok's `x_search` tool.
2. **Filter & extract** — each new article's text is extracted (trafilatura) and assessed in a single LLM call: is it a concrete AI-in-government development? Is it *agentic*? Which layer(s) of the [Agentic State framework](https://agenticstate.org/paper.html) does it map to? Structured fields are extracted (name, organisation, countries, description, novelty, stakeholders, year, tags, layers). Only agentic items are stored (`AGENTIC_ONLY=0` widens scope to all AI-in-gov).
3. **Dedupe** — by URL (ever) and by initiative name (last 60 days, catches the same story from multiple outlets).
4. **Store** — records are appended to [data/innovations.jsonl](data/innovations.jsonl) (one JSON object per line) and committed. Git history is the archive; no snapshot files.
5. **Digest** — a short Slack message (LLM-written lede + one line per item, agentic items first) posted via incoming webhook.

There are no servers: GitHub Actions runs the pipeline, the repo is the database, GitHub Pages serves the frontend ([site/index.html](site/index.html), a single static page reading the JSONL).

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
python scripts/test_pipeline_local.py  # smoke test, no API keys needed
```

## Data

`data/innovations.jsonl` — ~1,050 AI-in-government records since January 2025, one per line:

```json
{"id": "…", "name": "…", "organisation": "…", "countries": ["…"], "description": "…",
 "novelty": "…", "stakeholders": "…", "agentic_rationale": "why this is agentic",
 "tech_details": "…", "providers": ["Anthropic", "…"], "autonomy_level": 4,
 "status": "pilot", "news_date": "YYYY-MM-DD", "year": "2026",
 "url": "…", "sources": ["further urls for the same story"],
 "source": "google_news:<gl> | rss:<domain> | x_grok | web_grok", "date_added": "YYYY-MM-DD",
 "agentic": true, "tags": ["pilot", "…"], "layers": ["workflows", "…"], "functions": ["f46", "…"]}
```

Controlled vocabularies live in [data/taxonomies.json](data/taxonomies.json): `layers` (the framework's 12 layers), `autonomy_level` (the vision paper's L0 manual → L5 fully autonomous ladder), `status` (anchored to EU JRC AI Watch lifecycle: announced / in-development / pilot / implemented / scaled / discontinued / unclear). `functions` uses the 70 government functions from the WEF Agentic State report ([data/functions.json](data/functions.json)).

Same-story duplicates across outlets are merged by an LLM editorial pass at the end of each run (extra URLs land in `sources`); re-tells of records from the last 14 days are dropped.

Records with `"source": "google_alerts_legacy"` were seeded from the predecessor project ([GovServiceX](https://github.com/essemmeppi/GovServiceX)); keyword-flagged agentic ones have `"agentic": true`, the rest `null` (unclassified). To remove a bad entry, delete its line and commit.

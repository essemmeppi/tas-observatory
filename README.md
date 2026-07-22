# TAS Observatory

Daily observatory of news about **AI and agentic AI in government**, run by [The Agentic State](https://agenticstate.org).

## How it works

One GitHub Action runs every morning ([.github/workflows/observatory.yml](.github/workflows/observatory.yml)):

1. **Ingest** — multilingual Google News RSS search queries (EN/FR/ES/DE/IT/PT + IN/SG editions, no Google account needed; redirect links decoded to real article URLs) + gov-tech RSS feeds ([data/feeds.json](data/feeds.json)) + a daily X sweep via Grok's `x_search` tool.
2. **Filter & extract** — each new article's text is extracted (trafilatura) and assessed in a single LLM call: is it a concrete AI-in-government development? Is it *agentic*? Which layer(s) of the [Agentic State framework](https://agenticstate.org/paper.html) does it map to? Structured fields are extracted (name, organisation, countries, description, novelty, stakeholders, year, tags, layers).
3. **Dedupe** — by URL (ever) and by initiative name (last 60 days, catches the same story from multiple outlets).
4. **Store** — records are appended to [data/innovations.jsonl](data/innovations.jsonl) (one JSON object per line) and committed. Git history is the archive; no snapshot files.
5. **Digest** — a short Slack message (LLM-written lede + one line per item, agentic items first) posted via incoming webhook.

There are no servers: GitHub Actions runs the pipeline, the repo is the database. A future public frontend (Cloudflare Pages) can read the JSONL directly.

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
 "novelty": "…", "stakeholders": "…", "year": "2026", "url": "…",
 "source": "google_news:<gl> | rss:<domain> | x_grok", "date_added": "YYYY-MM-DD",
 "agentic": true, "tags": ["pilot", "…"], "layers": ["workflows", "…"]}
```

`layers` maps each record to the Agentic State framework: `service-design-ux`, `workflows`, `policy-rulemaking`, `compliance-supervision`, `crisis-response`, `procurement`, `agent-governance`, `data-privacy`, `tech-stack`, `cybersecurity`, `public-finance`, `people-culture`.

Records with `"source": "google_alerts_legacy"` were seeded from the predecessor project ([GovServiceX](https://github.com/essemmeppi/GovServiceX)); keyword-flagged agentic ones have `"agentic": true`, the rest `null` (unclassified). To remove a bad entry, delete its line and commit.

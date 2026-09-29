"""Configuration for the agentic-AI-in-government observatory. All secrets via env."""
import os
from pathlib import Path

ROOT = Path(__file__).parents[1]
DB_PATH = ROOT / "data" / "innovations.jsonl"
FEEDS_PATH = ROOT / "data" / "feeds.json"

# LLM used for per-article filtering/extraction and the daily digest.
# Any OpenAI-compatible endpoint works (OpenAI, Moonshot/Kimi, xAI, ...):
#   OpenAI:   LLM_BASE_URL=https://api.openai.com/v1      LLM_MODEL=gpt-4o-mini
#   Kimi:     LLM_BASE_URL=https://api.moonshot.ai/v1     LLM_MODEL=kimi-k2-0711-preview
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
DIGEST_MODEL = os.getenv("DIGEST_MODEL", LLM_MODEL)

# The relevance gate gets its own model. It is ~96% of all LLM calls, and measured
# on the 2026-07-27 run a single gate call took ~25s against ~2.5s for all the
# decoding and fetching put together -- the model was ~88% of the run's wall-clock.
# kimi-k2.5 lists `reasoning` among its supported parameters, so a two-field yes/no
# judgement was paying for hidden thinking tokens. Extraction keeps LLM_MODEL: it
# runs a handful of times a night and its output is what readers actually see.
# Both default to current behaviour; set them as repo variables to switch.
GATE_MODEL = os.getenv("GATE_MODEL", LLM_MODEL)
GATE_REASONING = os.getenv("GATE_REASONING", "0") == "1"

# Daily X sweep: a Grok model called through OpenRouter with its web/x_search
# plugin, using the same LLM_API_KEY. Runs only when LLM_BASE_URL is OpenRouter.
# Set XSWEEP_MODEL="" to disable.
XSWEEP_MODEL = os.getenv("XSWEEP_MODEL", "x-ai/grok-4.3")

# Slack incoming webhook for the daily digest. Optional: skipped if unset.
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "").strip()
if SLACK_WEBHOOK_URL and not SLACK_WEBHOOK_URL.startswith("http"):
    SLACK_WEBHOOK_URL = "https://" + SLACK_WEBHOOK_URL

# Safety valves for a single run. The cap is a runaway guard, not a working
# limit: a normal night brings ~400-450 new items, and a cap below that trims
# the lowest-ranked of every source each night (at 320 it cut ~100 on
# 2026-09-29). It exists for the abnormal night - a feed that suddenly returns
# thousands of entries, or a bug that repeats the queue.
MAX_ITEMS_PER_RUN = int(os.getenv("MAX_ITEMS_PER_RUN", "600"))
# Processing loop cutoff, kept 20 minutes under the workflow's 80-minute hard
# kill so the commit step always runs. With WORKERS in flight a full queue
# takes ~25 minutes, so this is reached only when something is slow.
TIME_BUDGET_MIN = int(os.getenv("TIME_BUDGET_MIN", "60"))
# Articles assessed at once. ~13s per article is almost all network waiting
# (redirect, page, model), so threads overlap it; one at a time reached only
# 281 of 320 in the budget on 2026-09-29. Kept low for Google's redirect
# decoding, which throttled the runner once (2026-08-17).
WORKERS = int(os.getenv("WORKERS", "4"))

# Only store items classified as agentic AI (the observatory's focus).
AGENTIC_ONLY = os.getenv("AGENTIC_ONLY", "1") == "1"

# Public frontend, linked from the Slack digest.
SITE_URL = "https://observatory.agenticstate.org/"

# How far back to look for near-duplicate names when deduping. This check is a
# free string comparison, so it can afford a wide window.
DEDUP_WINDOW_DAYS = 60

# How far back the LLM dedupe pass compares against. Narrower on purpose: these
# records are rendered into a prompt (~18 records / ~1.2k tokens at 14 days).
DEDUP_LLM_WINDOW_DAYS = int(os.getenv("DEDUP_LLM_WINDOW_DAYS", "14"))

REQUEST_TIMEOUT = 30
MAX_ARTICLE_CHARS = 12_000

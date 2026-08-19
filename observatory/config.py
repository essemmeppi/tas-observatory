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

# Safety valves for a single run. Both are calibrated against what the pipeline
# actually collects (~300 distinct candidates a day) and the workflow's 80-minute
# hang backstop -- not against the old 25-minute ingest, which is why the previous
# pair (150 / 30) silently discarded 232 of 301 candidates on 2026-07-26.
MAX_ITEMS_PER_RUN = int(os.getenv("MAX_ITEMS_PER_RUN", "320"))
# ~26s per article observed, so 60 min covers ~140. Not all ~300: reaching those
# needs concurrent gate calls, which is a separate change.
TIME_BUDGET_MIN = int(os.getenv("TIME_BUDGET_MIN", "60"))  # processing loop cutoff

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

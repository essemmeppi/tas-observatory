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

# Daily X sweep: a Grok model called through OpenRouter with its web/x_search
# plugin, using the same LLM_API_KEY. Runs only when LLM_BASE_URL is OpenRouter.
# Set XSWEEP_MODEL="" to disable.
XSWEEP_MODEL = os.getenv("XSWEEP_MODEL", "x-ai/grok-4.3")

# Slack incoming webhook for the daily digest. Optional: skipped if unset.
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "").strip()
if SLACK_WEBHOOK_URL and not SLACK_WEBHOOK_URL.startswith("http"):
    SLACK_WEBHOOK_URL = "https://" + SLACK_WEBHOOK_URL

# Safety valves for a single run.
MAX_ITEMS_PER_RUN = int(os.getenv("MAX_ITEMS_PER_RUN", "150"))
TIME_BUDGET_MIN = int(os.getenv("TIME_BUDGET_MIN", "30"))  # processing loop cutoff

# Only store items classified as agentic AI (the observatory's focus).
AGENTIC_ONLY = os.getenv("AGENTIC_ONLY", "1") == "1"

# Public frontend, linked from the Slack digest.
SITE_URL = "https://essemmeppi.github.io/tas-observatory/site/"

# How far back to look for near-duplicate names when deduping.
DEDUP_WINDOW_DAYS = 60

REQUEST_TIMEOUT = 30
MAX_ARTICLE_CHARS = 12_000

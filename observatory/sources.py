"""Ingestion sources: Google Alerts feeds, plain RSS feeds, and the X sweep via Grok."""
import json
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

import feedparser
import requests
from googlenewsdecoder import gnewsdecoder

from . import config

UA = {"User-Agent": "Mozilla/5.0 (compatible; TAS-Observatory/1.0)"}


def _parse_feed(url: str):
    """feedparser.parse(url) fetches with NO timeout and can hang a run forever;
    fetch ourselves with a timeout and hand feedparser the bytes."""
    resp = requests.get(url, timeout=config.REQUEST_TIMEOUT, headers=UA)
    resp.raise_for_status()
    return feedparser.parse(resp.content)


def _decode_gnews(link: str) -> str | None:
    """gnewsdecoder's internal requests have no timeout either; enforce one."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(gnewsdecoder, link, 1)
        try:
            decoded = future.result(timeout=25)
        except (FutureTimeout, Exception):
            return None
    if not decoded.get("status"):
        return None
    return decoded["decoded_url"]


def _clean_google_url(url: str) -> str:
    """Extract the real URL from a Google Alerts redirect link."""
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    return params["url"][0] if "url" in params else url


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def fetch_feed(feed_url: str, source_label: str, is_google_alert: bool) -> list:
    """Fetch one feed and return items as {title, url, published, source}."""
    parsed = _parse_feed(feed_url)
    items = []
    for entry in parsed.entries:
        link = entry.get("link", "")
        if is_google_alert:
            link = _clean_google_url(link)
        if not link:
            continue
        items.append({
            "title": _strip_html(entry.get("title", "")),
            "url": link,
            "published": entry.get("published", "") or entry.get("updated", ""),
            "source": source_label,
        })
    return items


def fetch_google_news(query: dict, max_entries: int = 25) -> list:
    """Google News RSS search. Query: {"q": "...", "hl": "en-US", "gl": "US", "ceid": "US:en"}.
    Links are Google redirects and get decoded to the real article URL."""
    params = urllib.parse.urlencode({
        "q": query["q"],
        "hl": query.get("hl", "en-US"),
        "gl": query.get("gl", "US"),
        "ceid": query.get("ceid", "US:en"),
    })
    parsed = _parse_feed(f"https://news.google.com/rss/search?{params}")
    items = []
    for entry in parsed.entries[:max_entries]:
        link = entry.get("link", "")
        if "news.google.com" in link:
            link = _decode_gnews(link)
        if not link:
            continue
        items.append({
            "title": _strip_html(entry.get("title", "")),
            "url": link,
            "published": entry.get("published", ""),
            "source": f"google_news:{query.get('gl', 'US')}",
        })
    return items


def fetch_all_feeds(feeds_path=config.FEEDS_PATH) -> list:
    with open(feeds_path, encoding="utf-8") as fh:
        feeds = json.load(fh)

    items = []
    for url in feeds.get("google_alerts", []):
        try:
            got = fetch_feed(url, "google_alerts", is_google_alert=True)
            print(f"  google_alerts feed: {len(got)} items")
            items.extend(got)
        except Exception as e:
            print(f"  warning: google alerts feed failed ({e})")
    for query in feeds.get("google_news", []):
        try:
            got = fetch_google_news(query)
            print(f"  google_news [{query['q'][:40]}]: {len(got)} items")
            items.extend(got)
        except Exception as e:
            print(f"  warning: google news query failed ({e})")
    for url in feeds.get("rss", []):
        label = f"rss:{urllib.parse.urlparse(url).netloc}"
        try:
            got = fetch_feed(url, label, is_google_alert=False)
            print(f"  {label}: {len(got)} items")
            items.extend(got)
        except Exception as e:
            print(f"  warning: {label} failed ({e})")
    return items


X_SWEEP_PROMPT = """Search X for posts from the last day about agentic AI, AI agents, \
or LLM-based automation being adopted, piloted, announced or debated in GOVERNMENT and \
the PUBLIC SECTOR anywhere in the world (any language; report in English). Look for \
concrete developments: deployments, pilots, procurements, policies, official announcements \
— not generic opinions or marketing.

Return ONLY a JSON array (no other text). Each element:
{"title": "...", "url": "<link to the underlying story or the X post>", "summary": "2-3 sentences: what happened, which government body, which country"}
Return [] if nothing substantive was found. Maximum 10 items, deduplicated."""


def fetch_x_sweep(from_date: str, to_date: str) -> list:
    """Daily X sweep: Grok via OpenRouter with the web/x_search plugin enabled."""
    if not config.XSWEEP_MODEL:
        print("  x sweep: XSWEEP_MODEL disabled, skipping")
        return []
    if "openrouter" not in config.LLM_BASE_URL or not config.LLM_API_KEY:
        print("  x sweep: requires OpenRouter LLM_BASE_URL + LLM_API_KEY, skipping")
        return []

    resp = requests.post(
        f"{config.LLM_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
        json={
            "model": config.XSWEEP_MODEL,
            "messages": [{"role": "user", "content": X_SWEEP_PROMPT}],
            "plugins": [{"id": "web", "engine": "native"}],
            "x_search_filter": {"from_date": from_date, "to_date": to_date},
        },
        timeout=300,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"] or ""

    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        print("  x sweep: no JSON array in response, skipping")
        return []
    try:
        stories = json.loads(match.group(0))
    except json.JSONDecodeError:
        print("  x sweep: could not parse JSON, skipping")
        return []

    items = []
    for s in stories:
        if not isinstance(s, dict) or not s.get("url"):
            continue
        items.append({
            "title": (s.get("title") or "").strip(),
            "url": s["url"].strip(),
            "published": to_date,
            "source": "x_grok",
            # X items carry their own summary; used instead of article extraction.
            "prefetched_text": (s.get("summary") or "").strip(),
        })
    print(f"  x sweep: {len(items)} items")
    return items

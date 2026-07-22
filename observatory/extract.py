"""Article text extraction."""
import requests
import trafilatura

from . import config

UA = {"User-Agent": "Mozilla/5.0 (compatible; TAS-Observatory/1.0)"}


def extract_text(url: str) -> str | None:
    try:
        resp = requests.get(url, timeout=config.REQUEST_TIMEOUT, headers=UA)
        resp.raise_for_status()
        text = trafilatura.extract(resp.text, include_comments=False)
        if not text or len(text) < 200:
            return None
        return text[: config.MAX_ARTICLE_CHARS]
    except Exception:
        return None

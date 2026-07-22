"""Article text extraction."""
import trafilatura

from . import config


def extract_text(url: str) -> str | None:
    try:
        html = trafilatura.fetch_url(url)
        if not html:
            return None
        text = trafilatura.extract(html, include_comments=False)
        if not text or len(text) < 200:
            return None
        return text[: config.MAX_ARTICLE_CHARS]
    except Exception:
        return None

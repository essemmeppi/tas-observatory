"""Article text extraction."""
import re

import requests
import trafilatura

from . import config

UA = {"User-Agent": "Mozilla/5.0 (compatible; TAS-Observatory/1.0)"}

# "Headline - Outlet" (Google News) and "Headline | Site" (CMS defaults). One
# trailing segment only, and never down to a stub: a real title that happens to
# contain a dash keeps it.
_TRAILING_OUTLET = re.compile(r"\s+[-–—|·]\s+[^-–—|·]{2,60}$")


def clean_headline(title: str) -> str:
    """A feed or page title without the outlet suffix, for display beside one."""
    title = (title or "").strip()
    stripped = _TRAILING_OUTLET.sub("", title)
    return stripped if len(stripped) >= 20 else title


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

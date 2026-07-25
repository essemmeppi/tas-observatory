"""URL canonicalisation and source-authority tiers.

Two small jobs that several modules need: collapsing the many URL shapes of one
article to a single identity, and ranking the outlet behind a URL.
"""
import json
import urllib.parse

from . import config

with open(config.ROOT / "data" / "source_tiers.json", encoding="utf-8") as _fh:
    _TIERS = json.load(_fh)

DEFAULT_TIER = int(_TIERS.get("default_tier", 4))
_SUFFIX_TIERS = sorted(
    ((s, int(t)) for t, group in _TIERS.get("suffixes", {}).items() for s in group),
    key=lambda pair: -len(pair[0]),
)
_DOMAIN_TIERS = {
    d: int(t) for t, group in _TIERS.get("domains", {}).items() for d in group
}

# Only strip parameters that are known to be tracking noise. Plenty of the feeds
# carry the article id in the query string (thelec.net?idxno=, aip.ci?id=), so a
# blanket strip would break those URLs.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "gclid", "fbclid", "mc_cid", "mc_eid", "igshid", "spm", "cmpid", "cmp",
    "ref", "ref_src", "referrer", "source", "outputtype", "amp", "at_medium",
    "at_campaign", "__twitter_impression", "sh", "s_kwcid",
}

_HOST_PREFIXES = ("www.", "amp.", "m.", "mobile.")
_PATH_SUFFIXES = ("/amp", "/amp/", ".amp")


def canonical_url(url: str) -> str:
    """One key per article, whatever shape the feed handed us.

    Collapses scheme, `www.`/`amp.`/`m.` hosts, AMP path suffixes, tracking
    parameters and trailing slashes. Google News alone yields several of these
    variants for the same story, and each one used to cost a full LLM call.
    """
    if not url:
        return ""
    split = urllib.parse.urlsplit(url.strip())
    if not split.netloc:
        return url.strip().rstrip("/").lower()

    host = split.netloc.lower()
    for prefix in _HOST_PREFIXES:
        if host.startswith(prefix):
            host = host[len(prefix):]
            break

    path = split.path
    for suffix in _PATH_SUFFIXES:
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    path = path.rstrip("/")

    kept = [
        (k, v) for k, v in urllib.parse.parse_qsl(split.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
    ]
    return urllib.parse.urlunsplit(("https", host, path, urllib.parse.urlencode(kept), ""))


def domain(url: str) -> str:
    host = urllib.parse.urlsplit(canonical_url(url)).netloc
    return host.split(":")[0]


def tier_for(item: dict) -> int:
    """Tier of a feed item, usable before any redirect is decoded.

    Google News hands us a news.google.com link plus the publisher's domain in
    the feed's <source url>; the publisher is what we want to rank on.
    """
    return tier(item.get("publisher") or item.get("url", ""))


def tier(url: str) -> int:
    """Authority of the outlet behind `url`; 1 is best, unlisted domains get
    DEFAULT_TIER. Governs the canonical slot of a merged record, the order of
    its `sources`, and which articles a limited budget is spent on."""
    host = domain(url)
    if not host:
        return DEFAULT_TIER
    for suffix, rank in _SUFFIX_TIERS:
        # `gov.uk` itself is as official as `hmrc.gov.uk`, so match the bare
        # suffix too, not only hosts that sit underneath it.
        if host.endswith(suffix) or host == suffix.lstrip("."):
            return rank
    parts = host.split(".")
    for i in range(len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in _DOMAIN_TIERS:
            return _DOMAIN_TIERS[candidate]
    return DEFAULT_TIER

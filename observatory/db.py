"""The database is data/innovations.jsonl: one JSON record per line."""
import difflib
import hashlib
import json
import re
from datetime import date, timedelta

from . import config, urls


def record_id(url: str) -> str:
    return hashlib.sha1(url.strip().encode()).hexdigest()[:12]


def load_records(db_path=config.DB_PATH) -> list:
    if not db_path.exists():
        return []
    records = []
    with open(db_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


NAME_SIMILARITY = 0.90


def names_match(a: str, b: str, countries_a=None, countries_b=None) -> bool:
    """Equal, one name contains the other, or the two are near-identical strings.

    Equality alone was too strict: "Public Consultation on AI Transparency" and
    "Public consultation on AI transparency and agentic AI regulation" are the
    same story, and the second sailed past an equality test. Containment was still
    too strict: "Ajman Agentic AI Trade Licence Renewal" and "...Trade License
    Renewal" differ by one character and neither contains the other.

    The similarity branch requires a shared country. That is what separates the
    Jacksonville "NAVI"/"Navi" pair (one initiative, merge) from "Microsoft 365
    Copilot" in the UK civil service and "Microsoft 365 Copilot Chat" in San
    Francisco (two deployments, leave alone).

    At 0.90 this matches exactly one pair across the 8,778 pairs in the current
    database, and that pair is a true duplicate.
    """
    x, y = _norm_name(a), _norm_name(b)
    if not x or not y:
        return False
    if countries_a and countries_b and not (set(countries_a) & set(countries_b)):
        return False
    if x == y:
        return True
    shorter, longer = sorted((x, y), key=len)
    # 18 chars / 3 tokens was too strict and hid every two-word initiative name:
    # "genesis mission" (15 chars, 2 tokens) IS a substring of "science a new
    # golden age genesis mission", but containment was never tested and the pair
    # sat in the database as a duplicate. Validated across every same-country pair
    # under 60 days apart: 12/2 catches it with no false positives, while 10/2
    # starts matching a record literally named "Agentic AI".
    if len(shorter) < 12 or len(shorter.split()) < 2:
        return False
    if shorter in longer:
        return True
    # Spelling and inflection variants only. SequenceMatcher penalises length
    # mismatch steeply, so a long headline cannot reach this bar against a short
    # initiative name.
    if countries_a and countries_b:
        return difflib.SequenceMatcher(None, x, y).ratio() >= NAME_SIMILARITY
    return False


class Deduper:
    """The set of article URLs already accounted for, canonicalised.

    Only URLs. A headline-versus-initiative-name check used to live here too, but
    it compared structurally different kinds of string, never fired in practice,
    and when it did it discarded the article's detail instead of merging it. Same-
    story duplicates are resolved after extraction by run.resolve_duplicates,
    which can merge and enrich rather than merely skip.
    """

    def __init__(self, records: list):
        self.urls = set()
        for r in records:
            self.urls.add(urls.canonical_url(r["url"]))
            self.urls.update(urls.canonical_url(u) for u in (r.get("sources") or []))

    def known_url(self, url: str) -> bool:
        return urls.canonical_url(url) in self.urls

    def is_new(self, url: str) -> bool:
        return not self.known_url(url)

    def add(self, url: str):
        """Remember a URL so it is never processed twice in one run.

        Called for *every* item, including rejected ones — the same article
        reaches us through several Google News editions, and without this one URL
        could be extracted and assessed more than once (and, at temperature 0.2,
        come back with contradictory verdicts).
        """
        self.urls.add(urls.canonical_url(url))


def recent_records(records: list, days: int = config.DEDUP_LLM_WINDOW_DAYS) -> list:
    """Records recent enough to compare a new candidate against.

    Deliberately narrower than DEDUP_WINDOW_DAYS: this list goes into a prompt,
    while the name-containment check above costs nothing and can look further back.
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return [r for r in records if (r.get("date_added") or "") >= cutoff and r.get("name")]


def append_records(records: list, db_path=config.DB_PATH):
    with open(db_path, "a", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_records(records: list, db_path=config.DB_PATH):
    """Rewrite the whole file. Needed when a run merges into records that are
    already stored rather than only appending new ones."""
    with open(db_path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

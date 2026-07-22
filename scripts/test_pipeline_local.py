"""Local smoke test for the observatory pipeline with the LLM step mocked.

Verifies: feed ingestion, dedup against the real DB, article extraction,
record construction, digest formatting. Run: python scripts/test_pipeline_local.py
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from observatory import db, digest, run, sources  # noqa: E402


def fake_assess(text, url, published):
    return {
        "relevant": True,
        "agentic": "agent" in text.lower(),
        "name": f"Test item for {url[:40]}",
        "organisation": "Test Org",
        "countries": ["Testland"],
        "description": text[:120].replace("\n", " "),
        "novelty": "n/a",
        "stakeholders": "n/a",
        "year": "2026",
        "tags": ["pilot"],
    }


def main():
    existing = db.load_records()
    assert len(existing) > 1000, "DB should contain migrated records"
    deduper = db.Deduper(existing)
    known_url = existing[0]["url"]
    assert not deduper.is_new(known_url), "dedup by URL failed"
    assert deduper.is_new("https://example.org/brand-new"), "fresh URL wrongly deduped"
    print(f"ok: DB loaded ({len(existing)} records), URL dedup works")

    items = sources.fetch_all_feeds()
    assert len(items) > 10, f"expected >10 feed items, got {len(items)}"
    assert all(i["url"].startswith("http") for i in items)
    print(f"ok: feeds fetched ({len(items)} items)")

    fresh = [i for i in items if deduper.is_new(i["url"], i.get("title", ""))]
    print(f"ok: {len(fresh)} fresh items after dedup")

    with patch("observatory.run.llm.assess_article", side_effect=fake_assess):
        records = []
        for item in fresh[:3]:
            r = run.process_item(item, deduper, "2026-07-22")
            if r:
                records.append(r)
    assert records, "no records built from fresh items (extraction failing?)"
    for r in records:
        assert r["id"] and r["url"] and r["date_added"] == "2026-07-22" and r["source"]
    print(f"ok: {len(records)} records built via extraction + (mocked) assessment")

    dup = run.process_item(fresh[0], deduper, "2026-07-22")
    assert dup is None, "second pass should be deduped"
    print("ok: within-run dedup works")

    with patch("observatory.digest.llm.write_digest_lede", return_value="Test lede."):
        with patch("observatory.digest.config.LLM_API_KEY", "x"):
            text = digest.build_digest(records, "2026-07-22")
    assert "TAS Observatory" in text and "Test lede." in text
    print("ok: digest formatting works\n")
    print(text[:600])


if __name__ == "__main__":
    main()

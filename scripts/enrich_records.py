"""One-off: enrich existing records with the new metadata schema.

For every record missing 'status', re-assess with the current prompt. Tries to
re-extract the full article (better tech_details); falls back to stored fields.
Adds new fields only — never overwrites existing curated content.
Run with OpenRouter env vars set: python scripts/enrich_records.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from observatory import config, extract, llm  # noqa: E402

NEW_FIELDS = ["agentic_rationale", "tech_details", "providers", "autonomy_level",
              "status", "news_date", "functions"]


def stored_text(r: dict) -> str:
    return (f"{r['name']}. Responsible organisation: {r.get('organisation','')}. "
            f"{r.get('description','')} {r.get('novelty','')} {r.get('stakeholders','')}")


def main():
    if not config.LLM_API_KEY:
        sys.exit("LLM_API_KEY not set")
    records = [json.loads(l) for l in open(config.DB_PATH, encoding="utf-8") if l.strip()]

    def unenriched(r):
        if "status" not in r:
            return True
        # A previous run's network-failure fallback: all new fields at defaults.
        return (r.get("status") == "unclear" and not r.get("agentic_rationale")
                and not r.get("functions") and not r.get("providers")
                and r.get("autonomy_level") is None and not r.get("tech_details"))

    todo = [r for r in records if unenriched(r)]
    print(f"{len(todo)} of {len(records)} records to enrich ({config.LLM_MODEL})", flush=True)

    done = failed = 0
    for i, r in enumerate(todo):
        text = extract.extract_text(r["url"]) or stored_text(r)
        try:
            a = llm.assess_article(text, r["url"], r.get("date_added", ""))
        except Exception as e:
            print(f"  error on {r['id']}: {e}", flush=True)
            a = None
        if a:
            for f in NEW_FIELDS:
                r[f] = a.get(f) if f != "functions" else (a.get("functions") or [])
            if not r.get("news_date"):
                r["news_date"] = r.get("date_added") or ""
            if not r.get("layers"):
                r["layers"] = a.get("layers") or []
            if not r.get("agentic_rationale") and a.get("agentic"):
                r["agentic_rationale"] = ""
            done += 1
        else:
            # Model judged it irrelevant or failed: keep record, mark unknowns.
            r.setdefault("status", "unclear")
            r.setdefault("news_date", r.get("date_added") or "")
            r.setdefault("providers", [])
            r.setdefault("autonomy_level", None)
            r.setdefault("agentic_rationale", "")
            r.setdefault("tech_details", "")
            r.setdefault("functions", [])
            failed += 1
        r.setdefault("sources", [])
        if (i + 1) % 20 == 0:
            print(f"  progress: {i+1}/{len(todo)} (enriched {done}, defaulted {failed})", flush=True)

    with open(config.DB_PATH, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"Enriched {done}, defaulted {failed}. DB rewritten.", flush=True)


if __name__ == "__main__":
    main()

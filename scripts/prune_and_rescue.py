"""One-off: resolve the enrichment-defaulted records per the agreed policy.

- Dead source link (DNS/connection failure, 404, 410) -> DELETE the record.
- Alive link -> strict re-assessment from the stored summary (must be relevant
  AND agentic under the current gate) -> enrich, else DELETE.
- Records already enriched by the main pass are not touched.
Run with OpenRouter env vars set: python scripts/prune_and_rescue.py
"""
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parents[1]))

from observatory import config, llm  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (compatible; TAS-Observatory/1.0)"}
DEAD_CODES = {404, 410}
NEW_FIELDS = ["agentic_rationale", "tech_details", "providers", "autonomy_level",
              "status", "news_date", "functions", "country_codes"]


def defaulted(r):
    return (r.get("status") == "unclear" and not r.get("agentic_rationale")
            and not r.get("functions") and not r.get("providers")
            and r.get("autonomy_level") is None and not r.get("tech_details"))


def link_alive(url: str) -> bool:
    try:
        resp = requests.get(url, timeout=12, headers=UA, stream=True)
        resp.close()
        return resp.status_code not in DEAD_CODES
    except requests.Timeout:
        return True  # slow is not dead; be conservative
    except requests.RequestException:
        return False


def stored_text(r):
    return (f"{r['name']}. Responsible organisation: {r.get('organisation','')}. "
            f"{r.get('description','')} {r.get('novelty','')} {r.get('stakeholders','')}")


def main():
    if not config.LLM_API_KEY:
        sys.exit("LLM_API_KEY not set")
    records = [json.loads(l) for l in open(config.DB_PATH, encoding="utf-8") if l.strip()]
    todo = [r for r in records if defaulted(r)]
    print(f"{len(todo)} defaulted records of {len(records)} total", flush=True)

    dead = cut_not_agentic = rescued = 0
    keep_ids = set()
    for i, r in enumerate(todo):
        if not link_alive(r["url"]):
            dead += 1
            print(f"  DEAD LINK, cut: {r['name'][:60]}", flush=True)
            continue
        try:
            a = llm.assess_article(stored_text(r), r["url"], r.get("date_added", ""))
        except Exception as e:
            print(f"  error on {r['id']} ({e}), keeping as-is", flush=True)
            keep_ids.add(r["id"])
            continue
        if a and a.get("agentic"):
            for f in NEW_FIELDS:
                r[f] = a.get(f) if f not in ("functions", "country_codes") else (a.get(f) or [])
            if not r.get("news_date"):
                r["news_date"] = r.get("date_added") or ""
            if not r.get("layers"):
                r["layers"] = a.get("layers") or []
            keep_ids.add(r["id"])
            rescued += 1
        else:
            cut_not_agentic += 1
            print(f"  NOT AGENTIC under strict gate, cut: {r['name'][:60]}", flush=True)
        if (i + 1) % 20 == 0:
            print(f"  progress: {i+1}/{len(todo)}", flush=True)

    todo_ids = {r["id"] for r in todo}
    kept = [r for r in records if r["id"] not in todo_ids or r["id"] in keep_ids]
    with open(config.DB_PATH, "w", encoding="utf-8") as fh:
        for r in kept:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\nDead links cut: {dead}; not-agentic cut: {cut_not_agentic}; rescued: {rescued}")
    print(f"Database: {len(records)} -> {len(kept)} records", flush=True)


if __name__ == "__main__":
    main()

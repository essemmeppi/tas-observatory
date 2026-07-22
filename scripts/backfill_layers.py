"""Backfill framework layers for records that predate the layer classification.

Uses each record's existing name/description/novelty (no article re-fetch) in one
LLM call per record. Only touches records with empty/missing layers.
Run from the repo root with OpenRouter env vars set:
  LLM_API_KEY=... LLM_BASE_URL=https://openrouter.ai/api/v1 LLM_MODEL=... \
  python scripts/backfill_layers.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from observatory import config, llm  # noqa: E402

VALID = {
    "service-design-ux", "workflows", "policy-rulemaking", "compliance-supervision",
    "crisis-response", "procurement", "agent-governance", "data-privacy",
    "tech-stack", "cybersecurity", "public-finance", "people-culture",
}

PROMPT = """Map this government AI initiative to the layer(s) of the Agentic State framework \
it most concerns (usually 1-2). Layers:
service-design-ux (citizen-facing services/UX), workflows (internal processes/orchestration), \
policy-rulemaking (drafting rules/policy), compliance-supervision (monitoring/inspection/enforcement), \
crisis-response (emergencies), procurement (public purchasing), agent-governance (accountability/oversight \
of autonomous systems), data-privacy (data infrastructure/privacy), tech-stack (APIs/models/compute/infrastructure), \
cybersecurity, public-finance (funding/cost models), people-culture (skills/workforce/leadership).

Respond ONLY with a JSON object: {"layers": ["slug", ...]}"""


def main():
    if not config.LLM_API_KEY:
        sys.exit("LLM_API_KEY not set")
    records = [json.loads(l) for l in open(config.DB_PATH, encoding="utf-8") if l.strip()]
    todo = [r for r in records if not r.get("layers")]
    print(f"{len(todo)} of {len(records)} records need layers ({config.LLM_MODEL})")

    done = failed = 0
    for r in todo:
        text = f"{r['name']} — {r.get('organisation','')}. {r.get('description','')} {r.get('novelty','')}"
        try:
            raw = llm._chat(
                [{"role": "system", "content": PROMPT}, {"role": "user", "content": text[:2000]}],
                model=config.LLM_MODEL, json_mode=True, max_tokens=100,
            )
            data = llm._parse_json(raw) or {}
            layers = [l for l in (data.get("layers") or []) if l in VALID]
            if layers:
                r["layers"] = layers
                done += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  error on {r['id']}: {e}")
            failed += 1
        if (done + failed) % 25 == 0:
            print(f"  progress: {done + failed}/{len(todo)}")

    with open(config.DB_PATH, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"Backfilled {done}, failed/empty {failed}. DB rewritten.")


if __name__ == "__main__":
    main()

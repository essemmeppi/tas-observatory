"""Regenerate llms.txt, the agent-readable rendering of the observatory.

The site is a JS shell — an agent fetching the homepage gets no records —
so llms.txt at the site root carries the full public dataset as plain text:
a short preamble, then every visible record, newest first. Both discovery
paths land here: agents that probe /llms.txt by convention, and agents that
follow the "help yourselves at llms.txt" link in the page header.

The file is regenerated from data/innovations.jsonl on every run rather
than appended to, so dedupes, merges, and edits to existing records are
always reflected. Hidden records are excluded, same as the site. Curated
fields only — pipeline internals (ids, tiers, dedupe metadata) stay out.

Run from the repo root:  python scripts/generate_llms_txt.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]

# Same display labels as the site (index.html LAYERS/TYPES).
LAYERS = {
    "service-design-ux": "Service design & UX", "workflows": "Government workflows",
    "policy-rulemaking": "Policy & rule-making", "compliance-supervision": "Compliance & supervision",
    "crisis-response": "Crisis response", "procurement": "Public procurement",
    "agent-governance": "Agent governance", "data-privacy": "Data & privacy",
    "tech-stack": "Tech stack", "cybersecurity": "Cyber security", "public-finance": "Public finance",
    "people-culture": "People & culture",
}
TYPES = {"deployment": "Deployment", "strategy": "Strategy", "regulation": "Regulation", "procurement": "Procurement"}

PREAMBLE = """\
# The Agentic State Observatory

> A daily record of agentic AI entering government — deployments, strategies, and laws across the world, mapped to the Agentic State framework.

This file is the full public dataset, regenerated on every daily run,
newest first ({count} records as of {latest}).

- Site: https://observatory.agenticstate.org
- The framework: https://agenticstate.org/paper
- Data license: CC BY-SA 4.0 — attribute "The Agentic State Observatory"
- Feedback: hello@agenticstate.org

## Records
"""


def render(record: dict) -> str:
    """Mirror the site's record card: same fields, same labels, same order."""
    countries = ", ".join(record.get("countries") or []) or "Global"
    lines = [f"### {record['name']} ({countries}, {record.get('news_date', '')})"]
    if record.get("organisation"):
        lines.append(f"Organisation: {record['organisation']}")
    layers = ", ".join(LAYERS.get(l, l) for l in record.get("layers") or [])
    if layers:
        lines.append(f"Layer: {layers}")
    types = ", ".join(TYPES.get(t, t) for t in record.get("types") or [])
    if types:
        lines.append(f"Type: {types}")
    if record.get("description"):
        lines.append(f"Summary: {record['description']}")
    why = " ".join(filter(None, [record.get("novelty"), record.get("agentic_rationale")]))
    if why:
        lines.append(f"Why it matters: {why}")
    if record.get("tech_details"):
        lines.append(f"What technology: {record['tech_details']}")
    urls = [u for u in [record.get("url"), *(record.get("sources") or [])] if u]
    titles = record.get("source_titles") or {}
    if urls:
        lines.append("Sources:")
        seen = set()
        for u in urls:
            if u in seen:
                continue
            seen.add(u)
            title = titles.get(u)
            lines.append(f"- {title} — {u}" if title else f"- {u}")
    return "\n".join(lines)


def main() -> None:
    with open(ROOT / "data" / "innovations.jsonl", encoding="utf-8") as fh:
        records = [json.loads(line) for line in fh if line.strip()]
    visible = [r for r in records if not r.get("hidden")]
    visible.sort(key=lambda r: (r.get("news_date", ""), r.get("date_added", "")), reverse=True)

    latest = max((r.get("news_date", "") for r in visible), default="")
    body = PREAMBLE.format(count=len(visible), latest=latest)
    body += "\n" + "\n\n".join(render(r) for r in visible) + "\n"

    (ROOT / "llms.txt").write_text(body, encoding="utf-8")
    print(f"llms.txt: {len(visible)} records, {len(body) // 1000} KB")


if __name__ == "__main__":
    main()

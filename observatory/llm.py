"""LLM calls via any OpenAI-compatible chat completions endpoint."""
import json
import re

import requests

from . import config

with open(config.ROOT / "data" / "functions.json", encoding="utf-8") as _fh:
    _FUNCTIONS = json.load(_fh)["functions"]
FUNCTION_IDS = {f["function_id"] for f in _FUNCTIONS}
FUNCTIONS_PROMPT_LIST = "\n".join(
    f"- {f['function_id']}: {f['function_title']} ({f['category_title']})" for f in _FUNCTIONS
)

ASSESS_PROMPT = """You screen news for an observatory of AGENTIC AI IN GOVERNMENT run by The Agentic State.

You get the text of one article. Decide if it reports a concrete development involving \
AI, AI agents, or automation in GOVERNMENT or the PUBLIC SECTOR: a deployment, pilot, \
procurement, policy, strategy, regulation or official announcement by a public body \
(any country, any level of government).

NOT relevant: private-sector-only news, academic papers without government adoption, \
opinion pieces with no concrete development, vendor marketing with no named public buyer.

Respond with ONLY a JSON object:
{
  "relevant": true/false,
  "agentic": true/false,          // true only if it involves AI agents / agentic AI / autonomous task execution, not just any AI or chatbot
  "name": "short name of the initiative",
  "organisation": "public body responsible",
  "countries": ["Country", ...],  // full English country names
  "country_codes": ["USA", ...],  // ISO 3166-1 alpha-3 codes, same order as countries
  "description": "2-3 sentences: what it is, purpose, results if available",
  "novelty": "1-2 sentences: what is new about it",
  "stakeholders": "1 sentence: users, beneficiaries, parties involved",
  "agentic_rationale": "1-2 sentences: WHY this qualifies as agentic — what does the system do autonomously (multi-step tasks, tool use, decisions)? Empty if not agentic.",
  "tech_details": "1-2 sentences: models, platforms, architecture, integration — only what the source states",
  "providers": ["..."],           // named tech providers/models, e.g. "OpenAI", "Anthropic", "Microsoft", "Salesforce", "Palantir", "sovereign/local model"; [] if unstated
  "autonomy_level": 0-5 or null,  // Agentic State autonomy ladder: 0 manual, 1 rule-based automation, 2 intelligent process automation, 3 agentic workflows, 4 semi-autonomous agents, 5 fully autonomous agents; null if undeterminable
  "status": "...",                // one of: announced, in-development, pilot, implemented, scaled, discontinued, unclear
  "news_date": "YYYY-MM-DD",      // publication date of the source
  "year": "YYYY",                 // year of implementation, else publication year
  "tags": [...],                  // subset of: "agentic-ai", "genai", "chatbot", "policy", "regulation", "procurement", "pilot", "deployment", "strategy", "infrastructure"
  "layers": [...],                // Agentic State framework layer slugs, usually 1-2, see below
  "functions": [...]              // 0-3 government function ids (f1-f70) this touches, from the list below; [] if none clearly applies
}

Agentic State framework layers (use these exact slugs in "layers"):
- "service-design-ux": public service design & UX — proactive, personalised citizen-facing services
- "workflows": government workflows — internal processes, cross-department orchestration
- "policy-rulemaking": policy- and rule-making — drafting, evidence-based or adaptive rules
- "compliance-supervision": regulatory compliance & supervision — monitoring, inspection, enforcement
- "crisis-response": crisis response — emergencies, disaster coordination
- "procurement": public procurement — acquisition processes, purchasing
- "agent-governance": agent governance — accountability, oversight and redress for autonomous systems
- "data-privacy": data & privacy — data infrastructure, information flows, privacy protection
- "tech-stack": tech stack — interfaces, APIs, models, compute, technical infrastructure
- "cybersecurity": cyber security & resilience
- "public-finance": public finance & buying agents — funding and cost models for AI/agents
- "people-culture": people, culture & leadership — skills, workforce, organisational capacity

Government functions (use ids in "functions"):
""" + FUNCTIONS_PROMPT_LIST + """

If relevant is false, the other fields may be empty."""

VALID_STATUS = {"announced", "in-development", "pilot", "implemented", "scaled", "discontinued", "unclear"}


def _chat(messages: list, model: str, json_mode: bool = False, max_tokens: int = 1600) -> str:
    if not config.LLM_API_KEY:
        raise RuntimeError("LLM_API_KEY is not set")
    body = {"model": model, "messages": messages, "temperature": 0.2, "max_tokens": max_tokens}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    resp = requests.post(
        f"{config.LLM_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
        json=body,
        timeout=120,
    )
    resp.raise_for_status()
    # Thinking models can return content=None when reasoning exhausts max_tokens.
    return resp.json()["choices"][0]["message"]["content"] or ""


def _parse_json(text: str) -> dict | None:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _clean_assessment(data: dict) -> dict:
    """Coerce model output into the schema's controlled vocabularies."""
    level = data.get("autonomy_level")
    data["autonomy_level"] = int(level) if isinstance(level, (int, float)) and 0 <= level <= 5 else None
    if data.get("status") not in VALID_STATUS:
        data["status"] = "unclear"
    data["functions"] = [f for f in (data.get("functions") or []) if f in FUNCTION_IDS][:3]
    data["providers"] = [str(p) for p in (data.get("providers") or [])][:5]
    return data


def assess_article(text: str, url: str, published: str) -> dict | None:
    """One call: relevance filter + structured extraction. None if irrelevant/unparseable."""
    user = f'Text: """{text}"""\nPublication date: "{published}"\nURL: "{url}"'
    try:
        raw = _chat(
            [{"role": "system", "content": ASSESS_PROMPT}, {"role": "user", "content": user}],
            model=config.LLM_MODEL,
            json_mode=True,
        )
    except requests.HTTPError:
        # Some providers reject response_format; retry without it.
        raw = _chat(
            [{"role": "system", "content": ASSESS_PROMPT}, {"role": "user", "content": user}],
            model=config.LLM_MODEL,
        )
    data = _parse_json(raw)
    if not data or not data.get("relevant"):
        return None
    return _clean_assessment(data)


MERGE_PROMPT = """You are the deduplication editor of a daily news pipeline about agentic AI in government.

You get (A) today's candidate records and (B) the names of records already in the database \
from the last two weeks. Different outlets report the same story with different names — find them.

Return ONLY a JSON object:
{
  "merge_groups": [[keep_idx, dup_idx, ...], ...],  // groups of today's indices describing the SAME initiative/story; first index = best/most complete record to keep
  "already_known": [idx, ...]                        // today's indices that are re-tells of a record in list B
}
Same story = same initiative by the same government body (wording may differ). Two DIFFERENT \
initiatives from the same country are NOT the same story. When unsure, do NOT merge.
Empty arrays if nothing applies."""


def dedupe_batch(candidates: list, recent_names: list) -> dict:
    """One call over today's batch. Returns {"merge_groups": [...], "already_known": [...]}."""
    lines = [
        f'{i}: {r["name"]} | {", ".join(r.get("countries") or [])} | {r.get("organisation","")} | {r.get("description","")[:160]}'
        for i, r in enumerate(candidates)
    ]
    user = "A) Today's candidates:\n" + "\n".join(lines) + \
        "\n\nB) Recent database records:\n" + "\n".join(f"- {n}" for n in recent_names[:200])
    raw = _chat(
        [{"role": "system", "content": MERGE_PROMPT}, {"role": "user", "content": user}],
        model=config.LLM_MODEL,
        json_mode=True,
        max_tokens=800,
    )
    data = _parse_json(raw) or {}
    return {
        "merge_groups": [g for g in (data.get("merge_groups") or []) if isinstance(g, list) and len(g) > 1],
        "already_known": [i for i in (data.get("already_known") or []) if isinstance(i, int)],
    }


def write_digest_lede(items: list) -> str | None:
    """One short paragraph summarising today's items, for the top of the Slack digest."""
    bullet_lines = "\n".join(
        f"- {r['name']} ({', '.join(r['countries']) or 'n/a'}): {r['description']}" for r in items
    )
    prompt = (
        "You write a daily Slack digest for The Agentic State team about AI and agentic AI "
        "in government. Given today's new items, write 2-3 plain sentences summarising the "
        "most significant developments. No greetings, no markdown headers, no bullet points."
    )
    try:
        return _chat(
            [{"role": "system", "content": prompt}, {"role": "user", "content": bullet_lines}],
            model=config.DIGEST_MODEL,
            max_tokens=600,
        ).strip()
    except Exception as e:
        print(f"  digest lede failed ({e}), using fallback")
        return None

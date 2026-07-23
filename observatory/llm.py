"""LLM calls via any OpenAI-compatible chat completions endpoint."""
import json
import re

import requests

from . import config

ASSESS_PROMPT = """You screen news for an observatory of AI IN GOVERNMENT run by The Agentic State.

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
  "description": "2-3 sentences: what it is, purpose, results if available",
  "novelty": "1-2 sentences: what is new about it",
  "stakeholders": "1 sentence: users, beneficiaries, parties involved",
  "year": "YYYY",                 // year of implementation, else publication year
  "tags": [...],                  // subset of: "agentic-ai", "genai", "chatbot", "policy", "regulation", "procurement", "pilot", "deployment", "strategy", "infrastructure"
  "layers": [...]                 // which layer(s) of the Agentic State framework this maps to, see below; usually 1-2, empty if none fits
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

If relevant is false, the other fields may be empty."""


def _chat(messages: list, model: str, json_mode: bool = False, max_tokens: int = 1200) -> str:
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
    return data


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
            max_tokens=300,
        ).strip()
    except Exception as e:
        print(f"  digest lede failed ({e}), using fallback")
        return None

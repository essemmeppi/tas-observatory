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

GATE_PROMPT = """You screen news for an observatory of AGENTIC AI IN GOVERNMENT run by The Agentic State.

You get the text of one article. Decide two things, and nothing else.

RELEVANT: does it report a concrete development involving AI, AI agents, or automation in \
GOVERNMENT or the PUBLIC SECTOR — a deployment, pilot, procurement, policy, strategy, \
regulation or official announcement by a public body (any country, any level of government)?

NOT relevant: private-sector-only news, academic papers without government adoption, \
opinion pieces with no concrete development, vendor marketing with no named public buyer.

AGENTIC: does it involve AI agents / agentic AI / autonomous task execution, as opposed to \
any AI, an analytics tool or a plain chatbot?

Respond with ONLY a JSON object:
{
  "relevant": true/false,
  "agentic": true/false,
  "subject": "the initiative in 8 words or fewer"
}"""


EXTRACT_PROMPT = """You extract structured records for an observatory of AGENTIC AI IN \
GOVERNMENT run by The Agentic State.

The article you are given has ALREADY been judged relevant to AI in government. Do not \
re-litigate that: extract the record.

Respond with ONLY a JSON object:
{
  "relevant": true,               // always true here; kept for schema stability
  "agentic": true/false,          // true only if it involves AI agents / agentic AI / autonomous task execution, not just any AI or chatbot
  "name": "short name of the initiative",
  "organisation": "public body responsible",
  "countries": ["Country", ...],  // full English country names
  "country_codes": ["USA", ...],  // ISO 3166-1 alpha-3 codes, same order as countries
  "description": "2-3 sentences: what it is, purpose, results if available",
  "novelty": "1-2 sentences: what is new about it",
  "stakeholders": "1 sentence: users, beneficiaries, parties involved",
  "agentic_rationale": "1-2 sentences: the SPECIFIC autonomous behaviour reported for THIS system — what does it concretely decide, execute or coordinate without a human initiating each step? Quote the concrete capability from the source. Do NOT use generic phrases like 'multi-step workflows', 'autonomous task execution' or 'agentic capabilities' unless the source itself describes them concretely. Empty if not agentic.",
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
""" + FUNCTIONS_PROMPT_LIST

VALID_STATUS = {"announced", "in-development", "pilot", "implemented", "scaled", "discontinued", "unclear"}


class BudgetExhausted(RuntimeError):
    """The API is refusing calls for billing reasons, not for this request.

    Worth its own type because it is terminal for the whole run: on 2026-07-25 a
    mid-run credit exhaustion produced 106 identical 402s, hid the dedupe pass,
    and still exited green.
    """


def _is_budget_error(resp) -> bool:
    if resp.status_code == 402:
        return True
    if resp.status_code in (401, 403, 429):
        body = (resp.text or "").lower()
        return any(w in body for w in ("insufficient", "quota", "credit", "billing", "payment"))
    return False


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
    if _is_budget_error(resp):
        raise BudgetExhausted(f"{resp.status_code} from {config.LLM_BASE_URL}: {(resp.text or '')[:200]}")
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


def _call_with_prompt(prompt: str, user: str, max_tokens: int = 1600) -> dict | None:
    try:
        raw = _chat(
            [{"role": "system", "content": prompt}, {"role": "user", "content": user}],
            model=config.LLM_MODEL, json_mode=True, max_tokens=max_tokens,
        )
    except requests.HTTPError:
        # Some providers reject response_format; retry without it. Budget errors
        # raise BudgetExhausted instead and are deliberately not retried — the
        # second call would fail the same way and only burn wall-clock.
        raw = _chat(
            [{"role": "system", "content": prompt}, {"role": "user", "content": user}],
            model=config.LLM_MODEL, max_tokens=max_tokens,
        )
    return _parse_json(raw)


def _user_block(text: str, url: str, published: str) -> str:
    return f'Text: """{text}"""\nPublication date: "{published}"\nURL: "{url}"'


def screen_article(text: str, url: str, published: str = "") -> dict:
    """Cheap first pass: is this relevant, and is it agentic?

    Deliberately separate from extraction. Around 83% of screened articles are
    rejected, and running them through the full schema — the 12 layers, the 70
    government functions, and a dozen generated prose fields — meant paying for
    output that was then discarded. This prompt is a fraction of the size and
    returns a handful of tokens.

    Returns {"relevant": bool, "agentic": bool}; unparseable output is treated as
    not relevant.
    """
    data = _call_with_prompt(GATE_PROMPT, _user_block(text, url, published), max_tokens=200)
    if not data:
        return {"relevant": False, "agentic": False}
    return {"relevant": bool(data.get("relevant")), "agentic": bool(data.get("agentic")),
            "subject": data.get("subject", "")}


def extract_record(text: str, url: str, published: str) -> dict | None:
    """Second pass: the full structured record. Only worth running on survivors."""
    data = _call_with_prompt(EXTRACT_PROMPT, _user_block(text, url, published))
    if not data:
        return None
    return _clean_assessment(data)


def assess_article(text: str, url: str, published: str) -> dict | None:
    """Screen, then extract. None if the article is not relevant.

    Kept as one entry point for the one-off scripts in scripts/; the pipeline
    calls the two stages separately so it can apply AGENTIC_ONLY between them and
    skip extraction entirely for a non-agentic article.
    """
    if not screen_article(text, url, published).get("relevant"):
        return None
    return extract_record(text, url, published)


MERGE_PROMPT = """You are the deduplication editor of a daily news pipeline about agentic AI in government.

You get (A) today's candidate records and (B) records already in the database from the last \
two weeks. Different outlets report the same story with different names — find them.

Return ONLY a JSON object:
{
  "merge_groups": [[keep_idx, dup_idx, ...], ...],   // groups of today's A-indices describing the SAME initiative/story; first index = best/most complete record
  "already_known": [{"candidate": a_idx, "existing": b_idx}, ...]  // each of today's A-indices that re-tells a specific record in B, with THAT record's index
}
Same story = same initiative by the same government body (wording may differ, the name may \
be phrased completely differently, one report may cover more detail than another). Two \
DIFFERENT initiatives from the same country are NOT the same story. When unsure, do NOT merge.
Empty arrays if nothing applies."""


def _record_line(i: int, r: dict) -> str:
    return (f'{i}: {r.get("name","")} | {", ".join(r.get("countries") or [])} '
            f'| {r.get("organisation","")} | {(r.get("description") or "")[:160]}')


def dedupe_batch(candidates: list, recent: list) -> dict:
    """One call over today's batch, retried once.

    `recent` carries country, organisation and a summary per existing record, not
    just a name — matching "VA Agentforce Enterprise License Agreement" to
    "VA Agentforce Expansion" needs more than the two strings.

    Returns {"merge_groups": [[keep, dup, ...]], "already_known": [{candidate, existing}]}.
    """
    user = (
        "A) Today's candidates:\n" + "\n".join(_record_line(i, r) for i, r in enumerate(candidates))
        + "\n\nB) Recent database records:\n" + "\n".join(_record_line(i, r) for i, r in enumerate(recent))
    )
    messages = [{"role": "system", "content": MERGE_PROMPT}, {"role": "user", "content": user}]
    data = None
    for attempt in range(2):
        try:
            data = _parse_json(_chat(messages, model=config.LLM_MODEL, json_mode=True, max_tokens=1000))
        except BudgetExhausted:
            raise
        except Exception as e:
            print(f"  dedupe attempt {attempt + 1} failed ({e})")
            continue
        if data is not None:
            break
    if data is None:
        raise RuntimeError("dedupe returned no parseable JSON after 2 attempts")

    known = []
    for hit in data.get("already_known") or []:
        if isinstance(hit, dict) and isinstance(hit.get("candidate"), int) and isinstance(hit.get("existing"), int):
            known.append({"candidate": hit["candidate"], "existing": hit["existing"]})
        elif isinstance(hit, int):
            # Tolerate the older bare-index shape: we know it is a re-tell but
            # not of what, so it has no merge target.
            known.append({"candidate": hit, "existing": None})
    return {
        "merge_groups": [g for g in (data.get("merge_groups") or []) if isinstance(g, list) and len(g) > 1],
        "already_known": known,
    }


ENRICH_PROMPT = """You are the editor of a database of agentic-AI-in-government initiatives.

Several outlets reported the same initiative. You get the record we already hold (KEPT) and \
the records extracted from the other reports (ADDITIONAL). Produce the best single record.

Use the additional reports to add concrete detail the kept record is missing — named systems, \
figures, deadlines, quoted capabilities, technologies — and to sharpen wording. Keep the kept \
record's framing where it is already better. Do NOT invent anything absent from both.

Return ONLY a JSON object with these fields:
{
  "description": "2-3 sentences: what it is, purpose, results if available",
  "novelty": "1-2 sentences: what is new about it",
  "stakeholders": "1 sentence: users, beneficiaries, parties involved",
  "agentic_rationale": "1-2 sentences: the SPECIFIC autonomous behaviour reported — what it concretely decides, executes or coordinates without a human initiating each step, quoting the source's concrete capability. Empty if the initiative is not agentic.",
  "tech_details": "1-2 sentences: models, platforms, architecture, integration — only what the sources state",
  "status": "one of: announced, in-development, pilot, implemented, scaled, discontinued, unclear",
  "autonomy_level": 0-5 or null
}"""


def merge_records(keeper: dict, dups: list) -> dict | None:
    """Rewrite the kept record's prose using what the duplicate reports add.

    None on failure, so the caller can fall back to a deterministic field fill.
    """
    def block(label: str, records: list) -> str:
        return f"{label}:\n" + "\n\n".join(
            json.dumps({k: r.get(k) for k in
                        ("name", "organisation", "url", "description", "novelty", "stakeholders",
                         "agentic_rationale", "tech_details", "status", "autonomy_level")},
                       ensure_ascii=False, indent=1)
            for r in records
        )

    try:
        raw = _chat(
            [{"role": "system", "content": ENRICH_PROMPT},
             {"role": "user", "content": block("KEPT", [keeper]) + "\n\n" + block("ADDITIONAL", dups)}],
            model=config.LLM_MODEL,
            json_mode=True,
        )
    except BudgetExhausted:
        raise
    except Exception as e:
        print(f"  merge enrichment failed ({e}), filling deterministically")
        return None
    data = _parse_json(raw)
    if not data:
        print("  merge enrichment unparseable, filling deterministically")
        return None
    level = data.get("autonomy_level")
    data["autonomy_level"] = int(level) if isinstance(level, (int, float)) and 0 <= level <= 5 else None
    if data.get("status") not in VALID_STATUS:
        data.pop("status", None)
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
            max_tokens=600,
        ).strip()
    except Exception as e:
        print(f"  digest lede failed ({e}), using fallback")
        return None

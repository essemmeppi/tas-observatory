"""LLM calls via any OpenAI-compatible chat completions endpoint."""
import json
import os
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

RELEVANT asks one question: is a NAMED PUBLIC BODY actually doing something with an AI system \
— deploying, piloting, buying, mandating, regulating, or committing to build one? There must be \
an identifiable government actor AND an identifiable system or rule. Any country, any level of \
government, any stage including announced and in-development.

NOT relevant, however much AI the article discusses:
- research networks, consortia, universities or conferences CONVENING, coordinating, \
coalition-building or publishing findings — a meeting about AI is not a government using AI
- science and research FUNDING policy: grant reform, lab budgets, research strategy, \
"autonomous experiments" or automated laboratories. That is science policy, not government \
operations
- articles about what AI can do in general, market trends, or the state of the field
- vendor or product announcements with no named public buyer
- personnel appointments, staff changes, event notices, award and conference listings
- opinion columns and explainers with no concrete development
- private-sector-only deployments

AGENTIC asks whether the SOURCE describes a system that decides or acts on its own — chooses \
among steps, executes a task, or coordinates a process without a human initiating each step. \
Judge the system the article reports, not the technology in the abstract. A field described as \
"autonomous", a roadmap that aspires to autonomy, or a plain chatbot, assistant, analytics or \
document-classification tool is NOT agentic.

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
  "types": [...],                 // 1-2 of: "deployment", "strategy", "regulation", "procurement" — primary first; see the type rules below
  "layers": [...],                // Agentic State framework layer slugs, usually 1-2, see below
  "functions": [...]              // 0-3 government function ids (f1-f70) this touches, from the list below; [] if none clearly applies
}

Development type rules for "types" — classify the FORM the news takes, never the domain it \
serves (AI used to speed up procurement is a "deployment"; a law about deployments is \
"regulation"). One primary type; add a second only when the record genuinely straddles two:
- "deployment": a nameable AI system in government hands at any stage — pilots, platforms, \
tools, including ones only announced or in development. A specific planned pilot is a \
deployment (its "status" carries the timing); a vague intention to run pilots is "strategy".
- "procurement": the news is the buying itself — a tender, RFP, sources-sought notice, \
framework agreement or contract award. Once the bought system is in use, later reporting \
about it is "deployment".
- "regulation": rules and rule-making — laws, bills, executive orders, binding or soft \
guidance, standards, certifications, and public consultations that feed rule-making.
- "strategy": stated intentions and capacity building — strategies, plans, funding \
programmes, training, partnerships, and the creation of offices, task forces or governance \
bodies. A body created to oversee or enforce rules is "strategy" first, "regulation" second.

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
VALID_TYPES = {"deployment", "strategy", "regulation", "procurement"}

# Extraction asks for ~16 fields including four prose paragraphs, and the `types`
# rules pushed the prompt to ~5k chars. On 2026-07-28 that combination produced 20
# unparseable responses and lost 10 articles the gate had already approved, one
# error naming the cut-off exactly: "line 187 column 1 (char 1023)". 1023 chars is
# far short of what 1600 tokens allows, so the budget was going to a reasoning
# model's hidden thinking and leaving too little for the JSON. Reasoning stays on
# here deliberately — extraction makes the judgement calls readers see — so the
# ceiling has to be large enough for both it and the schema.
EXTRACT_MAX_TOKENS = int(os.getenv("EXTRACT_MAX_TOKENS", "3000"))

# Country names arrive as free text and are the one list field that was never
# checked, so "United States of America" landed beside 77 "United States" and
# forked the site's country filter and choropleth. Normalise the variants we can
# predict; anything unrecognised passes through so a genuinely new country is
# never silently dropped.
COUNTRY_ALIASES = {
    "united states of america": "United States",
    "usa": "United States", "u s a": "United States", "us": "United States",
    "u s": "United States", "america": "United States",
    "united kingdom of great britain and northern ireland": "United Kingdom",
    "uk": "United Kingdom", "u k": "United Kingdom", "great britain": "United Kingdom",
    "britain": "United Kingdom", "england": "United Kingdom",
    "uae": "United Arab Emirates", "u a e": "United Arab Emirates",
    "republic of korea": "South Korea", "korea, republic of": "South Korea",
    "korea": "South Korea", "south korea (republic of korea)": "South Korea",
    "people's republic of china": "China", "prc": "China", "mainland china": "China",
    "russian federation": "Russia", "czechia": "Czech Republic",
    "netherlands (the)": "Netherlands", "the netherlands": "Netherlands",
    "european union (eu)": "European Union", "eu": "European Union",
    "hong kong sar": "Hong Kong", "hong kong, china": "Hong Kong",
    "viet nam": "Vietnam", "türkiye": "Turkey", "turkiye": "Turkey",
    "saudi arabia (kingdom of)": "Saudi Arabia", "ksa": "Saudi Arabia",
}


def canonical_country(name: str) -> str:
    key = re.sub(r"[^a-z0-9' ]+", " ", (name or "").lower()).strip()
    key = re.sub(r"\s+", " ", key)
    return COUNTRY_ALIASES.get(key, (name or "").strip())


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


def _chat(messages: list, model: str, json_mode: bool = False, max_tokens: int = 1600,
          reasoning: bool | None = None) -> str:
    if not config.LLM_API_KEY:
        raise RuntimeError("LLM_API_KEY is not set")
    body = {"model": model, "messages": messages, "temperature": 0.2, "max_tokens": max_tokens}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if reasoning is False:
        # OpenRouter's unified switch. A reasoning model spends most of its latency
        # on hidden thinking tokens, which is wasted on a two-field classification.
        body["reasoning"] = {"enabled": False}
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


def _as_object(data):
    """The object a caller asked for, or None.

    A model that is told to return one JSON object sometimes wraps it in an array
    anyway. Unwrapping a single-element list is free; anything else is not the
    shape the callers expect, and letting it through cost an article on the
    2026-07-29 legacy import — _clean_assessment met a list and raised
    AttributeError, which reads as a defect rather than as unparseable output.
    """
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        return data[0]
    return None


def _parse_json(text: str) -> dict | None:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return _as_object(json.loads(text))
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return _as_object(json.loads(match.group(0)))
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
    # Dedupe preserving order: the first value is the primary type.
    data["types"] = list(dict.fromkeys(
        t for t in (data.get("types") or []) if t in VALID_TYPES))[:2]
    data["providers"] = [str(p) for p in (data.get("providers") or [])][:5]
    # Country names are free text from the model, and were the one list field
    # never checked here; fold the predictable variants onto a single spelling so
    # the site's filter and choropleth do not fork.
    data["countries"] = list(dict.fromkeys(
        canonical_country(c) for c in (data.get("countries") or []) if str(c).strip()))
    return data


def _call_with_prompt(prompt: str, user: str, max_tokens: int = 1600,
                      model: str | None = None, reasoning: bool | None = None) -> dict | None:
    messages = [{"role": "system", "content": prompt}, {"role": "user", "content": user}]
    model = model or config.LLM_MODEL
    try:
        raw = _chat(messages, model=model, json_mode=True, max_tokens=max_tokens,
                    reasoning=reasoning)
    except requests.HTTPError:
        # Some providers reject response_format; retry without it. Budget errors
        # raise BudgetExhausted instead and are deliberately not retried — the
        # second call would fail the same way and only burn wall-clock.
        raw = _chat(messages, model=model, max_tokens=max_tokens, reasoning=reasoning)
    return _parse_json(raw)


def _user_block(text: str, url: str, published: str) -> str:
    return f'Text: """{text}"""\nPublication date: "{published}"\nURL: "{url}"'


def screen_article(text: str, url: str, published: str = "",
                   model: str | None = None, reasoning: bool | None = None) -> dict:
    """Cheap first pass: is this relevant, and is it agentic?

    Deliberately separate from extraction. Around 83% of screened articles are
    rejected, and running them through the full schema — the 12 layers, the 70
    government functions, and a dozen generated prose fields — meant paying for
    output that was then discarded. This prompt is a fraction of the size and
    returns a handful of tokens.

    Runs on GATE_MODEL with reasoning off by default: this is the call the run's
    wall-clock is made of (~25s of a ~28s article on 2026-07-27), and a two-field
    classification has nothing to think about. `model` and `reasoning` override
    both, which is what scripts/bench_gate.py uses to compare candidates.

    Returns {"relevant": bool, "agentic": bool, "subject": str}; unparseable
    output is retried once, then treated as not relevant. The retry matters
    because a "not relevant" verdict is final: process_item records the URL as
    seen before assessing it, so one garbled response loses the story for good.
    HTTP errors are not swallowed here — they propagate and are counted by the
    caller, which is already louder than a quiet false negative.
    """
    user = _user_block(text, url, published)
    data = None
    for attempt in range(2):
        data = _call_with_prompt(
            GATE_PROMPT, user, max_tokens=200,
            model=model or config.GATE_MODEL,
            reasoning=config.GATE_REASONING if reasoning is None else reasoning,
        )
        if data:
            break
        print(f"  gate attempt {attempt + 1} returned unparseable JSON")
    if not data:
        return {"relevant": False, "agentic": False}
    return {"relevant": bool(data.get("relevant")), "agentic": bool(data.get("agentic")),
            "subject": data.get("subject", "")}


def extract_record(text: str, url: str, published: str) -> dict | None:
    """Second pass: the full structured record. Only worth running on survivors.

    Retried once, matching dedupe_batch. Without the retry a single unparseable
    response lost the article outright — on 2026-07-26 that cost a primary-source
    government roadmap the gate had already judged relevant and agentic. A budget
    error is not retried: the second call would fail identically.
    """
    user = _user_block(text, url, published)
    for attempt in range(2):
        try:
            data = _call_with_prompt(EXTRACT_PROMPT, user, max_tokens=EXTRACT_MAX_TOKENS)
        except BudgetExhausted:
            raise
        except Exception as e:
            print(f"  extraction attempt {attempt + 1} errored ({e})")
            continue
        if data:
            return _clean_assessment(data)
        print(f"  extraction attempt {attempt + 1} returned unparseable JSON")
    return None


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
            # Dedupe is a semantic judgement, but its whole output is two small
            # index arrays. On 2026-08-05 the main model's hidden thinking spent
            # the entire 1000-token ceiling, content came back empty, both
            # attempts failed and the run fell back to name matching — so
            # reasoning is off here and the ceiling doubled as margin.
            data = _parse_json(_chat(messages, model=config.LLM_MODEL, json_mode=True,
                                     max_tokens=2000, reasoning=False))
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
        "You write the daily brief of the TAS Observatory, a public record of agentic AI in "
        "government read by policymakers, civil servants and researchers. Given today's new "
        "records, write 2-4 plain sentences on the most significant developments, the most "
        "consequential first. Editorial and factual: name the countries and institutions, state "
        "what actually happened, and let the facts carry the weight — no hype adjectives "
        "('groundbreaking', 'revolutionary'), no opinions, no greetings, no markdown, no bullets. "
        "The same text appears on the public site and in the team's Slack."
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

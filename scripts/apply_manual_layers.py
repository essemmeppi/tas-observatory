"""One-off: apply hand-assigned framework layers to the 70 records the LLM
backfill missed (classified by Claude in-session, 2026-07-23)."""
import json
from pathlib import Path

DB = Path(__file__).parents[1] / "data" / "innovations.jsonl"

MAPPING = {
    "fff53e5974fc": ["policy-rulemaking"],
    "39fb14155ea4": ["service-design-ux"],
    "a33af9ba2993": ["workflows", "service-design-ux"],
    "2998cc3c9f78": ["service-design-ux"],
    "b56bdcc04153": ["workflows"],
    "ac2e66cd8212": ["cybersecurity"],
    "df367bb860e4": ["service-design-ux", "workflows"],
    "93746c29c507": ["people-culture"],
    "15474de0c633": ["service-design-ux"],
    "bd7648837262": ["policy-rulemaking", "public-finance"],
    "f70be6d1f04d": ["service-design-ux", "workflows"],
    "c706b99b32bf": ["tech-stack"],
    "450abb136c16": ["workflows"],
    "6d88175ce97a": ["tech-stack"],
    "fa6969216804": ["workflows"],
    "5e506ef1b40a": ["tech-stack"],
    "c6ea418179df": ["procurement", "tech-stack"],
    "2ab69fd0e4e6": ["procurement"],
    "70a96b0b9310": ["workflows"],
    "5117bc3f066c": ["workflows"],
    "bd507f188071": ["service-design-ux"],
    "ddfd0c0d30d9": ["service-design-ux"],
    "9316d7dc488d": ["crisis-response", "workflows"],
    "648d6a310b3b": ["procurement", "agent-governance"],
    "4ed4ebe8d159": ["service-design-ux", "workflows"],
    "5d08e3bf5a2f": ["service-design-ux"],
    "5df655b0d17e": ["workflows"],
    "5adf84df5a7c": ["people-culture"],
    "00cf00ca8974": ["service-design-ux", "workflows"],
    "b95599252d8a": ["workflows"],
    "9d056070215c": ["workflows"],
    "ddec727d1e7f": ["workflows", "people-culture"],
    "7013bff5265e": ["tech-stack"],
    "68c36e124b66": ["tech-stack"],
    "cbc8546350f9": ["workflows", "crisis-response"],
    "11edb4a2d310": ["service-design-ux"],
    "3fb9d33777e6": ["people-culture"],
    "d4124b6f8854": ["cybersecurity", "tech-stack"],
    "f01886531fdb": ["workflows"],
    "314dd13508a0": ["service-design-ux"],
    "b176038fc961": ["agent-governance"],
    "6e2638a4e676": ["crisis-response", "tech-stack"],
    "3bca0a0e4db5": ["people-culture"],
    "4496159f82bf": ["service-design-ux"],
    "75cdcecdfc63": ["procurement"],
    "56edcfa5afbf": ["service-design-ux", "workflows"],
    "d258a206c17b": ["agent-governance"],
    "3d638231bce9": ["workflows"],
    "a75795089144": ["tech-stack", "procurement"],
    "1361dc2a9fb0": ["service-design-ux"],
    "45a2c381e149": ["service-design-ux"],
    "558ae5c72525": ["tech-stack"],
    "e7485543b9b4": ["tech-stack"],
    "d1eab9b07c40": ["procurement", "policy-rulemaking"],
    "53b1645fa0e1": ["workflows"],
    "46abdf9f7507": ["service-design-ux"],
    "9fc07e7b9527": ["tech-stack"],
    "dd9ac9168057": ["workflows"],
    "b18b0ea7f080": ["policy-rulemaking", "procurement"],
    "a33ea17d8458": ["people-culture"],
    "6186a52cc0de": ["cybersecurity"],
    "9998f7afcfbb": ["cybersecurity"],
    "466b6607803d": ["agent-governance"],
    "b87a6f15a8a0": ["workflows"],
    "07afdaf6ed16": ["people-culture"],
    "00e22d7535e7": ["service-design-ux"],
    "8966ee0e4318": ["procurement"],
    "e9bd87ec63c7": ["tech-stack"],
    "f9286a62a123": ["workflows"],
    "507364895422": ["people-culture"],
}

records = [json.loads(l) for l in DB.read_text(encoding="utf-8").splitlines() if l.strip()]
applied = 0
for r in records:
    if not r.get("layers") and r["id"] in MAPPING:
        r["layers"] = MAPPING[r["id"]]
        applied += 1
with open(DB, "w", encoding="utf-8") as fh:
    for r in records:
        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
missing = sum(1 for r in records if not r.get("layers"))
print(f"applied {applied}; records still without layers: {missing}")

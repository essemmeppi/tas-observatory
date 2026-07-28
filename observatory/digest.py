"""Daily digest, rendered twice from one structured object.

The Slack message and the site's digest archive (data/digests.jsonl, read by
site/digest.html) carry the same content — stats sentence, lede, items — so
the text only has to be got right once. The one deliberate difference: the
:warning: incomplete-run block is Slack-only. It is an ops note for the team;
on a public page it reads as noise.
"""
import json

import requests

from . import config, llm

ARCHIVE_PATH = config.ROOT / "data" / "digests.jsonl"


def _warning(degraded: str | None, unassessed: int, dedupe_ran: bool) -> str:
    """Say so when the harvest is incomplete.

    The 2026-07-25 run lost its LLM budget after 23 of 150 articles and posted a
    digest that looked entirely normal — including two duplicates the unfunded
    dedupe pass never saw.
    """
    if not degraded and dedupe_ran:
        return ""
    bits = []
    if degraded:
        bits.append(degraded)
    if unassessed > 0:
        bits.append(f"{unassessed} articles never assessed")
    if not dedupe_ran:
        bits.append("duplicate check did not run — items may repeat earlier records")
    return ":warning: *Incomplete run* — " + "; ".join(bits) + "."


def _stats_sentence(scanned: int, assessed: int, n_new: int, n_updated: int,
                    show_assessed: bool = False) -> str:
    """"We scanned X and found Y" — the day's work in one line.

    The public page never renders `assessed`: readers care what was found, not
    how far the queue got. Slack shows it whenever it fell short of `scanned`,
    because that is the team's only always-on signal that coverage was partial —
    the :warning: block counts articles the queue never reached, but says
    nothing about articles the MAX_ITEMS cap silently kept out of the queue.
    """
    if not scanned:
        return ""
    head = f"Scanned {scanned} new sources"
    if show_assessed and assessed and assessed < scanned:
        head += f", assessed {assessed}"
    found = (f"{n_new} new initiative{'s' if n_new != 1 else ''}"
             if n_new else "no new initiatives")
    if n_updated:
        found += f"; {n_updated} existing record{'s' if n_updated != 1 else ''} updated"
    return f"{head}: {found}."


def _snapshot(r: dict) -> dict:
    """What the archive keeps per item: enough to render the day's note forever,
    even if the record is later merged away or renamed."""
    return {"id": r["id"], "name": r["name"], "countries": r.get("countries") or [],
            "description": r.get("description", ""), "url": r.get("url", "")}


def archive_row(items: list, run_date: str, enriched: list, lede: str | None,
                scanned: int = 0, assessed: int = 0) -> dict:
    return {
        "date": run_date,
        "scanned": scanned,
        "assessed": assessed,
        "lede": lede or "",
        "new": [_snapshot(r) for r in items],
        "updated": [
            {"id": r["id"], "name": r["name"], "countries": r.get("countries") or [],
             "note": f"now citing {1 + len(r.get('sources') or [])} sources"}
            for r in enriched
        ],
    }


def write_archive(row: dict) -> None:
    """Append the day's digest, replacing any earlier entry for the same date
    so a manual re-run does not duplicate the day.

    Replacing is right for a re-run of the same harvest — merging would carry
    forward records a failed run listed and then lost, and double-count `scanned`.
    It is wrong for two genuinely different harvests on one date, which is what a
    manual test run alongside the scheduled one produces: on 2026-07-28 that
    dropped 7 of the day's 16 records from the note while leaving them in the
    database. That case is rare and operator-driven, so the fix is to make the
    loss visible rather than to guess at merging; scripts/rebuild_digest_day.py
    repairs a day from the database afterwards.
    """
    rows = []
    if ARCHIVE_PATH.exists():
        rows = [json.loads(l) for l in ARCHIVE_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    prior = next((r for r in rows if r.get("date") == row["date"]), None)
    if prior:
        lost = {x["id"] for x in prior.get("new") or []} - {x["id"] for x in row.get("new") or []}
        if lost:
            print(f"  warning: replacing the {row['date']} digest entry drops "
                  f"{len(lost)} record(s) it listed; rebuild with "
                  f"scripts/rebuild_digest_day.py --date {row['date']}")
    rows = [r for r in rows if r.get("date") != row["date"]] + [row]
    rows.sort(key=lambda r: r["date"])
    with open(ARCHIVE_PATH, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def build_digest(items: list, run_date: str, enriched: list | None = None,
                 degraded: str | None = None, unassessed: int = 0,
                 dedupe_ran: bool = True, scanned: int = 0, assessed: int = 0,
                 lede: str | None = None) -> str:
    agentic = [r for r in items if r.get("agentic")]
    other = [r for r in items if not r.get("agentic")]
    enriched = enriched or []

    if lede is None:
        lede = llm.write_digest_lede(items) if (config.LLM_API_KEY and items) else None
    lines = [f"*TAS Observatory — {run_date}*"]
    warning = _warning(degraded, unassessed, dedupe_ran)
    if warning:
        lines += ["", warning]
    stats = _stats_sentence(scanned, assessed, len(items), len(enriched), show_assessed=True)
    if stats:
        lines += ["", stats]
    if lede:
        lines += ["", lede]

    def fmt(r):
        countries = ", ".join(r.get("countries") or []) or "—"
        card = f"{config.SITE_URL}#r={r['id']}"
        urls = [r["url"]] + (r.get("sources") or [])
        src = " ".join(
            f"<{u}|source{' ' + str(i + 1) if len(urls) > 1 else ''}>" for i, u in enumerate(urls)
        )
        return f"• <{card}|{r['name']}> ({countries}) — {r.get('description', '')} · {src}"

    if agentic:
        header = f"*New today ({len(agentic)})*" if not other else f"*Agentic AI ({len(agentic)})*"
        lines += ["", header] + [fmt(r) for r in agentic]
    if other:
        lines += ["", f"*AI in government ({len(other)})*"] + [fmt(r) for r in other]
    if enriched:
        # Today's reporting that improved a record we already held rather than
        # adding one. Worth showing: it used to be discarded silently.
        lines += ["", f"*Updated from new reporting ({len(enriched)})*"]
        lines += [
            f"• <{config.SITE_URL}#r={r['id']}|{r['name']}> "
            f"({', '.join(r.get('countries') or []) or '—'}) — now citing "
            f"{1 + len(r.get('sources') or [])} sources"
            for r in enriched
        ]
    lines += ["", f"<{config.SITE_URL}|Browse the full observatory →>"]
    return "\n".join(lines)


def post_to_slack(text: str) -> bool:
    if not config.SLACK_WEBHOOK_URL:
        print("  slack: SLACK_WEBHOOK_URL not set, skipping")
        return False
    resp = requests.post(
        config.SLACK_WEBHOOK_URL,
        json={"text": text},
        timeout=config.REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return True

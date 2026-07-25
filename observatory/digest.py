"""Daily Slack digest: a short lede plus one line per new item."""
import requests

from . import config, llm


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


def build_digest(items: list, run_date: str, enriched: list | None = None,
                 degraded: str | None = None, unassessed: int = 0,
                 dedupe_ran: bool = True) -> str:
    agentic = [r for r in items if r.get("agentic")]
    other = [r for r in items if not r.get("agentic")]
    enriched = enriched or []

    lede = llm.write_digest_lede(items) if (config.LLM_API_KEY and items) else None
    lines = [f"*TAS Observatory — {run_date}*"]
    warning = _warning(degraded, unassessed, dedupe_ran)
    if warning:
        lines += ["", warning]
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

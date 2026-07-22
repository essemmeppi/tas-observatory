"""Daily Slack digest: a short lede plus one line per new item."""
import requests

from . import config, llm


def build_digest(items: list, run_date: str) -> str:
    agentic = [r for r in items if r.get("agentic")]
    other = [r for r in items if not r.get("agentic")]

    lede = llm.write_digest_lede(items) if config.LLM_API_KEY else None
    lines = [f"*TAS Observatory — {run_date}*"]
    if lede:
        lines += ["", lede]

    def fmt(r):
        countries = ", ".join(r.get("countries") or []) or "—"
        return f"• <{r['url']}|{r['name']}> ({countries}) — {r.get('description', '')}"

    if agentic:
        header = f"*New today ({len(agentic)})*" if not other else f"*Agentic AI ({len(agentic)})*"
        lines += ["", header] + [fmt(r) for r in agentic]
    if other:
        lines += ["", f"*AI in government ({len(other)})*"] + [fmt(r) for r in other]
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

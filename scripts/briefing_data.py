"""Collect the records and numbers behind a weekly or monthly Briefing draft.

The Briefing goes out every Friday (weekly) and on the last Friday of each month
(monthly, in addition to that day's weekly). This script does the deterministic
part — which records fall in the window, and how the window compares with the
recent past — so the drafting agent (.claude/skills/briefing) spends its judgment
on selection, clustering and emerging topics, not on arithmetic.

Windows, by `date_added` (the day a record entered the Observatory):
  weekly   the 7 days ending the day before the run date (Friday -> Thursday)
  monthly  from the previous month's last Friday (inclusive) to the day before
           the run date, so consecutive monthlies tile with no gap or overlap

Usage:
  python scripts/briefing_data.py --period weekly [--date YYYY-MM-DD]
  python scripts/briefing_data.py --is-last-friday [--date YYYY-MM-DD]

--date is the run (send) date and defaults to today in Europe/Rome.
"""

import argparse
import json
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "innovations.jsonl"
FUNCTIONS_PATH = ROOT / "data" / "functions.json"
BRIEFINGS_DIR = ROOT / "data" / "briefings"
SITE_URL = "https://observatory.agenticstate.org/"

# How far back the "before" picture reaches, in windows of the same length.
BASELINE_WINDOWS = {"weekly": 8, "monthly": 3}


def last_friday_of_month(year: int, month: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - 4) % 7)


def is_last_friday(d: date) -> bool:
    return d == last_friday_of_month(d.year, d.month)


def window(period: str, run: date) -> tuple[date, date]:
    end = run - timedelta(days=1)
    if period == "weekly":
        return run - timedelta(days=7), end
    prev = run.replace(day=1) - timedelta(days=1)
    return last_friday_of_month(prev.year, prev.month), end


def load_records() -> list[dict]:
    with open(DB_PATH, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def in_range(r: dict, start: date, end: date) -> bool:
    try:
        d = date.fromisoformat(r.get("date_added", "")[:10])
    except ValueError:
        return False
    return start <= d <= end


def counts(rows: list[dict], key: str) -> Counter:
    c = Counter()
    for r in rows:
        vals = r.get(key) or []
        c.update(vals if isinstance(vals, list) else [vals])
    return c


def full(r: dict, fn_titles: dict) -> dict:
    return {
        "id": r["id"],
        "card": f"{SITE_URL}#r={r['id']}",
        "name": r.get("name"),
        "organisation": r.get("organisation"),
        "countries": r.get("countries"),
        "description": r.get("description"),
        "novelty": r.get("novelty"),
        "stakeholders": r.get("stakeholders"),
        "providers": r.get("providers"),
        "autonomy_level": r.get("autonomy_level"),
        "status": r.get("status"),
        "types": r.get("types"),
        "layers": r.get("layers"),
        "functions": [fn_titles.get(f, f) for f in r.get("functions") or []],
        "agentic": r.get("agentic"),
        "news_date": r.get("news_date"),
        "date_added": r.get("date_added"),
        "url": r.get("url"),
    }


def brief(r: dict) -> dict:
    desc = r.get("description") or ""
    return {
        "name": r.get("name"),
        "countries": r.get("countries"),
        "layers": r.get("layers"),
        "date_added": r.get("date_added"),
        "description": desc[:220] + ("…" if len(desc) > 220 else ""),
    }


def shift(cur: Counter, base: Counter, n_cur: int, n_base: int, top: int = 8) -> list[dict]:
    """Share of records per value now vs. in the baseline, biggest movers first."""
    out = []
    for k in set(cur) | set(base):
        now = cur[k] / n_cur if n_cur else 0
        before = base[k] / n_base if n_base else 0
        out.append({"value": k, "now": cur[k], "now_share": round(now, 3),
                    "baseline_share": round(before, 3), "delta": round(now - before, 3)})
    out.sort(key=lambda x: -abs(x["delta"]))
    return out[:top]


def previous_briefings(period: str, run: date, n: int = 4) -> list[dict]:
    if not BRIEFINGS_DIR.exists():
        return []
    files = sorted(p for p in BRIEFINGS_DIR.glob(f"*-{period}.md") if p.stem[:10] < run.isoformat())
    return [{"file": str(p.relative_to(ROOT)), "text": p.read_text(encoding="utf-8")} for p in files[-n:]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", choices=["weekly", "monthly"])
    ap.add_argument("--date", help="run date YYYY-MM-DD (default: today, Europe/Rome)")
    ap.add_argument("--is-last-friday", action="store_true",
                    help="print 'yes' or 'no' and exit")
    args = ap.parse_args()

    run = date.fromisoformat(args.date) if args.date else datetime.now(ZoneInfo("Europe/Rome")).date()
    if args.is_last_friday:
        print("yes" if is_last_friday(run) else "no")
        return
    if not args.period:
        ap.error("--period is required")

    fn = json.loads(FUNCTIONS_PATH.read_text(encoding="utf-8"))
    fn_titles = {f["function_id"]: f.get("function_title", f["function_id"])
                 for f in fn.get("functions", []) if "function_id" in f}

    rows = load_records()
    start, end = window(args.period, run)
    length = (end - start).days + 1
    base_start = start - timedelta(days=length * BASELINE_WINDOWS[args.period])
    base_end = start - timedelta(days=1)

    cur = [r for r in rows if in_range(r, start, end)]
    base = [r for r in rows if in_range(r, base_start, base_end)]
    before = [r for r in rows if in_range(r, date.min, base_end)]
    seen_countries = set(counts(before, "countries"))

    stats = {
        "records_in_window": len(cur),
        "baseline_records": len(base),
        "baseline_windows": BASELINE_WINDOWS[args.period],
        "total_records_in_db": len(rows),
        "countries_in_window": len(counts(cur, "countries")),
        "first_time_countries": sorted(set(counts(cur, "countries")) - seen_countries),
        "layers": shift(counts(cur, "layers"), counts(base, "layers"), len(cur), len(base)),
        "types": shift(counts(cur, "types"), counts(base, "types"), len(cur), len(base)),
        "status": shift(counts(cur, "status"), counts(base, "status"), len(cur), len(base)),
        "autonomy_mean_now": round(sum(r.get("autonomy_level") or 0 for r in cur) / len(cur), 2) if cur else None,
        "autonomy_mean_baseline": round(sum(r.get("autonomy_level") or 0 for r in base) / len(base), 2) if base else None,
        "top_providers": counts(cur, "providers").most_common(6),
    }

    print(json.dumps({
        "period": args.period,
        "run_date": run.isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "baseline_window": {"start": base_start.isoformat(), "end": base_end.isoformat()},
        "stats": stats,
        "records": [full(r, fn_titles) for r in cur],
        "baseline_records": [brief(r) for r in base],
        "previous_briefings": previous_briefings(args.period, run),
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

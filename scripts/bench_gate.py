"""Compare candidate models for the relevance gate on real articles.

The gate is ~96% of the pipeline's LLM calls and, measured on the 2026-07-27 run,
~88% of its wall-clock: ~25s per call against ~2.5s for all the decoding and
fetching put together. Making it fast is worth more than any other change, but the
gate also decides what never gets read, and its false negatives leave no trace. So
this measures three things at once and refuses to trade the third for the first:

  latency   p50/p90 wall-clock per call
  recall    known-good articles must still be judged relevant AND agentic
  agreement how far the day-to-day verdict mix would shift

Positives come from the curated database — articles that passed the gate, survived
extraction, and a human left in place. A candidate that rejects one of those is
exactly the "we missed something good" failure, so recall is the gate criterion and
speed only breaks ties.

Run with OpenRouter env vars set:
    python scripts/bench_gate.py [--sample 20] [--live 30] [--models a,b,c]
"""
import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from observatory import config, db, extract, llm, run, sources  # noqa: E402

# (label, model, reasoning) — reasoning None means "leave the model's default alone".
CANDIDATES = [
    ("kimi-k2.5 (current)",      "moonshotai/kimi-k2.5",              None),
    ("kimi-k2.5 no-reasoning",   "moonshotai/kimi-k2.5",              False),
    ("gemini-2.5-flash-lite",    "google/gemini-2.5-flash-lite",      False),
    ("gpt-4o-mini",              "openai/gpt-4o-mini",                None),
    ("nova-lite-v1",             "amazon/nova-lite-v1",               None),
    ("llama-3.3-70b",            "meta-llama/llama-3.3-70b-instruct", None),
]


def _fetch(url: str) -> str | None:
    try:
        return extract.extract_text(url)
    except Exception:
        return None


def build_sample(n_positive: int, n_live: int) -> list:
    """Known-good records plus a slice of a real queue.

    Article text is fetched concurrently — this is the bench's own setup cost, not
    the thing being measured, so there is no reason for it to be slow.
    """
    records = db.load_records()
    recent = [r for r in records if r.get("date_added", "") >= "2026-06-01"][-n_positive:]
    print(f"fetching {len(recent)} known-good articles from the database…")
    with ThreadPoolExecutor(max_workers=8) as pool:
        texts = list(pool.map(lambda r: _fetch(r["url"]), recent))
    sample = [{"kind": "positive", "url": r["url"], "title": r["name"], "text": t}
              for r, t in zip(recent, texts) if t]
    print(f"  {len(sample)} usable (the rest are dead links or paywalls)")

    print(f"fetching a live queue slice ({n_live} items)…")
    deduper = db.Deduper(records)
    queue = run.prepare_items(sources.fetch_all_feeds(), deduper)[:n_live]
    with ThreadPoolExecutor(max_workers=8) as pool:
        resolved = list(pool.map(lambda i: sources.resolve_url(i["url"]), queue))
        texts = list(pool.map(lambda u: _fetch(u) if u else None, resolved))
    live = [{"kind": "live", "url": u, "title": i.get("title", ""), "text": t}
            for i, u, t in zip(queue, resolved, texts) if t]
    print(f"  {len(live)} usable")
    return sample + live


def bench_one(label: str, model: str, reasoning, sample: list) -> dict:
    lat, verdicts, errors = [], [], 0
    for item in sample:
        t0 = time.time()
        try:
            v = llm.screen_article(item["text"], item["url"], model=model, reasoning=reasoning)
        except Exception as e:
            errors += 1
            print(f"    error: {type(e).__name__}: {str(e)[:70]}")
            verdicts.append(None)
            continue
        lat.append(time.time() - t0)
        verdicts.append(v)
    return {"label": label, "model": model, "reasoning": reasoning,
            "latency": lat, "verdicts": verdicts, "errors": errors}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=20, help="known-good records to test")
    ap.add_argument("--live", type=int, default=30, help="live queue items to test")
    ap.add_argument("--models", default="", help="comma-separated subset of labels")
    args = ap.parse_args()
    if not config.LLM_API_KEY:
        sys.exit("LLM_API_KEY not set")

    candidates = CANDIDATES
    if args.models:
        wanted = {m.strip() for m in args.models.split(",")}
        candidates = [c for c in CANDIDATES if c[0] in wanted or c[1] in wanted]
        if not candidates:
            sys.exit(f"no candidates matched {wanted}")

    sample = build_sample(args.sample, args.live)
    positives = [i for i, s in enumerate(sample) if s["kind"] == "positive"]
    if not sample:
        sys.exit("no usable articles fetched")
    print(f"\nsample: {len(sample)} articles ({len(positives)} known-good)\n")

    results = []
    for label, model, reasoning in candidates:
        print(f"benchmarking {label} ({model}, reasoning={reasoning})…")
        r = bench_one(label, model, reasoning, sample)
        results.append(r)
        if r["latency"]:
            print(f"    p50 {statistics.median(r['latency']):.1f}s  "
                  f"n={len(r['latency'])}  errors={r['errors']}")

    ref = next((r for r in results if r["label"].endswith("(current)")), results[0])

    print("\n" + "=" * 92)
    print(f"{'candidate':<26}{'p50':>7}{'p90':>8}{'recall':>9}{'agree':>8}{'relevant%':>11}{'err':>6}")
    print("-" * 92)
    for r in results:
        lat = sorted(r["latency"])
        p50 = statistics.median(lat) if lat else float("nan")
        p90 = lat[int(len(lat) * 0.9)] if len(lat) > 2 else (lat[-1] if lat else float("nan"))
        kept = [i for i in positives
                if r["verdicts"][i] and r["verdicts"][i]["relevant"] and r["verdicts"][i]["agentic"]]
        agree = sum(
            1 for a, b in zip(r["verdicts"], ref["verdicts"])
            if a and b and a["relevant"] == b["relevant"] and a["agentic"] == b["agentic"]
        )
        rel = sum(1 for v in r["verdicts"] if v and v["relevant"])
        n = len([v for v in r["verdicts"] if v])
        print(f"{r['label']:<26}{p50:>6.1f}s{p90:>7.1f}s"
              f"{len(kept):>5}/{len(positives):<3}{agree:>6}/{len(sample):<3}"
              f"{100 * rel / max(1, n):>10.0f}%{r['errors']:>6}")
    print("=" * 92)
    print("recall = known-good articles still judged relevant AND agentic. This is the")
    print("gate criterion: a candidate that misses any of them is out, however fast.")
    print("Among those that pass, take the fastest; break ties on price.")

    missed = {}
    for r in results:
        lost = [sample[i]["title"][:58] for i in positives
                if not (r["verdicts"][i] and r["verdicts"][i]["relevant"]
                        and r["verdicts"][i]["agentic"])]
        if lost:
            missed[r["label"]] = lost
    if missed:
        print("\nknown-good articles each candidate would have dropped:")
        for label, lost in missed.items():
            print(f"  {label}:")
            for t in lost:
                print(f"     - {t}")

    out = Path(config.ROOT) / "bench_gate_results.json"
    out.write_text(json.dumps(
        [{k: v for k, v in r.items() if k != "verdicts"} for r in results], indent=1))
    print(f"\nraw latencies written to {out.name}")


if __name__ == "__main__":
    main()

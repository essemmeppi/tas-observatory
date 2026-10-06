---
name: briefing
description: Draft the weekly or monthly TAS Observatory Briefing email from the initiatives added in the period — pick the most interesting public-sector initiatives across topics and geographies, cluster similar ones, name emerging topics, write it in STE100-style plain English, and save it to data/briefings/. Use for "/briefing weekly", "/briefing monthly", or any request to draft the Briefing.
---

# Briefing draft

The Briefing is the email that subscribers of the Agentic State Observatory get
every Friday (weekly) and on the last Friday of each month (monthly). You write
the **first draft**. A human edits it and sends it with Gmail mail merge. You
never send it to subscribers.

Arguments: `weekly` or `monthly`, and optionally a run date `YYYY-MM-DD`
(default: today, Europe/Rome). The run date is the Friday the Briefing goes out.

## 1. Get the data

```bash
python scripts/briefing_data.py --period <weekly|monthly> [--date YYYY-MM-DD] > /tmp/briefing.json
```

It returns:
- `window`: the days covered (by `date_added`).
- `records`: every initiative added in the window, with full fields and a `card`
  link to its page on the Observatory.
- `stats`: shares of layers, types and status, now vs the baseline; first-time
  countries; mean autonomy level; top providers.
- `baseline_records`: short versions of the records from the previous 8 weeks
  (weekly) or 3 months (monthly).
- `previous_briefings`: the last few drafts of the same period type.

Read all of `records`. Skim `baseline_records` and `previous_briefings`.

If `records` is empty, write a two-line draft that says the period was quiet and
links the Observatory, and stop.

## 2. Select the initiatives

Target: **5–7 bullets weekly, 8–10 monthly.** A bullet can be a cluster.

Favour, in this order:
1. **Public-sector agency.** A government body designs, buys, regulates, or
   deploys. Vendors may appear as providers.
2. **Real substance.** Deployments, pilots, rules, and procurements beat vague
   announcements. Higher `status` and a concrete `novelty` beat a press-release
   line.
3. **Novelty for this audience.** A first in a country, a new kind of task given
   to an agent, a new governance tool, unusual autonomy.

Exclude or push down:
- **Vendor-led stories.** For example, "OpenAI launches X for governments" or
  "Microsoft offers agents to public sector". The test: is the `organisation` a
  company, and does the company sell the thing? If yes, leave it out. You can
  mention it in one clause inside a trend only when it shows a real shift.
- Items that a previous briefing already covered, unless there is a real new
  development (say what is new).
- Opinion pieces and warnings with no initiative behind them, unless they
  support an emerging topic.

Diversity rules for the final list:
- **Geography:** no more than one bullet per country (clusters excepted). Try to
  include more than one continent. Include the Global South when the data has it.
  Mention first-time countries from `stats.first_time_countries` when they are
  worth it.
- **Topic:** no two single-item bullets with the same main layer, if a good
  alternative exists. Mix types (deployment, strategy, regulation, procurement).

**Clustering.** When two or more records describe the same kind of move (for
example, three tax authorities deploy filing agents), merge them into one bullet.
Name every country and link every card. A cluster is often stronger than any one
item in it.

## 3. Find what is changing

This becomes the **opening** of the email: flowing prose that reflects on what
is happening globally, before the list. Do this step before you finalise the
list, because the themes you find can change which items earn a bullet. Use two
kinds of evidence:

- **Emerging topics (the main part).** Read the descriptions and novelty fields.
  Find themes that come up again and again in this window, more than in
  `baseline_records`. For example: AI-driven cyber attacks on public bodies and
  agent-based defence; citizen-facing agents that act, not only answer;
  liability rules for agent errors; agents in procurement. These themes do not
  have to match a taxonomy value. Name the theme in plain words, say how many
  records show it and where, and give one concrete example. If
  `previous_briefings` already named the theme, say whether it is growing,
  stable, or fading. Connect the themes to each other — the opening is one
  argument about the period, not a list of observations.
- **Numbers (supporting).** Use `stats` only when a shift is large and makes
  sense, for example "half of this week's records deal with agent governance, up
  from about a third". Do not list statistics for their own sake. A weekly
  window is small, so do not build a claim on one or two records.

Claim only what the records support. Do not say "first ever" or "global trend"
unless the data shows it. Your view is welcome but must stay close to the data.

## 4. Write it — STE100 at 75%

Apply ASD-STE100 Simplified Technical English, relaxed:

Keep:
- Sentences of 20 words or fewer. Paragraphs of 4 sentences or fewer.
- Active voice. Clear subject: who does what.
- One idea per sentence.
- Simple, common verbs ("uses", "starts", "lets", "checks"), not nominalisations
  ("the utilisation of", "the implementation of").
- Same word for the same thing throughout.
- No idioms, hype, or marketing language: no "game-changer", "revolutionise",
  "landscape", "unlock", "harness", "leverage", "cutting-edge", "delve".

Relax:
- Domain words are fine: agentic, procurement, interoperability, LLM, sandbox,
  autonomy level, and organisation names.
- The approved-word dictionary does not apply. Use the clearest normal word.
- An em dash (—) is fine to join a short clause.

Register: plain, calm, precise — an AI lab writing for public servants, not a
LinkedIn post. No emoji, no exclamation marks.

## 5. Shape

```
Subject: <specific, ≤ 9 words, names the strongest item or theme>

<Opening: 2–3 paragraphs weekly, 3–4 monthly. Normal prose, no bullets, no
sub-headings. The global reflection from step 3: what is changing, where, and
why it matters. It may mention items from the list in passing, but it must not
pre-summarise the bullets one by one.>

What happened
• <Initiative or cluster> (<Country>) — <what was done, by whom, 1–3 sentences.> <card link>
• …

<One closing line: explore the Observatory + "Know an initiative we're missing? Share it with us.">
```

Nothing comes after the list except the closing line: no "What we see"
section, no second set of bullets. The reflection lives in the opening only.

Monthly differs only in scale: more bullets, a longer opening, and you may lead
with the month's single biggest change.

Bullet rule: lead with the actor and the action, not the product name. Write
"Uganda's revenue authority started a pilot…", not "TaxBot: Uganda's…". Link the
**name** to its `card`. Do not link raw news sources.

Target length: weekly about 350–450 words; monthly about 600–750 words.

## 6. Check before you finish

Go through the draft again:
- Every bullet traces to a record in `records`. Every number traces to `stats`
  or a count you made from `records`.
- No vendor-led item in the bullets.
- Country and layer diversity rules hold.
- The opening and the bullets do not repeat each other. If a sentence in the
  opening only restates a bullet, cut it or make it a broader point.
- Every sentence has 20 words or fewer. Fix the long ones.
- Look for any banned words or passive voice. Fix them.

## 7. Output

1. Save the draft as `data/briefings/<run-date>-<period>.md` (Markdown, the
   links as `[name](card)`). Below a `---` line at the end, add a short
   "Editor notes" section: the records you nearly picked and why you left them
   out, and any vendor-led items you excluded. The editor uses this to swap
   items.
2. Show the draft in the conversation. Do not post or send it anywhere unless
   the person (or the routine prompt that called you) asks for that.

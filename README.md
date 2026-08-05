# flights-skill

A flight search skill for [Claude Code](https://claude.com/claude-code) and other agent harnesses that read `SKILL.md` files. It searches Google Flights and returns live fares, times, durations and stop counts.

No API key, no account, no paid data provider. It scrapes Google Flights via [`fast-flights`](https://pypi.org/project/fast-flights/), with a replacement parser.

```
$ .venv/bin/python scripts/flights.py search --from RAR --to AKL --date 2026-09-15 --limit 2
Market price: typical

[1] ★ Air New Zealand
  10:15 PM on Tue, Sep 15 → 12:55 AM on Thu, Sep 17 (+2)  (4 hr 40 min, nonstop)
  Price: NZ$465

[2] ★ Air New Zealand
  3:45 PM on Tue, Sep 15 → 6:15 PM on Wed, Sep 16 (+1)  (4 hr 30 min, nonstop)
  Price: NZ$1011
```

## Install

As a Claude Code skill, clone into your skills directory:

```bash
git clone https://github.com/mossly/flights-skill ~/.claude/skills/flights
cd ~/.claude/skills/flights
uv venv .venv && uv pip install --python .venv -r requirements.txt
```

Then ask Claude to find you a flight. It works standalone as a CLI too — see `SKILL.md` for the full argument reference.

## Features

- One-way, round-trip and multi-city itineraries
- Seat class, passenger mix (adults, children, infants in seat or on lap), max stops
- Any currency Google supports; text or JSON output
- Google's market price rating (`low` / `typical` / `high`) and its "best" flight pick

## Why the parser is replaced

The script does not use the upstream `fast-flights` parser. It builds Google's base64 `tfs` query parameter through the library, then parses the returned HTML itself, because upstream drops results and misses prices.

Fixes carried here:

| Problem | Fix |
|---|---|
| A `[:-1]` slice meant to skip a trailing "see more" row silently dropped the only result when a container held one item | Skip the last item only when there is more than one and it has no price |
| Price selector `.YMlIz.FpEdX` intermittently absent | Fall back to the `aria-label` price, two patterns |
| Google dropped `span.mv1WYe` and `.wtdjmc` (August 2026), blanking all times | Recover times and dates from the itinerary `aria-label` |
| Google intermittently returns a page with no result containers, roughly 1 request in 5 | Retry three times with backoff |

## Maintaining it

Scraping a live page is inherently fragile: expect Google to break this again. The failure looks like blank fields or a persistent "No flights parsed from response".

Run with `--debug` to dump the raw `aria-label` of each result. That attribute has survived every markup change so far and carries the entire itinerary, so the repair is almost always to parse the missing field out of it rather than to hunt for Google's new class names. If `--debug` prints nothing, the result containers themselves moved and the `div[jsname=...]` selectors need updating.

## Requirements

Python 3.9+, and `fast-flights` **2.x** — the 3.x line rewrote the public API and is incompatible. `requirements.txt` pins this; do not relax it without porting the script.

## Licence

MIT

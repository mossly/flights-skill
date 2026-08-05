---
name: flights
description: Search for flights and check prices using Google Flights data (no API key required). Use when the user asks to find flights, check fares, compare routes, or plan a trip. Supports one-way, round-trip, and multi-city. Covers seat class, passenger counts, nonstop filters, and live pricing.
---

## Skill: Flight Search

Use when the user asks to search for flights, check prices, find the cheapest fare, or compare routes.

## Setup

One-time, from the skill directory:

```bash
uv venv .venv
uv pip install --python .venv -r requirements.txt
```

Without `uv`:

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt      # Windows: .venv\Scripts\python.exe
```

`requirements.txt` pins `fast-flights<3` deliberately. Version 3.x rewrote the public API and the script will not import against it; if you see an `ImportError` for `FlightData`, an unpinned 3.x is installed.

## Running

```bash
.venv/bin/python scripts/flights.py search [options]
```

On Windows use `.venv\Scripts\python.exe`. No encoding wrapper is needed — the script forces UTF-8 output itself.

## Arguments

| Flag | Description | Default |
|---|---|---|
| `--from IATA` | Origin airport code (repeatable for multi-city) | required |
| `--to IATA` | Destination airport code (repeatable for multi-city) | required |
| `--date YYYY-MM-DD` | Departure date (repeatable for multi-city) | required |
| `--return-date YYYY-MM-DD` | Return date → triggers round-trip mode | — |
| `--seat` | `economy` / `premium-economy` / `business` / `first` | economy |
| `--adults N` | Adult passengers | 1 |
| `--children N` | Child passengers | 0 |
| `--infants-seat N` | Infants in seat | 0 |
| `--infants-lap N` | Infants on lap | 0 |
| `--max-stops N` | Max stops (0 = nonstop only) | any |
| `--limit N` | Max results to show | 10 |
| `--currency CODE` | Currency code (e.g. NZD, AUD, USD) | `$FLIGHTS_CURRENCY`, else NZD |
| `--json` | Output as JSON | text |
| `--debug` | Dump raw aria-labels to stderr | off |

## Trip modes

- **One-way:** single `--from/--to/--date`
- **Round-trip:** add `--return-date`
- **Multi-city:** repeat `--from`, `--to`, `--date` for each leg in order

## Output

Text output shows:

- Market price level: `low` / `typical` / `high`
- Per flight: airline, departure→arrival times, duration, stops, price
- `★` marks the "best" result per Google's ranking

Use `--json` when the result feeds further processing rather than the user.

## Notes

- Results are live and mirror what Google Flights shows in a browser
- The `Impersonate 'chrome_126' does not exist, using 'random'` warning on stderr is harmless
- For round-trips the return leg is auto-constructed as last-destination → first-origin
- Google intermittently serves an empty result page; the script retries three times with backoff before failing

## When parsing breaks

This scrapes a live page, so Google's markup changes will eventually break it. Symptoms are blank fields (`? → ?`) or "No flights parsed from response" that survives retries.

Run the same search with `--debug` first. Every field is recoverable from the `[role="link"][aria-label]` attribute, which has carried the full itinerary — price, airline, both airports, both times, duration, stops — through every markup change so far. Parse the missing field out of that aria-label rather than chasing Google's new CSS class names, which is the pattern all existing fixes follow. `--debug` printing nothing at all means the containers themselves moved, so start from the `div[jsname=...]` selectors instead.

## Example calls

One-way, nonstop only:

```bash
.venv/bin/python scripts/flights.py search --from RAR --to AKL --date 2026-09-15 --max-stops 0
```

Round-trip, business class, 2 adults:

```bash
.venv/bin/python scripts/flights.py search --from RAR --to SYD \
  --date 2026-09-15 --return-date 2026-09-22 --seat business --adults 2
```

Multi-city:

```bash
.venv/bin/python scripts/flights.py search \
  --from RAR --to NAN --date 2026-09-10 \
  --from NAN --to AKL --date 2026-09-15
```

JSON output:

```bash
.venv/bin/python scripts/flights.py search --from RAR --to AKL --date 2026-09-15 --json
```

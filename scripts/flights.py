#!/usr/bin/env python3
"""
flights.py — thin CLI wrapper around fast-flights (Google Flights scraper)

Usage:
  python3 flights.py search --from RAR --to AKL --date 2026-04-15 [options]
  python3 flights.py search --from RAR --to SYD --date 2026-04-15 --return-date 2026-04-22 [options]
  python3 flights.py search --from RAR --to AKL --date 2026-04-15 --to NAN --date 2026-04-20 [options]  (multi-city)

Options:
  --from IATA         Origin airport code (repeatable for multi-city)
  --to IATA           Destination airport code (repeatable for multi-city)
  --date YYYY-MM-DD   Departure date (repeatable for multi-city)
  --return-date       Return date (makes it round-trip)
  --seat              economy|premium-economy|business|first (default: economy)
  --adults N          Number of adult passengers (default: 1)
  --children N        Number of child passengers (default: 0)
  --infants-seat N    Infants in seat (default: 0)
  --infants-lap N     Infants on lap (default: 0)
  --max-stops N       Max stops per leg (0 = nonstop)
  --limit N           Max results to show (default: 10)
  --currency CODE     Currency code for prices, e.g. NZD, USD, AUD (default: $FLIGHTS_CURRENCY or NZD)
  --json              Output as JSON instead of text
  --debug             Dump raw aria-labels to stderr (use when parsing breaks)

Requires fast-flights 2.x — 3.x rewrote the API and is incompatible.
"""

import argparse
import json
import os
import re
import sys
import time
from typing import List, Literal, Optional

# Google returns narrow no-break spaces (U+202F) inside times. On a default
# Windows console (cp1252) printing those raises UnicodeEncodeError, so force
# UTF-8 rather than making every caller set PYTHONIOENCODING.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

try:
    from fast_flights import FlightData, Passengers
    from fast_flights.filter import TFSData
    from fast_flights.primp import Client
    from fast_flights.schema import Flight, Result
    from selectolax.lexbor import LexborHTMLParser
except ImportError as exc:
    sys.exit(
        f"Import failed: {exc}\n\n"
        "This script requires fast-flights 2.x. Version 3.x rewrote the public "
        "API and does not export FlightData/Passengers/TFSData.\n"
        "Install with:  pip install 'fast-flights<3' typing_extensions\n"
        "See README.md for the uv one-liner."
    )

DEFAULT_CURRENCY = os.environ.get("FLIGHTS_CURRENCY", "NZD")


# ---------------------------------------------------------------------------
# Patched parser — fixes two upstream bugs:
#   1. [:-1] slice drops results when a container has only one item
#   2. Falls back to aria-label for price when primary selector misses
# ---------------------------------------------------------------------------

DEBUG = False


def _safe_text(node, strip=True):
    if node is None:
        return ""
    return node.text(strip=strip)


def parse_response_patched(html: str) -> Result:
    parser = LexborHTMLParser(html)
    flights = []

    # Google uses IWWDBc for "best" container and YdtKid for "other flights".
    # IWWDBc is sometimes absent; YdtKid always present.
    containers = parser.css('div[jsname="IWWDBc"], div[jsname="YdtKid"]')

    for i, fl in enumerate(containers):
        is_best = i == 0
        items = fl.css("ul.Rk10dc li")

        # Upstream does [:-1] on non-best containers to skip a trailing
        # "see more" list item — but this nukes results when only 1 item exists.
        # We skip the last item only if there are >1 items AND it looks like
        # a "see more" row (i.e. has no price).
        if not is_best and len(items) > 1:
            last = items[-1]
            if not last.css_first(".YMlIz.FpEdX") and not last.css_first("[aria-label*='dollars']"):
                items = items[:-1]

        for item in items:
            # ── Pull aria-label from the main link div — always present, has everything ──
            aria = ""
            link_div = item.css_first('[role="link"][aria-label]')
            if link_div:
                aria = link_div.attributes.get("aria-label", "")

            if DEBUG:
                print(f"[aria] {aria or '(none)'}", file=sys.stderr)

            # Airline name — primary selector, fallback to aria-label
            name = _safe_text(item.css_first("div.sSHqwe.tPgKwe.ogfYpf span"))
            if not name:
                # aria: "... Nonstop flight with Jetstar. ..." or "1 stop flight with Air New Zealand. ..."
                m = re.search(r'flight with ([^.]+)\.', aria)
                if m:
                    name = m.group(1).strip()

            # Departure / arrival times
            dp_ar = item.css("span.mv1WYe div")
            try:
                departure = " ".join(dp_ar[0].text(strip=True).split())
                arrival = " ".join(dp_ar[1].text(strip=True).split())
            except IndexError:
                departure = arrival = ""

            if not departure:
                departure = _safe_text(item.css_first(".wtdjmc"))

            # 2026-08 fallback: Google dropped both span.mv1WYe and .wtdjmc.
            # aria-label still carries the full itinerary, e.g.
            #   "... Leaves Rarotonga International Airport at 10:15 PM on Tuesday,
            #    September 15 and arrives at Auckland Airport at 12:55 AM on
            #    Thursday, September 17. ..."
            if not departure or not arrival:
                m = re.search(
                    r'Leaves .*? at (.+?) on (\w+, \w+ \d+) and '
                    r'arrives at .*? at (.+?) on (\w+, \w+ \d+)',
                    aria.replace(" ", " "),
                )
                if m:
                    dep_t, dep_d, arr_t, arr_d = (g.strip() for g in m.groups())
                    departure = departure or f"{dep_t} on {dep_d}"
                    arrival = arrival or f"{arr_t} on {arr_d}"

            # Arrival time ahead (+1, +2 days)
            time_ahead = _safe_text(item.css_first("span.bOzv6")).strip().lstrip("+")

            # Duration — primary, then aria-label
            duration = _safe_text(item.css_first("li div.Ak5kof div"))
            if not duration:
                m = re.search(r'Total duration ([^.]+)\.', aria)
                if m:
                    duration = m.group(1).strip()

            # Stops — primary, then aria-label
            stops_text = _safe_text(item.css_first(".BbR8Ec .ogfYpf"))
            if not stops_text:
                m = re.search(r'(Nonstop|\d+ stops?) flight', aria)
                if m:
                    stops_text = m.group(1)
            try:
                stops_fmt = 0 if stops_text.lower().startswith("nonstop") else int(stops_text.split(" ", 1)[0])
            except (ValueError, AttributeError):
                stops_fmt = "Unknown"

            # Delay
            delay = _safe_text(item.css_first(".GsCCve")) or None

            # Price — primary selector, then aria-label
            price_node = item.css_first(".YMlIz.FpEdX")
            price = price_node.text().replace(",", "") if price_node else ""
            if not price or price == "0":
                # span aria-label: "868 New Zealand dollars"
                for span in item.css("span[aria-label]"):
                    lbl = span.attributes.get("aria-label", "")
                    m = re.search(r'(\d[\d,]*)\s+\w+ dollars', lbl)
                    if m:
                        price = span.text().replace(",", "")
                        break
            if not price or price == "0":
                # main aria-label: "From 351 New Zealand dollars. ..."
                m = re.search(r'From (\d[\d,]*) \w+ dollars', aria)
                if m:
                    price = f"NZ${m.group(1)}"

            if not price:
                continue  # skip unparseable rows

            flights.append(Flight(
                is_best=is_best,
                name=name,
                departure=departure,
                arrival=arrival,
                arrival_time_ahead=time_ahead,
                duration=duration,
                stops=stops_fmt,
                delay=delay,
                price=price,
            ))

    # Deduplicate by (name, departure, price) — containers sometimes repeat
    seen = set()
    unique = []
    for f in flights:
        key = (f.name, f.departure, f.price)
        if key not in seen:
            seen.add(key)
            unique.append(f)

    current_price = _safe_text(parser.css_first("span.gOatQ"))

    if not unique:
        raise RuntimeError("No flights parsed from response")

    return Result(current_price=current_price, flights=unique)


# ---------------------------------------------------------------------------
# Fetch + parse (bypasses upstream get_flights_from_filter's parser)
# ---------------------------------------------------------------------------

def fetch_flights(filter: TFSData, currency: str, attempts: int = 3) -> Result:
    """Fetch and parse, retrying on empty responses.

    Google intermittently serves a page with no result containers at all —
    same query, same moment, no error. Observed roughly 1 request in 5.
    A retry clears it; treating it as a hard failure does not.
    """
    data = filter.as_b64()
    params = {
        "tfs": data.decode("utf-8"),
        "hl": "en",
        "tfu": "EgQIABABIgA",
        "curr": currency,
    }
    client = Client(impersonate="chrome_126", verify=False)

    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            res = client.get("https://www.google.com/travel/flights", params=params)
            if res.status_code != 200:
                raise RuntimeError(f"HTTP {res.status_code}")
            return parse_response_patched(res.text)
        except RuntimeError as e:
            last_error = e
            if attempt < attempts:
                if DEBUG:
                    print(f"[retry] attempt {attempt} failed: {e}", file=sys.stderr)
                time.sleep(1.5 * attempt)

    raise RuntimeError(f"{last_error} (after {attempts} attempts)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Google Flights via fast-flights")
    sub = p.add_subparsers(dest="command")

    s = sub.add_parser("search", help="Search for flights")
    s.add_argument("--from", dest="froms", action="append", required=True, metavar="IATA")
    s.add_argument("--to", dest="tos", action="append", required=True, metavar="IATA")
    s.add_argument("--date", dest="dates", action="append", required=True, metavar="YYYY-MM-DD")
    s.add_argument("--return-date", metavar="YYYY-MM-DD")
    s.add_argument("--seat", default="economy",
                   choices=["economy", "premium-economy", "business", "first"])
    s.add_argument("--adults", type=int, default=1)
    s.add_argument("--children", type=int, default=0)
    s.add_argument("--infants-seat", type=int, default=0)
    s.add_argument("--infants-lap", type=int, default=0)
    s.add_argument("--max-stops", type=int, default=None)
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--currency", default=DEFAULT_CURRENCY,
                   help=f"Currency code for prices (default: {DEFAULT_CURRENCY}, "
                        "override with $FLIGHTS_CURRENCY)")
    s.add_argument("--json", dest="as_json", action="store_true")
    s.add_argument("--debug", action="store_true",
                   help="Dump the raw aria-label of each result to stderr. Use this "
                        "first when Google changes its markup and fields go blank.")

    return p.parse_args()


def build_legs(froms, tos, dates, return_date, max_stops) -> List[FlightData]:
    if len(froms) != len(tos) or len(froms) != len(dates):
        print("Error: --from, --to, --date must each appear the same number of times.", file=sys.stderr)
        sys.exit(1)
    legs = [
        FlightData(date=d, from_airport=f.upper(), to_airport=t.upper(), max_stops=max_stops)
        for f, t, d in zip(froms, tos, dates)
    ]
    if return_date:
        legs.append(FlightData(
            date=return_date,
            from_airport=tos[-1].upper(),
            to_airport=froms[0].upper(),
            max_stops=max_stops,
        ))
    return legs


def determine_trip(froms, return_date) -> str:
    if return_date:
        return "round-trip"
    if len(froms) > 1:
        return "multi-city"
    return "one-way"


def format_text(result: Result, limit: int) -> str:
    lines = [f"Market price: {result.current_price or 'unknown'}"]
    for i, f in enumerate(result.flights[:limit], 1):
        best = " ★" if f.is_best else ""
        delay = f" [delay: {f.delay}]" if f.delay else ""
        ahead = f" (+{f.arrival_time_ahead})" if f.arrival_time_ahead else ""
        stops_str = "nonstop" if f.stops == 0 else f"{f.stops} stop{'s' if f.stops != 1 else ''}"
        lines.append(
            f"\n[{i}]{best} {f.name or '(unknown airline)'}\n"
            f"  {f.departure or '?'} → {f.arrival or '?'}{ahead}  ({f.duration or '?'}, {stops_str}){delay}\n"
            f"  Price: {f.price}"
        )
    return "\n".join(lines)


def format_json(result: Result, limit: int) -> str:
    return json.dumps({
        "current_price": result.current_price,
        "flights": [
            {
                "is_best": f.is_best,
                "airline": f.name,
                "departure": f.departure,
                "arrival": f.arrival,
                "arrival_time_ahead": f.arrival_time_ahead,
                "duration": f.duration,
                "stops": f.stops,
                "delay": f.delay,
                "price": f.price,
            }
            for f in result.flights[:limit]
        ],
    }, indent=2)


def cmd_search(args):
    global DEBUG
    DEBUG = args.debug
    legs = build_legs(args.froms, args.tos, args.dates, args.return_date, args.max_stops)
    trip = determine_trip(args.froms, args.return_date)
    passengers = Passengers(
        adults=args.adults,
        children=args.children,
        infants_in_seat=args.infants_seat,
        infants_on_lap=args.infants_lap,
    )
    try:
        filter_ = TFSData.from_interface(
            flight_data=legs,
            trip=trip,
            passengers=passengers,
            seat=args.seat,
            max_stops=args.max_stops,
        )
        result = fetch_flights(filter_, args.currency)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(format_json(result, args.limit) if args.as_json else format_text(result, args.limit))


def main():
    args = parse_args()
    if args.command == "search":
        cmd_search(args)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()

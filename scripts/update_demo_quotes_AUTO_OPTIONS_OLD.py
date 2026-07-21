#!/usr/bin/env python3
"""Build a best-effort delayed quote snapshot for the public demo portal.

This script intentionally publishes only an allowlisted set of public test
symbols. It does not send investor identifiers, credentials, holdings, or local
scenario edits to the quote source.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request


SYMBOLS = ("SGOV", "IBM", "IVR", "NVDA", "PHYS", "PLTR", "QBTS", "SPY", "TSSI", "WMT")
ENDPOINT = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
USER_AGENT = "Mozilla/5.0 (compatible; QSF-Public-Demo-Quote-Refresh/1.0)"
MAX_EXPECTED_QUOTE_AGE_SECONDS = 4 * 24 * 60 * 60


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso_utc(timestamp: int | float) -> str:
    return dt.datetime.fromtimestamp(float(timestamp), tz=dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def quote_quality(timestamp: int | float) -> str:
    """Keep weekends/market holidays valid, but never call an old bar current."""
    observed = dt.datetime.fromtimestamp(float(timestamp), tz=dt.timezone.utc)
    age_seconds = (utc_now() - observed).total_seconds()
    if -300 <= age_seconds <= MAX_EXPECTED_QUOTE_AGE_SECONDS:
        return "public_delayed"
    return "stale_fallback"


def finite_positive(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def fetch_quote(symbol: str, timeout: int = 20) -> dict[str, object]:
    query = urllib.parse.urlencode({"interval": "15m", "range": "5d", "events": "div,splits"})
    url = ENDPOINT.format(symbol=urllib.parse.quote(symbol, safe="")) + "?" + query
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)

    result = payload.get("chart", {}).get("result") or []
    if not result:
        raise ValueError("no chart result")
    chart = result[0]
    timestamps = chart.get("timestamp") or []
    indicators = chart.get("indicators", {}).get("quote") or []
    closes = indicators[0].get("close") if indicators else []
    if not isinstance(closes, list):
        closes = []

    for timestamp, close in reversed(list(zip(timestamps, closes))):
        price = finite_positive(close)
        if price is not None:
            return {
                "price": round(price, 6),
                "as_of": iso_utc(timestamp),
                "source": "Yahoo Finance chart snapshot",
                "quality": quote_quality(timestamp),
            }

    meta_price = finite_positive(chart.get("meta", {}).get("regularMarketPrice"))
    meta_time = chart.get("meta", {}).get("regularMarketTime")
    if meta_price is None or not meta_time:
        raise ValueError("no finite market price")
    return {
        "price": round(meta_price, 6),
        "as_of": iso_utc(meta_time),
        "source": "Yahoo Finance chart snapshot",
        "quality": quote_quality(meta_time),
    }


def read_fallback(path: pathlib.Path | None) -> dict[str, dict[str, object]]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    quotes = payload.get("quotes") if isinstance(payload, dict) else None
    return quotes if isinstance(quotes, dict) else {}


def build_snapshot(fallback: dict[str, dict[str, object]]) -> tuple[dict[str, object], list[str]]:
    quotes: dict[str, dict[str, object]] = {}
    failures: list[str] = []
    for symbol in SYMBOLS:
        try:
            quotes[symbol] = fetch_quote(symbol)
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as error:
            failures.append(f"{symbol}: {type(error).__name__}")
            prior = fallback.get(symbol)
            if isinstance(prior, dict) and finite_positive(prior.get("price")) is not None:
                quotes[symbol] = dict(prior)
                quotes[symbol]["quality"] = "stale_fallback"

    missing = [symbol for symbol in SYMBOLS if symbol not in quotes]
    snapshot = {
        "schema_version": 1,
        "demo": True,
        "generated_at": utc_now().isoformat().replace("+00:00", "Z"),
        "source": "Best-effort Yahoo Finance chart snapshots for an intentionally public test portal. Not an official valuation feed.",
        "failures": failures,
        "missing": missing,
        "quotes": quotes,
    }
    return snapshot, missing


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--fallback", type=pathlib.Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    snapshot, missing = build_snapshot(read_fallback(args.fallback))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if args.strict and missing:
        print("Missing demo quote coverage: " + ", ".join(missing), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

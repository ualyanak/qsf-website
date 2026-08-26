#!/usr/bin/env python3
"""Build a best-effort delayed quote snapshot for the public demo portal.

This script intentionally publishes only an allowlisted set of public test
symbols. It does not send investor identifiers, credentials, holdings, or local
scenario edits to the quote source. Listed-option redistribution requires a
licensed feed, so the seeded option positions use clearly labeled Black-Scholes
model marks anchored to the user-supplied opening premiums. Those estimates
move automatically with delayed underlier prices and time decay; they are never
described as exchange quotes.
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


SYMBOLS = ("SGOV", "BULL", "IBM", "INFQ", "IVR", "NVDA", "PHYS", "PLTR", "QBTS", "SPY", "TSSI", "WMT")
ENDPOINT = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
USER_AGENT = "Mozilla/5.0 (compatible; QSF-Public-Demo-Quote-Refresh/1.0)"
MAX_EXPECTED_QUOTE_AGE_SECONDS = 4 * 24 * 60 * 60
MODEL_RISK_FREE_RATE = 0.04
MODEL_MAX_VOLATILITY = 5.0

# The opening spots are the 9:30 a.m. ET bars on the supplied July 17, 2026
# baseline.  "JAN 27" is treated as January 2027; the listed Jan. 15 expiry and
# OCC identifiers below have been verified.  Each strategy's volatility is
# re-calibrated deterministically to its supplied opening premium.
OPTION_MODEL_SPECS: dict[str, dict[str, object]] = {
    "BULL_C10_20261218": {
        "underlying": "BULL",
        "expiry": "2026-12-18T21:00:00Z",
        "opening_as_of": "2026-08-21T13:30:00Z",
        # The trade supplied an $0.89 premium but no contemporaneous underlier
        # fill.  $8.85 is the latest completed public BULL close available when
        # the date-only Aug. 21 position was registered, so it is an explicit
        # calibration seed rather than a claimed execution-time stock price.
        "opening_spot": 8.85,
        "opening_spot_source": (
            "Aug. 20, 2026 completed public close proxy; "
            "no Aug. 21 execution-time underlier spot was supplied"
        ),
        "opening_mark": 0.89,
        "option_symbols": ["BULL261218C00010000"],
        "legs": [{"type": "call", "strike": 10.0, "ratio": 1.0}],
    },
    "BULL_P10_JAN2027": {
        "underlying": "BULL",
        "expiry": "2027-01-15T21:00:00Z",
        "opening_as_of": "2026-07-17T13:30:00Z",
        "opening_spot": 7.170000076293945,
        "opening_mark": 3.16,
        "option_symbols": ["BULL270115P00010000"],
        "legs": [{"type": "put", "strike": 10.0, "ratio": 1.0}],
    },
    "INFQ_C25_20270115": {
        "underlying": "INFQ",
        "expiry": "2027-01-15T21:00:00Z",
        "opening_as_of": "2026-07-17T13:30:00Z",
        "opening_spot": 8.75,
        "opening_mark": 0.75,
        "option_symbols": ["INFQ270115C00025000"],
        "legs": [{"type": "call", "strike": 25.0, "ratio": 1.0}],
    },
    "INFQ_C10_C17_5_20270115": {
        "underlying": "INFQ",
        "expiry": "2027-01-15T21:00:00Z",
        "opening_as_of": "2026-07-17T13:30:00Z",
        "opening_spot": 8.75,
        "opening_mark": 1.37,
        "option_symbols": ["INFQ270115C00010000", "INFQ270115C00017500"],
        "legs": [
            {"type": "call", "strike": 10.0, "ratio": 1.0},
            {"type": "call", "strike": 17.5, "ratio": -1.0},
        ],
        "minimum_mark": 0.0,
        "maximum_mark": 7.5,
    },
}


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


def finite_nonnegative(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def parse_utc(value: object) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def refreshed_fallback_quote(prior: object) -> dict[str, object] | None:
    if not isinstance(prior, dict) or finite_positive(prior.get("price")) is None:
        return None
    result = dict(prior)
    try:
        result["quality"] = quote_quality(parse_utc(result.get("as_of")).timestamp())
    except (TypeError, ValueError):
        result["quality"] = "stale_fallback"
    return result


def newest_equity_quote(fetched: dict[str, object], prior: object) -> dict[str, object]:
    fallback = refreshed_fallback_quote(prior)
    if fallback is None:
        return fetched
    try:
        fetched_time = parse_utc(fetched.get("as_of"))
        fallback_time = parse_utc(fallback.get("as_of"))
    except (TypeError, ValueError):
        return fetched
    return fallback if fallback_time > fetched_time else fetched


def year_fraction(start: dt.datetime, end: dt.datetime) -> float:
    return max(0.0, (end - start).total_seconds() / (365.0 * 24.0 * 60.0 * 60.0))


def normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def black_scholes(spot: float, strike: float, years: float, volatility: float, option_type: str) -> float:
    if years <= 0:
        return max(spot - strike, 0.0) if option_type == "call" else max(strike - spot, 0.0)
    if volatility <= 0:
        discounted_strike = strike * math.exp(-MODEL_RISK_FREE_RATE * years)
        return max(spot - discounted_strike, 0.0) if option_type == "call" else max(discounted_strike - spot, 0.0)
    root_time = math.sqrt(years)
    d1 = (
        math.log(spot / strike)
        + (MODEL_RISK_FREE_RATE + 0.5 * volatility * volatility) * years
    ) / (volatility * root_time)
    d2 = d1 - volatility * root_time
    if option_type == "call":
        return spot * normal_cdf(d1) - strike * math.exp(-MODEL_RISK_FREE_RATE * years) * normal_cdf(d2)
    return strike * math.exp(-MODEL_RISK_FREE_RATE * years) * normal_cdf(-d2) - spot * normal_cdf(-d1)


def strategy_value(spec: dict[str, object], spot: float, observed: dt.datetime, volatility: float) -> float:
    expiry = parse_utc(spec["expiry"])
    years = year_fraction(observed, expiry)
    value = 0.0
    for raw_leg in spec["legs"]:  # type: ignore[union-attr]
        leg = dict(raw_leg)
        value += float(leg["ratio"]) * black_scholes(
            spot,
            float(leg["strike"]),
            years,
            volatility,
            str(leg["type"]),
        )
    minimum = float(spec.get("minimum_mark", 0.0))
    maximum = float(spec.get("maximum_mark", math.inf))
    return min(max(value, minimum), maximum)


def calibrated_volatility(spec: dict[str, object]) -> float:
    """Return the lowest positive volatility matching the supplied opening mark."""
    target = float(spec["opening_mark"])
    opening_spot = float(spec["opening_spot"])
    opening_time = parse_utc(spec["opening_as_of"])

    def difference(volatility: float) -> float:
        return strategy_value(spec, opening_spot, opening_time, volatility) - target

    steps = 2500
    low = 0.01
    low_difference = difference(low)
    closest = (abs(low_difference), low)
    for index in range(1, steps + 1):
        high = 0.01 + (MODEL_MAX_VOLATILITY - 0.01) * index / steps
        high_difference = difference(high)
        if abs(high_difference) < closest[0]:
            closest = (abs(high_difference), high)
        if low_difference == 0 or high_difference == 0 or (low_difference < 0 < high_difference) or (high_difference < 0 < low_difference):
            for _ in range(70):
                midpoint = (low + high) / 2.0
                midpoint_difference = difference(midpoint)
                if abs(midpoint_difference) < 1e-10:
                    return midpoint
                if (low_difference <= 0 <= midpoint_difference) or (midpoint_difference <= 0 <= low_difference):
                    high = midpoint
                else:
                    low = midpoint
                    low_difference = midpoint_difference
            return (low + high) / 2.0
        low = high
        low_difference = high_difference
    return closest[1]


def calibration_scale(spec: dict[str, object], volatility: float) -> float:
    opening_value = strategy_value(
        spec,
        float(spec["opening_spot"]),
        parse_utc(spec["opening_as_of"]),
        volatility,
    )
    if not math.isfinite(opening_value) or opening_value <= 0:
        return 1.0
    return float(spec["opening_mark"]) / opening_value


def modeled_strategy_value(spec: dict[str, object], spot: float, observed: dt.datetime, volatility: float) -> float:
    value = strategy_value(spec, spot, observed, volatility) * calibration_scale(spec, volatility)
    minimum = float(spec.get("minimum_mark", 0.0))
    maximum = float(spec.get("maximum_mark", math.inf))
    return min(max(value, minimum), maximum)


def build_model_quote(instrument_id: str, spec: dict[str, object], underlying_quote: dict[str, object]) -> dict[str, object]:
    spot = finite_positive(underlying_quote.get("price"))
    if spot is None:
        raise ValueError("missing model underlier")
    observed = parse_utc(underlying_quote.get("as_of"))
    valuation_time = max(observed, utc_now())
    volatility = calibrated_volatility(spec)
    scale = calibration_scale(spec, volatility)
    mark = modeled_strategy_value(spec, spot, valuation_time, volatility)
    if not math.isfinite(mark) or mark < 0:
        raise ValueError("invalid model mark")
    underlying = str(spec["underlying"])
    current_underlier = underlying_quote.get("quality") == "public_delayed"
    return {
        "price": round(mark, 6),
        "as_of": observed.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "valuation_as_of": valuation_time.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source": f"Automatic Black-Scholes estimate from delayed {underlying}; not an option-market quote",
        "quality": "model_delayed" if current_underlier else "stale_model",
        "underlying_symbol": underlying,
        "underlying_price": round(spot, 6),
        "option_symbols": list(spec["option_symbols"]),
        "model": "Black-Scholes approximation with opening-premium calibration",
        "calibration_as_of": str(spec["opening_as_of"]),
        "calibration_opening_spot": round(float(spec["opening_spot"]), 6),
        "calibration_opening_mark": round(float(spec["opening_mark"]), 6),
        "calibration_spot_source": str(spec.get("opening_spot_source", "Published opening underlier anchor")),
        "calibrated_volatility": round(volatility, 6),
        "calibration_scale": round(scale, 6),
        "risk_free_rate": MODEL_RISK_FREE_RATE,
        "instrument_id": instrument_id,
    }


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
            quotes[symbol] = newest_equity_quote(fetch_quote(symbol), fallback.get(symbol))
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as error:
            failures.append(f"{symbol}: {type(error).__name__}")
            prior = refreshed_fallback_quote(fallback.get(symbol))
            if prior is not None:
                quotes[symbol] = prior

    for instrument_id, spec in OPTION_MODEL_SPECS.items():
        try:
            underlying = str(spec["underlying"])
            quotes[instrument_id] = build_model_quote(instrument_id, spec, quotes[underlying])
        except (ValueError, KeyError, TypeError, OverflowError) as error:
            failures.append(f"{instrument_id}: {type(error).__name__}")
            prior = fallback.get(instrument_id)
            if isinstance(prior, dict) and finite_nonnegative(prior.get("price")) is not None:
                quotes[instrument_id] = dict(prior)
                quotes[instrument_id]["quality"] = "stale_model"

    expected = SYMBOLS + tuple(OPTION_MODEL_SPECS)
    missing = [symbol for symbol in expected if symbol not in quotes]
    snapshot = {
        "schema_version": 2,
        "demo": True,
        "generated_at": utc_now().isoformat().replace("+00:00", "Z"),
        "source": "Best-effort public underlier snapshots plus automatic option model estimates for an intentionally public test portal. Not an official valuation feed.",
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

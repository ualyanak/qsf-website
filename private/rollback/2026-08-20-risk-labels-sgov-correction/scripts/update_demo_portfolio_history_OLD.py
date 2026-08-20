#!/usr/bin/env python3
"""Build nightly NAV history for the intentionally public ``ahub`` demo.

The current portfolio remains defined by ``data/demo-accounts.json``.  This
module replays the separate synthetic ledger, downloads raw Yahoo daily closes,
and values the same seeded option models used by ``update_demo_quotes.py``.
It also builds public SPY, GLD, and BTC-USD comparison series normalized to the
formation NAV.  It never creates a point for a market session that has not fully
closed.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import math
import pathlib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any
from zoneinfo import ZoneInfo


import update_demo_quotes as option_models


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = REPOSITORY_ROOT / "data" / "demo-portfolio-ledger.json"
DEFAULT_ACCOUNTS = REPOSITORY_ROOT / "data" / "demo-accounts.json"
DEFAULT_OUTPUT = REPOSITORY_ROOT / "data" / "demo-portfolio-history.json"
YAHOO_HOSTS = ("query2.finance.yahoo.com", "query1.finance.yahoo.com")
YAHOO_ENDPOINT = "https://{host}/v8/finance/chart/{symbol}"
USER_AGENT = "Mozilla/5.0 (compatible; QSF-Public-Demo-History/1.0)"
MARKET_ZONE = ZoneInfo("America/New_York")
MARKET_CLOSE = dt.time(16, 0)
FIRST_CALCULATED_SESSION = dt.date(2026, 7, 20)
EPSILON = 1e-8
BENCHMARK_SPECS: tuple[dict[str, Any], ...] = (
    {
        "id": "spy",
        "label": "SPY (adjusted)",
        "symbol": "SPY",
        "price_basis": "adjusted_close",
    },
    {
        "id": "gold-gld",
        "label": "Gold (GLD proxy, adjusted)",
        "symbol": "GLD",
        "price_basis": "adjusted_close",
    },
    {
        "id": "btc-usd",
        "label": "Bitcoin (BTC-USD)",
        "symbol": "BTC-USD",
        "price_basis": "close",
    },
)
BENCHMARK_SOURCE = "Yahoo Finance daily chart endpoint; public comparison proxy, not an official valuation feed."
EXTERNAL_FLOW_CLASSIFICATIONS = frozenset({"contribution", "deposit", "external_flow", "redemption", "withdrawal"})
EXPOSURE_CATEGORY_STYLES: dict[str, tuple[str, str]] = {
    "cash-cash-equivalents": ("Cash & Cash Equivalents", "#15344f"),
    "financial-technology": ("Financial Technology", "#56816f"),
    "options": ("Options", "#c9a24f"),
    "technology": ("Technology", "#3d6f8c"),
    "real-estate": ("Real Estate", "#8b5f63"),
    "precious-metals": ("Precious Metals", "#967638"),
    "index-funds": ("Index Funds", "#445467"),
    "consumer": ("Consumer", "#7b8793"),
    "financing": ("Financing", "#b45309"),
    "other": ("Other", "#6b7280"),
}


class HistoryError(RuntimeError):
    """Raised when a complete, non-invented history cannot be produced."""


DailyFetcher = Callable[[str, dt.date, dt.date], Mapping[Any, float]]


def load_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HistoryError(f"Could not read valid JSON from {path}") from error
    if not isinstance(payload, dict):
        raise HistoryError(f"Expected a JSON object in {path}")
    return payload


def finite_number(value: object, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise HistoryError(f"{label} is not numeric") from error
    if not math.isfinite(number):
        raise HistoryError(f"{label} is not finite")
    return number


def parse_datetime(value: object, *, date_at_end_of_day: bool = False) -> dt.datetime:
    text = str(value).strip()
    if not text:
        raise HistoryError("Missing date/time")
    if len(text) == 10:
        parsed_date = dt.date.fromisoformat(text)
        parsed_time = dt.time(23, 59, 59) if date_at_end_of_day else dt.time(9, 30)
        return dt.datetime.combine(parsed_date, parsed_time, MARKET_ZONE)
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise HistoryError(f"Invalid ISO date/time: {text}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MARKET_ZONE)
    return parsed


def utc_text(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def event_datetime(event: Mapping[str, Any]) -> dt.datetime:
    if event.get("effective_at"):
        return parse_datetime(event["effective_at"])
    if event.get("effective_date") and event.get("timing") == "before_close":
        return dt.datetime.combine(dt.date.fromisoformat(str(event["effective_date"])), dt.time(9, 30), MARKET_ZONE)
    raise HistoryError(f"Ledger event {event.get('id', '<unknown>')} has no supported effective time")


def instrument_multiplier(ledger: Mapping[str, Any], instrument_id: str) -> float:
    instruments = ledger.get("instruments")
    if not isinstance(instruments, Mapping) or instrument_id not in instruments:
        raise HistoryError(f"Unknown ledger instrument: {instrument_id}")
    instrument = instruments[instrument_id]
    if not isinstance(instrument, Mapping):
        raise HistoryError(f"Invalid instrument metadata: {instrument_id}")
    multiplier = finite_number(instrument.get("multiplier", 1), f"{instrument_id} multiplier")
    if multiplier <= 0:
        raise HistoryError(f"Invalid non-positive multiplier for {instrument_id}")
    return multiplier


def initial_state(ledger: Mapping[str, Any]) -> dict[str, Any]:
    formation = ledger.get("formation")
    if not isinstance(formation, Mapping):
        raise HistoryError("Ledger formation object is missing")
    positions: dict[str, dict[str, float]] = {}
    raw_positions = formation.get("positions")
    if not isinstance(raw_positions, list):
        raise HistoryError("Ledger formation positions are missing")
    for raw_position in raw_positions:
        if not isinstance(raw_position, Mapping):
            raise HistoryError("Invalid formation position")
        instrument_id = str(raw_position.get("instrument", ""))
        instrument_multiplier(ledger, instrument_id)
        quantity = finite_number(raw_position.get("quantity"), f"{instrument_id} formation quantity")
        basis = finite_number(raw_position.get("basis_price"), f"{instrument_id} formation basis")
        if abs(quantity) < EPSILON or basis < 0:
            raise HistoryError(f"Invalid formation lot for {instrument_id}")
        positions[instrument_id] = {"quantity": quantity, "basis_price": basis}
    return {
        "cash": finite_number(formation.get("cash"), "formation cash"),
        "positions": positions,
    }


def trade_leg_fee(leg: Mapping[str, Any], instrument_id: str) -> float:
    fee = finite_number(leg.get("fees", 0), f"{instrument_id} trade fees")
    if fee < 0:
        raise HistoryError(f"Invalid negative trade fees for {instrument_id}")
    return fee


def apply_trade_leg(state: dict[str, Any], ledger: Mapping[str, Any], leg: Mapping[str, Any]) -> None:
    instrument_id = str(leg.get("instrument", ""))
    multiplier = instrument_multiplier(ledger, instrument_id)
    signed_quantity = finite_number(leg.get("signed_quantity"), f"{instrument_id} trade quantity")
    price = finite_number(leg.get("price"), f"{instrument_id} trade price")
    fee = trade_leg_fee(leg, instrument_id)
    if abs(signed_quantity) < EPSILON or price < 0:
        raise HistoryError(f"Invalid trade leg for {instrument_id}")

    state["cash"] -= signed_quantity * price * multiplier + fee
    old_lot = state["positions"].get(instrument_id)
    old_quantity = finite_number(old_lot.get("quantity"), "old quantity") if old_lot else 0.0
    old_basis = finite_number(old_lot.get("basis_price"), "old basis") if old_lot else price
    new_quantity = old_quantity + signed_quantity

    if abs(new_quantity) < EPSILON:
        state["positions"].pop(instrument_id, None)
        return
    if abs(old_quantity) < EPSILON:
        fee_per_unit = fee / (abs(signed_quantity) * multiplier)
        new_basis = price + (fee_per_unit if signed_quantity > 0 else -fee_per_unit)
    elif old_quantity * new_quantity < 0:
        opening_quantity = abs(new_quantity)
        opening_fee = fee * opening_quantity / abs(signed_quantity)
        fee_per_unit = opening_fee / (opening_quantity * multiplier)
        new_basis = price + (fee_per_unit if new_quantity > 0 else -fee_per_unit)
    elif old_quantity * signed_quantity > 0:
        fee_per_unit = fee / (abs(signed_quantity) * multiplier)
        effective_basis = price + (fee_per_unit if signed_quantity > 0 else -fee_per_unit)
        new_basis = (abs(old_quantity) * old_basis + abs(signed_quantity) * effective_basis) / abs(new_quantity)
    else:
        new_basis = old_basis
    if new_basis < 0:
        raise HistoryError(f"Trade fees exceed opening value for {instrument_id}")
    state["positions"][instrument_id] = {"quantity": new_quantity, "basis_price": new_basis}


def replay_ledger(ledger: Mapping[str, Any], through: dt.datetime | None = None) -> dict[str, Any]:
    state = initial_state(ledger)
    events = ledger.get("events")
    if not isinstance(events, list):
        raise HistoryError("Ledger events are missing")
    ordered = sorted(events, key=lambda event: (event_datetime(event), str(event.get("id", ""))))
    for event in ordered:
        effective = event_datetime(event)
        if through is not None and effective > through:
            continue
        kind = event.get("kind")
        if kind == "cash_adjustment":
            state["cash"] += finite_number(event.get("amount"), f"{event.get('id')} amount")
        elif kind == "trade":
            legs = event.get("legs")
            if not isinstance(legs, list) or not legs:
                raise HistoryError(f"Trade {event.get('id')} has no legs")
            for leg in legs:
                if not isinstance(leg, Mapping):
                    raise HistoryError(f"Trade {event.get('id')} has an invalid leg")
                apply_trade_leg(state, ledger, leg)
        else:
            raise HistoryError(f"Unsupported ledger event kind: {kind}")
    state["cash"] = round(float(state["cash"]), 10)
    return state


def position_quantities(state: Mapping[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    positions = state.get("positions") or {}
    for instrument_id, lot in positions.items():
        quantity = finite_number(lot.get("quantity"), f"{instrument_id} quantity")
        if abs(quantity) >= EPSILON:
            result[str(instrument_id)] = quantity
    return dict(sorted(result.items()))


def validate_ledger(ledger: Mapping[str, Any]) -> None:
    formation = ledger.get("formation")
    if not isinstance(formation, Mapping):
        raise HistoryError("Missing formation")
    instruments = ledger.get("instruments")
    if not isinstance(instruments, Mapping) or not instruments:
        raise HistoryError("Instrument metadata is missing")
    for instrument_id, instrument in instruments.items():
        if not isinstance(instrument, Mapping):
            raise HistoryError(f"Invalid instrument metadata: {instrument_id}")
        for key in ("name", "attribution_group_id", "attribution_group_label", "exposure_group_id", "exposure_group_label"):
            if not str(instrument.get(key, "")).strip():
                raise HistoryError(f"{instrument_id} is missing {key}")
        exposure_group_id = str(instrument["exposure_group_id"])
        if exposure_group_id not in EXPOSURE_CATEGORY_STYLES:
            raise HistoryError(f"{instrument_id} has unsupported exposure group {exposure_group_id}")
    formation_state = initial_state(ledger)
    basis_value = formation_state["cash"]
    for instrument_id, lot in formation_state["positions"].items():
        basis_value += lot["quantity"] * lot["basis_price"] * instrument_multiplier(ledger, instrument_id)
    formation_nav = finite_number(formation.get("nav"), "formation NAV")
    if not math.isclose(basis_value, formation_nav, abs_tol=0.005):
        raise HistoryError(f"Formation basis value {basis_value:.2f} does not reconcile to {formation_nav:.2f}")

    expected = ledger.get("expected_current_snapshot")
    if not isinstance(expected, Mapping):
        raise HistoryError("Expected current snapshot is missing")
    current = replay_ledger(ledger)
    if not math.isclose(current["cash"], finite_number(expected.get("cash"), "expected cash"), abs_tol=0.005):
        raise HistoryError("Ledger cash does not reconcile to its expected current snapshot")
    expected_positions = {str(key): finite_number(value, f"expected {key} quantity") for key, value in (expected.get("positions") or {}).items()}
    if position_quantities(current) != dict(sorted(expected_positions.items())):
        raise HistoryError("Ledger positions do not reconcile to their expected current snapshot")


def assert_current_account_snapshot(ledger: Mapping[str, Any], accounts: Mapping[str, Any]) -> None:
    account_id = str(ledger.get("account_id", ""))
    account_map = accounts.get("accounts")
    account = account_map.get(account_id) if isinstance(account_map, Mapping) else None
    if not isinstance(account, Mapping):
        raise HistoryError(f"Current account snapshot is missing for {account_id}")
    current = replay_ledger(ledger)
    if not math.isclose(current["cash"], finite_number(account.get("cash"), "account cash"), abs_tol=0.005):
        raise HistoryError("Ledger cash does not match data/demo-accounts.json")
    published: dict[str, float] = {}
    for position in account.get("positions") or []:
        instrument_id = str(position.get("instrument", ""))
        published[instrument_id] = published.get(instrument_id, 0.0) + finite_number(position.get("quantity"), f"published {instrument_id} quantity")
    published = {key: value for key, value in published.items() if abs(value) >= EPSILON}
    if position_quantities(current) != dict(sorted(published.items())):
        raise HistoryError("Ledger positions do not match data/demo-accounts.json")


def parse_yahoo_daily_result(
    result: Mapping[str, Any],
    *,
    symbol: str,
    start: dt.date,
    end: dt.date,
    adjusted: bool = False,
    utc_dates: bool = False,
) -> dict[dt.date, float]:
    """Return positive daily prices from one Yahoo chart result.

    Portfolio marks intentionally use raw closes and New York session dates.
    SPY and GLD comparisons use adjusted closes, while BTC uses raw closes and
    the UTC date attached to Yahoo's continuously traded daily candle.
    """
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    quote_rows = indicators.get("quote") or []
    closes = quote_rows[0].get("close") if quote_rows else []
    if adjusted:
        adjusted_rows = indicators.get("adjclose") or []
        prices = adjusted_rows[0].get("adjclose") if adjusted_rows else []
        price_label = "adjusted close"
    else:
        prices = closes
        price_label = "close"
    observations: dict[dt.date, float] = {}
    for timestamp, raw_price in zip(timestamps, prices or []):
        if raw_price is None:
            continue
        price = finite_number(raw_price, f"{symbol} Yahoo {price_label}")
        if price <= 0:
            continue
        observed = dt.datetime.fromtimestamp(int(timestamp), dt.timezone.utc)
        observed_date = observed.date() if utc_dates else observed.astimezone(MARKET_ZONE).date()
        if start <= observed_date <= end:
            observations[observed_date] = price
    if not observations:
        raise HistoryError(f"Yahoo returned no usable daily {price_label}s for {symbol}")
    return observations


def fetch_yahoo_daily_closes(
    symbol: str,
    start: dt.date,
    end: dt.date,
    timeout: int = 20,
    *,
    adjusted: bool = False,
    utc_dates: bool = False,
) -> dict[dt.date, float]:
    period_start = int(dt.datetime.combine(start - dt.timedelta(days=5), dt.time(), dt.timezone.utc).timestamp())
    period_end = int(dt.datetime.combine(end + dt.timedelta(days=2), dt.time(), dt.timezone.utc).timestamp())
    parameters = urllib.parse.urlencode({
        "period1": period_start,
        "period2": period_end,
        "interval": "1d",
        "events": "div,splits",
        "includeAdjustedClose": "true" if adjusted else "false",
    })
    last_error: Exception | None = None
    for host in YAHOO_HOSTS:
        url = YAHOO_ENDPOINT.format(host=host, symbol=urllib.parse.quote(symbol, safe="")) + "?" + parameters
        request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
            chart = payload.get("chart") or {}
            if chart.get("error"):
                raise HistoryError(f"Yahoo returned an error for {symbol}")
            results = chart.get("result") or []
            if not results:
                raise HistoryError(f"Yahoo returned no result for {symbol}")
            result = results[0]
            return parse_yahoo_daily_result(
                result,
                symbol=symbol,
                start=start,
                end=end,
                adjusted=adjusted,
                utc_dates=utc_dates,
            )
        except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError, HistoryError, ValueError) as error:
            last_error = error
    raise HistoryError(f"Yahoo daily history unavailable for {symbol}") from last_error


def required_symbols(ledger: Mapping[str, Any]) -> list[str]:
    symbols: set[str] = set()
    for instrument_id, raw_instrument in (ledger.get("instruments") or {}).items():
        if not isinstance(raw_instrument, Mapping):
            raise HistoryError(f"Invalid instrument metadata: {instrument_id}")
        kind = raw_instrument.get("kind")
        if kind == "equity":
            symbols.add(str(raw_instrument.get("symbol") or instrument_id))
        elif kind == "option_model":
            symbols.add(str(raw_instrument.get("underlying", "")))
        else:
            raise HistoryError(f"Unsupported instrument kind for {instrument_id}: {kind}")
    symbols.discard("")
    if "SPY" not in symbols:
        raise HistoryError("SPY is required as the trading-session calendar anchor")
    return sorted(symbols)


def seed_marks(ledger: Mapping[str, Any]) -> dict[str, tuple[float, dt.date]]:
    formation = ledger["formation"]
    formation_date = parse_datetime(formation["as_of"]).astimezone(MARKET_ZONE).date()
    seeds: dict[str, tuple[float, dt.date]] = {}
    instruments = ledger["instruments"]
    for position in formation["positions"]:
        instrument_id = str(position["instrument"])
        instrument = instruments[instrument_id]
        if instrument["kind"] == "equity":
            seeds[str(instrument["symbol"])] = (finite_number(position["basis_price"], f"{instrument_id} seed"), formation_date)
        else:
            model_id = str(instrument["model_spec_id"])
            spec = option_models.OPTION_MODEL_SPECS.get(model_id)
            if not spec:
                raise HistoryError(f"Missing option model specification: {model_id}")
            symbol = str(spec["underlying"])
            candidate = (finite_number(spec["opening_spot"], f"{symbol} opening spot"), formation_date)
            if symbol in seeds and not math.isclose(seeds[symbol][0], candidate[0], rel_tol=1e-9):
                raise HistoryError(f"Conflicting formation seeds for {symbol}")
            seeds[symbol] = candidate
    return seeds


def normalize_observations(raw: Mapping[dt.date | str, float], start: dt.date, end: dt.date, symbol: str) -> dict[dt.date, float]:
    result: dict[dt.date, float] = {}
    for raw_date, raw_price in raw.items():
        try:
            observed_date = raw_date if isinstance(raw_date, dt.date) else dt.date.fromisoformat(str(raw_date))
            price = finite_number(raw_price, f"{symbol} close on {observed_date}")
        except (ValueError, HistoryError):
            continue
        if start <= observed_date <= end and price > 0:
            # Persisted fallback marks use six decimals. Quantizing fetched
            # observations here makes a fallback-only rebuild cent-for-cent
            # deterministic instead of letting binary float tails alter a
            # rounded category value on the next run.
            result[observed_date] = round(price, 6)
    return result


def fallback_mark_history(fallback: Mapping[str, Any] | None, start: dt.date, end: dt.date) -> dict[str, dict[dt.date, float]]:
    result: dict[str, dict[dt.date, float]] = {}
    raw_history = fallback.get("mark_history") if isinstance(fallback, Mapping) else None
    if not isinstance(raw_history, Mapping):
        return result
    for symbol, rows in raw_history.items():
        observations: dict[dt.date, float] = {}
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                try:
                    observed_date = dt.date.fromisoformat(str(row.get("date")))
                    price = finite_number(row.get("price"), f"fallback {symbol} price")
                except (ValueError, HistoryError):
                    continue
                if start <= observed_date <= end and price > 0:
                    observations[observed_date] = price
        if observations:
            result[str(symbol)] = observations
    return result


def fallback_comparison(
    fallback: Mapping[str, Any] | None,
    account_id: str,
    spec: Mapping[str, Any],
    start: dt.date,
    end: dt.date,
    *,
    last_source_date: dt.date | None = None,
) -> dict[str, Any] | None:
    """Return one validated comparison series from a prior snapshot."""
    comparison_id = str(spec["id"])
    if not isinstance(fallback, Mapping):
        return None
    accounts = fallback.get("accounts")
    account = accounts.get(account_id) if isinstance(accounts, Mapping) else None
    comparisons = account.get("comparisons") if isinstance(account, Mapping) else None
    if not isinstance(comparisons, list):
        return None
    for candidate in comparisons:
        if not isinstance(candidate, Mapping) or str(candidate.get("id")) != comparison_id:
            continue
        if (
            str(candidate.get("symbol")) != str(spec["symbol"])
            or str(candidate.get("price_basis")) != str(spec["price_basis"])
            or str(candidate.get("units")) != "normalized_account_value_usd"
        ):
            return None
        try:
            baseline_date = dt.date.fromisoformat(str(candidate.get("baseline_date")))
            baseline_price = finite_number(candidate.get("baseline_price"), f"fallback {comparison_id} baseline")
        except (ValueError, HistoryError):
            return None
        if baseline_date != start or baseline_price <= 0:
            return None
        points: list[dict[str, Any]] = []
        for point in candidate.get("points") or []:
            if not isinstance(point, Mapping):
                continue
            try:
                day = dt.date.fromisoformat(str(point.get("date")))
                source_date = dt.date.fromisoformat(str(point.get("source_date")))
                value = finite_number(point.get("value"), f"fallback {comparison_id} value")
                market_price = finite_number(point.get("market_price"), f"fallback {comparison_id} price")
            except (ValueError, HistoryError):
                continue
            if (
                start <= day <= end
                and start <= source_date <= day
                and (last_source_date is None or source_date <= last_source_date)
                and value >= 0
                and market_price > 0
            ):
                points.append({
                    "date": day.isoformat(),
                    "value": round(value, 2),
                    "market_price": round(market_price, 6),
                    "kind": str(point.get("kind") or "carry_forward"),
                    "source_date": source_date.isoformat(),
                    "quality": str(point.get("quality") or "fallback"),
                })
        if not points:
            return None
        result = dict(candidate)
        result["baseline_price"] = baseline_price
        result["points"] = sorted(points, key=lambda point: point["date"])
        return result
    return None


def comparison_observations(series: Mapping[str, Any] | None) -> dict[dt.date, float]:
    """Recover unique source prices from a previously generated comparison."""
    observations: dict[dt.date, float] = {}
    if not isinstance(series, Mapping):
        return observations
    for point in series.get("points") or []:
        if not isinstance(point, Mapping):
            continue
        try:
            source_date = dt.date.fromisoformat(str(point.get("source_date")))
            market_price = finite_number(point.get("market_price"), "fallback benchmark market price")
        except (ValueError, HistoryError):
            continue
        if market_price > 0:
            observations[source_date] = market_price
    return observations


def unavailable_comparison(spec: Mapping[str, Any], formation_date: dt.date, opening_nav: float) -> dict[str, Any]:
    return {
        "id": str(spec["id"]),
        "label": str(spec["label"]),
        "symbol": str(spec["symbol"]),
        "price_basis": str(spec["price_basis"]),
        "baseline_date": formation_date.isoformat(),
        "baseline_price": None,
        "baseline_value": round(opening_nav, 2),
        "units": "normalized_account_value_usd",
        "status": "unavailable",
        "source": BENCHMARK_SOURCE,
        "points": [],
    }


def build_comparison(
    spec: Mapping[str, Any],
    *,
    formation_date: dt.date,
    calendar_dates: list[dt.date],
    opening_nav: float,
    market_sessions: set[dt.date],
    fetched: Mapping[dt.date, float],
    fallback_series: Mapping[str, Any] | None,
    fetch_failed: bool,
) -> dict[str, Any]:
    """Normalize one public benchmark to the account's formation value."""
    comparison_id = str(spec["id"])
    observations = comparison_observations(fallback_series)
    observations.update(normalize_observations(fetched, formation_date, calendar_dates[-1], str(spec["symbol"])))
    baseline_price = observations.get(formation_date)
    if baseline_price is None and isinstance(fallback_series, Mapping):
        try:
            fallback_date = dt.date.fromisoformat(str(fallback_series.get("baseline_date")))
            fallback_price = finite_number(fallback_series.get("baseline_price"), f"{comparison_id} fallback baseline")
            if fallback_date == formation_date and fallback_price > 0:
                baseline_price = fallback_price
                observations[formation_date] = fallback_price
        except (ValueError, HistoryError):
            pass
    if baseline_price is None or baseline_price <= 0:
        return unavailable_comparison(spec, formation_date, opening_nav)

    points: list[dict[str, Any]] = []
    degraded = fetch_failed
    for day in calendar_dates:
        eligible = [observed for observed in observations if observed <= day]
        if not eligible:
            return unavailable_comparison(spec, formation_date, opening_nav)
        source_date = max(eligible)
        market_price = observations[source_date]
        if day == formation_date:
            kind = "formation_baseline"
            quality = "baseline"
            value = opening_nav
        elif source_date == day:
            kind = "daily_close"
            quality = "retained" if fetch_failed else "complete"
            value = opening_nav * market_price / baseline_price
        else:
            kind = "carry_forward"
            expected_observation = comparison_id == "btc-usd" or day in market_sessions
            if fetch_failed or expected_observation:
                quality = "stale_fallback"
                degraded = True
            else:
                quality = "carry_forward"
            value = opening_nav * market_price / baseline_price
        points.append({
            "date": day.isoformat(),
            "value": round(value, 2),
            "market_price": round(market_price, 6),
            "kind": kind,
            "source_date": source_date.isoformat(),
            "quality": quality,
        })

    return {
        "id": comparison_id,
        "label": str(spec["label"]),
        "symbol": str(spec["symbol"]),
        "price_basis": str(spec["price_basis"]),
        "baseline_date": formation_date.isoformat(),
        "baseline_price": round(baseline_price, 6),
        "baseline_value": round(opening_nav, 2),
        "units": "normalized_account_value_usd",
        "status": "degraded" if degraded else "ready",
        "source": BENCHMARK_SOURCE,
        "points": points,
    }


def completed_cutoff(as_of: dt.datetime) -> dt.date:
    local = as_of.astimezone(MARKET_ZONE)
    if local.timetz().replace(tzinfo=None) >= MARKET_CLOSE:
        return local.date()
    return local.date() - dt.timedelta(days=1)


def active_symbols(ledger: Mapping[str, Any], state: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    instruments = ledger["instruments"]
    for instrument_id in state["positions"]:
        instrument = instruments[instrument_id]
        if instrument["kind"] == "equity":
            result.add(str(instrument["symbol"]))
        else:
            result.add(str(instrument["underlying"]))
    return result


def instrument_mark(
    ledger: Mapping[str, Any],
    instrument_id: str,
    marks: Mapping[str, float],
    valuation_time: dt.datetime,
    volatility_cache: dict[str, float] | None = None,
) -> float:
    instrument = ledger["instruments"][instrument_id]
    if instrument["kind"] == "equity":
        mark = finite_number(marks[str(instrument["symbol"])], f"{instrument_id} mark")
    else:
        model_id = str(instrument["model_spec_id"])
        spec = option_models.OPTION_MODEL_SPECS.get(model_id)
        if not spec:
            raise HistoryError(f"Missing option model specification: {model_id}")
        underlier = str(instrument["underlying"])
        cache = volatility_cache if volatility_cache is not None else {}
        volatility = cache.setdefault(model_id, option_models.calibrated_volatility(spec))
        mark = option_models.modeled_strategy_value(
            spec,
            finite_number(marks[underlier], f"{underlier} mark"),
            valuation_time.astimezone(dt.timezone.utc),
            volatility,
        )
    if not math.isfinite(mark) or mark < 0:
        raise HistoryError(f"Invalid mark for {instrument_id}")
    return mark


def position_market_values(
    ledger: Mapping[str, Any],
    state: Mapping[str, Any],
    marks: Mapping[str, float],
    valuation_time: dt.datetime,
) -> dict[str, float]:
    values: dict[str, float] = {}
    volatility_cache: dict[str, float] = {}
    for instrument_id, lot in state["positions"].items():
        quantity = finite_number(lot["quantity"], f"{instrument_id} quantity")
        multiplier = instrument_multiplier(ledger, instrument_id)
        mark = instrument_mark(ledger, instrument_id, marks, valuation_time, volatility_cache)
        values[instrument_id] = quantity * multiplier * mark
    return values


def position_value(
    ledger: Mapping[str, Any],
    state: Mapping[str, Any],
    marks: Mapping[str, float],
    valuation_time: dt.datetime,
) -> float:
    return sum(position_market_values(ledger, state, marks, valuation_time).values())


def realized_trade_records(ledger: Mapping[str, Any], through: dt.datetime) -> list[dict[str, Any]]:
    """Return average-cost realizations grouped by ledger event and instrument."""
    state = initial_state(ledger)
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    events = sorted(ledger.get("events") or [], key=lambda event: (event_datetime(event), str(event.get("id", ""))))
    for event in events:
        effective = event_datetime(event)
        if effective > through:
            continue
        if event.get("kind") == "cash_adjustment":
            state["cash"] += finite_number(event.get("amount"), f"{event.get('id')} amount")
            continue
        if event.get("kind") != "trade":
            raise HistoryError(f"Unsupported ledger event kind: {event.get('kind')}")
        event_id = str(event.get("id", ""))
        for leg in event.get("legs") or []:
            if not isinstance(leg, Mapping):
                raise HistoryError(f"Trade {event_id} has an invalid leg")
            instrument_id = str(leg.get("instrument", ""))
            signed_quantity = finite_number(leg.get("signed_quantity"), f"{instrument_id} trade quantity")
            price = finite_number(leg.get("price"), f"{instrument_id} trade price")
            multiplier = instrument_multiplier(ledger, instrument_id)
            fee = trade_leg_fee(leg, instrument_id)
            old_lot = state["positions"].get(instrument_id)
            old_quantity = finite_number(old_lot.get("quantity"), f"{instrument_id} prior quantity") if old_lot else 0.0
            old_basis = finite_number(old_lot.get("basis_price"), f"{instrument_id} prior basis") if old_lot else price
            closed_quantity = min(abs(old_quantity), abs(signed_quantity)) if old_quantity * signed_quantity < 0 else 0.0
            if closed_quantity > EPSILON:
                closing_fee = fee * closed_quantity / abs(signed_quantity)
                closing_value = closed_quantity * price * multiplier
                closed_basis = closed_quantity * old_basis * multiplier
                direction = 1.0 if old_quantity > 0 else -1.0
                realized_pnl = direction * (closing_value - closed_basis) - closing_fee
                key = (event_id, instrument_id)
                instrument = ledger["instruments"][instrument_id]
                record = grouped.setdefault(key, {
                    "id": event_id + "::" + instrument_id,
                    "event_id": event_id,
                    "date": effective.astimezone(MARKET_ZONE).date().isoformat(),
                    "instrument_id": instrument_id,
                    "label": str(instrument["name"]),
                    "attribution_group_id": str(instrument["attribution_group_id"]),
                    "attribution_group_label": str(instrument["attribution_group_label"]),
                    "closed_sides": set(),
                    "closed_quantity": 0.0,
                    "multiplier": multiplier,
                    "fill_count": 0,
                    "closing_value": 0.0,
                    "closed_basis": 0.0,
                    "fees": 0.0,
                    "realized_pnl": 0.0,
                })
                record["closed_sides"].add("long" if old_quantity > 0 else "short")
                record["closed_quantity"] += closed_quantity
                record["fill_count"] += 1
                record["closing_value"] += closing_value
                record["closed_basis"] += abs(closed_basis)
                record["fees"] += closing_fee
                record["realized_pnl"] += realized_pnl
            apply_trade_leg(state, ledger, leg)

    records: list[dict[str, Any]] = []
    for record in grouped.values():
        quantity = float(record["closed_quantity"])
        multiplier = float(record["multiplier"])
        closed_basis = float(record["closed_basis"])
        sides = sorted(record.pop("closed_sides"))
        record["closed_side"] = sides[0] if len(sides) == 1 else "mixed"
        record["average_exit_price"] = round(float(record["closing_value"]) / (quantity * multiplier), 6)
        record["closed_quantity"] = round(quantity, 8)
        record["closing_value"] = round(float(record["closing_value"]), 2)
        record["closed_basis"] = round(closed_basis, 2)
        record["fees"] = round(float(record["fees"]), 2)
        record["realized_pnl"] = round(float(record["realized_pnl"]), 2)
        record["return_pct"] = round(float(record["realized_pnl"]) / closed_basis * 100, 4) if closed_basis > EPSILON else None
        records.append(record)
    return sorted(records, key=lambda record: (-float(record["realized_pnl"]), str(record["date"]), str(record["id"])))


def performance_cash_adjustments(
    ledger: Mapping[str, Any],
    through: dt.datetime,
) -> tuple[dict[str, float], dict[str, Any], float]:
    instrument_income: dict[str, float] = {}
    unattributed_events: list[dict[str, Any]] = []
    external_flows = 0.0
    for event in sorted(ledger.get("events") or [], key=lambda item: (event_datetime(item), str(item.get("id", "")))):
        effective = event_datetime(event)
        if effective > through or event.get("kind") != "cash_adjustment":
            continue
        amount = finite_number(event.get("amount"), f"{event.get('id')} amount")
        classification = str(event.get("classification") or "unattributed_strategy_pnl")
        if classification in EXTERNAL_FLOW_CLASSIFICATIONS:
            external_flows += amount
            continue
        instrument_id = str(event.get("instrument") or "")
        if instrument_id:
            instrument_multiplier(ledger, instrument_id)
            instrument_income[instrument_id] = instrument_income.get(instrument_id, 0.0) + amount
        else:
            unattributed_events.append({
                "id": str(event.get("id", "")),
                "date": effective.astimezone(MARKET_ZONE).date().isoformat(),
                "classification": classification,
                "amount": round(amount, 2),
                "note": str(event.get("note") or ""),
            })
    return instrument_income, {
        "total": round(sum(float(event["amount"]) for event in unattributed_events), 2),
        "events": unattributed_events,
    }, round(external_flows, 2)


def exposure_categories(ledger: Mapping[str, Any], *, include_financing: bool = False) -> list[dict[str, str]]:
    requested = {"cash-cash-equivalents"}
    for instrument in (ledger.get("instruments") or {}).values():
        requested.add(str(instrument.get("exposure_group_id") or "other"))
    if include_financing:
        requested.add("financing")
    categories: list[dict[str, str]] = []
    for category_id, (default_label, color) in EXPOSURE_CATEGORY_STYLES.items():
        if category_id not in requested:
            continue
        labels = {
            str(instrument.get("exposure_group_label"))
            for instrument in (ledger.get("instruments") or {}).values()
            if str(instrument.get("exposure_group_id")) == category_id and instrument.get("exposure_group_label")
        }
        categories.append({
            "id": category_id,
            "label": sorted(labels)[0] if labels else default_label,
            "color": color,
        })
    return categories


def exposure_point(
    ledger: Mapping[str, Any],
    state: Mapping[str, Any],
    market_values: Mapping[str, float],
    categories: list[dict[str, str]],
    *,
    day: dt.date,
    kind: str,
    source_date: dt.date,
    quality: str,
) -> dict[str, Any]:
    values = {category["id"]: 0.0 for category in categories}
    cash = finite_number(state.get("cash"), "exposure cash")
    if cash >= 0:
        values["cash-cash-equivalents"] = values.get("cash-cash-equivalents", 0.0) + cash
    else:
        values["financing"] = values.get("financing", 0.0) + abs(cash)
    for instrument_id, raw_market_value in market_values.items():
        market_value = finite_number(raw_market_value, f"{instrument_id} market value")
        instrument = ledger["instruments"][instrument_id]
        if instrument.get("cash_equivalent") is True:
            category_id = "cash-cash-equivalents" if market_value >= 0 else "financing"
        else:
            category_id = str(instrument.get("exposure_group_id") or "other")
        values[category_id] = values.get(category_id, 0.0) + abs(market_value)
    gross_exposure = sum(values.values())
    if gross_exposure <= EPSILON:
        raise HistoryError(f"No positive gross exposure on {day}")
    rounded_percentages = {
        category_id: round(value / gross_exposure * 100, 6)
        for category_id, value in values.items()
    }
    percentage_residual = round(100.0 - sum(rounded_percentages.values()), 6)
    if abs(percentage_residual) > 0:
        largest = max(values, key=values.get)
        rounded_percentages[largest] = round(rounded_percentages[largest] + percentage_residual, 6)
    return {
        "date": day.isoformat(),
        "kind": kind,
        "source_date": source_date.isoformat(),
        "quality": quality,
        "gross_exposure": round(gross_exposure, 2),
        "values": {
            category["id"]: {
                "value": round(values.get(category["id"], 0.0), 2),
                "percent": rounded_percentages.get(category["id"], 0.0),
            }
            for category in categories
        },
    }


def build_analytics(
    ledger: Mapping[str, Any],
    *,
    through: dt.datetime,
    state: Mapping[str, Any],
    market_values: Mapping[str, float],
    latest_nav: float,
    exposure_history: Mapping[str, Any],
) -> dict[str, Any]:
    realized_trades = realized_trade_records(ledger, through)
    instrument_income, unattributed_pnl, external_flows = performance_cash_adjustments(ledger, through)
    instrument_stats: dict[str, dict[str, float]] = {}
    for trade in realized_trades:
        instrument_id = str(trade["instrument_id"])
        stats = instrument_stats.setdefault(instrument_id, {"realized_pnl": 0.0, "disposed_basis": 0.0})
        stats["realized_pnl"] += float(trade["realized_pnl"])
        stats["disposed_basis"] += float(trade["closed_basis"])
    for instrument_id, amount in instrument_income.items():
        stats = instrument_stats.setdefault(instrument_id, {"realized_pnl": 0.0, "disposed_basis": 0.0})
        stats["income"] = stats.get("income", 0.0) + amount
    for instrument_id, lot in (state.get("positions") or {}).items():
        stats = instrument_stats.setdefault(instrument_id, {"realized_pnl": 0.0, "disposed_basis": 0.0})
        quantity = finite_number(lot.get("quantity"), f"{instrument_id} analytics quantity")
        basis = finite_number(lot.get("basis_price"), f"{instrument_id} analytics basis")
        multiplier = instrument_multiplier(ledger, instrument_id)
        market_value = finite_number(market_values[instrument_id], f"{instrument_id} analytics market value")
        stats["market_value"] = market_value
        stats["current_basis"] = abs(quantity * basis * multiplier)
        stats["unrealized_pnl"] = market_value - quantity * basis * multiplier

    grouped: dict[str, dict[str, Any]] = {}
    for instrument_id, stats in instrument_stats.items():
        instrument = ledger["instruments"][instrument_id]
        group_id = str(instrument["attribution_group_id"])
        group = grouped.setdefault(group_id, {
            "id": group_id,
            "label": str(instrument["attribution_group_label"]),
            "instrument_ids": [],
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "income": 0.0,
            "market_value": 0.0,
            "tracked_basis": 0.0,
        })
        group["instrument_ids"].append(instrument_id)
        group["realized_pnl"] += stats.get("realized_pnl", 0.0)
        group["unrealized_pnl"] += stats.get("unrealized_pnl", 0.0)
        group["income"] += stats.get("income", 0.0)
        group["market_value"] += stats.get("market_value", 0.0)
        group["tracked_basis"] += stats.get("disposed_basis", 0.0) + stats.get("current_basis", 0.0)

    opening_nav = finite_number(ledger["formation"]["nav"], "analytics opening NAV")
    contributors: list[dict[str, Any]] = []
    attributed_pnl_exact = 0.0
    for group in grouped.values():
        total_pnl = float(group["realized_pnl"]) + float(group["unrealized_pnl"]) + float(group["income"])
        attributed_pnl_exact += total_pnl
        tracked_basis = float(group["tracked_basis"])
        contributors.append({
            "id": str(group["id"]),
            "label": str(group["label"]),
            "instrument_ids": sorted(group["instrument_ids"]),
            "realized_pnl": round(float(group["realized_pnl"]), 2),
            "unrealized_pnl": round(float(group["unrealized_pnl"]), 2),
            "income": round(float(group["income"]), 2),
            "total_pnl": round(total_pnl, 2),
            "tracked_basis": round(tracked_basis, 2),
            "return_pct": round(total_pnl / tracked_basis * 100, 4) if tracked_basis > EPSILON else None,
            "portfolio_contribution_pct": round(total_pnl / opening_nav * 100, 4) if opening_nav > EPSILON else None,
            "market_value": round(float(group["market_value"]), 2),
        })
    contributors.sort(key=lambda group: (-float(group["total_pnl"]), str(group["label"]), str(group["id"])))

    unattributed_total = float(unattributed_pnl["total"])
    nav_change_exact = latest_nav - opening_nav
    explained_change_exact = attributed_pnl_exact + unattributed_total
    residual_exact = nav_change_exact - external_flows - explained_change_exact
    if abs(residual_exact) > 0.005:
        raise HistoryError(f"Analytics contribution residual is {residual_exact:.6f}")
    attributed_pnl = round(attributed_pnl_exact, 2)
    nav_change = round(nav_change_exact, 2)
    explained_change = round(explained_change_exact, 2)
    residual = 0.0 if abs(residual_exact) <= 0.005 else round(residual_exact, 2)
    return {
        "schema_version": 1,
        "as_of": through.astimezone(MARKET_ZONE).date().isoformat(),
        "methodology": {
            "realized_pnl": "Average-cost closed-lot P&L net of reported fees; fills are grouped by ledger event and instrument.",
            "contribution": "Realized P&L plus tagged income plus latest completed-night unrealized P&L. Return percentages use cumulative tracked basis; portfolio contribution uses formation NAV.",
            "exposure": "Absolute marked market value plus absolute cash, grouped by asset class. Long SGOV and positive cash are Cash & Cash Equivalents; this is not delta or notional exposure.",
        },
        "realized_trades": realized_trades,
        "contributors": contributors,
        "unattributed_pnl": unattributed_pnl,
        "reconciliation": {
            "opening_nav": round(opening_nav, 2),
            "latest_nav": round(latest_nav, 2),
            "nav_change": nav_change,
            "external_flows": external_flows,
            "attributed_pnl": attributed_pnl,
            "unattributed_pnl": round(unattributed_total, 2),
            "explained_change": explained_change,
            "residual": residual,
        },
        "exposure_history": dict(exposure_history),
    }


def point_from_value(
    day: dt.date,
    *,
    kind: str,
    value: float,
    cash: float,
    positions_value: float,
    source_date: dt.date,
    quality: str,
    forward_filled_symbols: list[str],
    position_count: int,
) -> dict[str, Any]:
    return {
        "date": day.isoformat(),
        "value": round(value, 2),
        "cash": round(cash, 2),
        "positions_value": round(positions_value, 2),
        "kind": kind,
        "source_date": source_date.isoformat(),
        "quality": quality,
        "forward_filled_symbols": sorted(forward_filled_symbols),
        "position_count": position_count,
    }


def build_history(
    ledger: Mapping[str, Any],
    *,
    as_of: dt.datetime,
    fetcher: DailyFetcher = fetch_yahoo_daily_closes,
    benchmark_fetcher: DailyFetcher | None = None,
    fallback: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fetch, replay, and value a deterministic calendar-daily history."""
    validate_ledger(ledger)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=MARKET_ZONE)
    formation_date = parse_datetime(ledger["formation"]["as_of"]).astimezone(MARKET_ZONE).date()
    cutoff = completed_cutoff(as_of)
    if cutoff < formation_date:
        raise HistoryError("The requested as-of precedes portfolio formation")
    fetch_start = formation_date - dt.timedelta(days=7)
    symbols = required_symbols(ledger)
    fallback_series = fallback_mark_history(fallback, fetch_start, cutoff)
    observations: dict[str, dict[dt.date, float]] = {symbol: dict(fallback_series.get(symbol, {})) for symbol in symbols}
    observation_source: dict[str, dict[dt.date, str]] = {
        symbol: {day: "fallback" for day in observations[symbol]} for symbol in symbols
    }
    failures: list[str] = []

    for symbol in symbols:
        try:
            fetched = normalize_observations(fetcher(symbol, fetch_start, cutoff), fetch_start, cutoff, symbol)
            if not fetched:
                raise HistoryError("no usable observations")
            observations[symbol].update(fetched)
            observation_source[symbol].update({day: "yahoo" for day in fetched})
        except Exception as error:  # The scheduled job must retain valid prior public data.
            failures.append(f"{symbol}: {type(error).__name__}")

    sessions = sorted(day for day in observations.get("SPY", {}) if formation_date <= day <= cutoff)
    if fallback and isinstance(fallback.get("market_sessions"), list):
        for raw_day in fallback["market_sessions"]:
            try:
                day = dt.date.fromisoformat(str(raw_day))
            except ValueError:
                continue
            if formation_date <= day <= cutoff and day not in sessions:
                sessions.append(day)
        sessions.sort()
    calculated_sessions = [day for day in sessions if day >= FIRST_CALCULATED_SESSION]
    if cutoff >= FIRST_CALCULATED_SESSION and not calculated_sessions:
        raise HistoryError("No completed SPY session is available on or after July 20")
    last_session = calculated_sessions[-1] if calculated_sessions else formation_date
    history_end = cutoff

    seeds = seed_marks(ledger)
    missing_seeds = [symbol for symbol in symbols if symbol not in seeds]
    if missing_seeds:
        raise HistoryError("Missing non-zero formation mark seeds: " + ", ".join(missing_seeds))

    points: list[dict[str, Any]] = []
    raw_exposure_categories = exposure_categories(ledger, include_financing=True)
    exposure_points: list[dict[str, Any]] = []
    formation_nav = finite_number(ledger["formation"]["nav"], "formation NAV")
    formation_cash = finite_number(ledger["formation"]["cash"], "formation cash")
    formation_positions_value = formation_nav - formation_cash
    formation_state = initial_state(ledger)
    formation_market_values = {
        instrument_id: finite_number(lot["quantity"], f"{instrument_id} formation quantity")
        * instrument_multiplier(ledger, instrument_id)
        * finite_number(lot["basis_price"], f"{instrument_id} formation basis")
        for instrument_id, lot in formation_state["positions"].items()
    }
    points.append(point_from_value(
        formation_date,
        kind="formation_baseline",
        value=formation_nav,
        cash=formation_cash,
        positions_value=formation_positions_value,
        source_date=formation_date,
        quality="baseline",
        forward_filled_symbols=[],
        position_count=len(formation_state["positions"]),
    ))
    exposure_points.append(exposure_point(
        ledger,
        formation_state,
        formation_market_values,
        raw_exposure_categories,
        day=formation_date,
        kind="formation_baseline",
        source_date=formation_date,
        quality="baseline",
    ))
    session_set = set(calculated_sessions)
    last_source_date = formation_date
    last_point = points[0]
    last_exposure_point = exposure_points[0]
    analytics_state = formation_state
    analytics_market_values = formation_market_values
    analytics_nav = formation_nav
    day = formation_date + dt.timedelta(days=1)
    while day <= history_end:
        if day not in session_set:
            carried = copy.deepcopy(last_point)
            carried.update({
                "date": day.isoformat(),
                "kind": "carry_forward",
                "source_date": last_source_date.isoformat(),
                "quality": "carry_forward",
                "forward_filled_symbols": [],
            })
            points.append(carried)
            last_point = carried
            carried_exposure = copy.deepcopy(last_exposure_point)
            carried_exposure.update({
                "date": day.isoformat(),
                "kind": "carry_forward",
                "source_date": last_source_date.isoformat(),
                "quality": "carry_forward",
            })
            exposure_points.append(carried_exposure)
            last_exposure_point = carried_exposure
            day += dt.timedelta(days=1)
            continue

        close_time = dt.datetime.combine(day, MARKET_CLOSE, MARKET_ZONE)
        state = replay_ledger(ledger, close_time)
        marks: dict[str, float] = {}
        forward_filled: list[str] = []
        for symbol in sorted(active_symbols(ledger, state)):
            eligible = [observed for observed in observations.get(symbol, {}) if observed <= day]
            if eligible:
                mark_date = max(eligible)
                marks[symbol] = observations[symbol][mark_date]
            else:
                marks[symbol], mark_date = seeds[symbol]
            if mark_date != day:
                forward_filled.append(symbol)
            if not math.isfinite(marks[symbol]) or marks[symbol] <= 0:
                raise HistoryError(f"No positive mark can cover {symbol} on {day}")
        market_values = position_market_values(ledger, state, marks, close_time)
        positions_total = sum(market_values.values())
        nav = state["cash"] + positions_total
        if not math.isfinite(nav):
            raise HistoryError(f"Non-finite NAV on {day}")
        point = point_from_value(
            day,
            kind="session_close",
            value=nav,
            cash=state["cash"],
            positions_value=positions_total,
            source_date=day,
            quality="degraded" if forward_filled else "complete",
            forward_filled_symbols=forward_filled,
            position_count=len(state["positions"]),
        )
        points.append(point)
        current_exposure = exposure_point(
            ledger,
            state,
            market_values,
            raw_exposure_categories,
            day=day,
            kind="session_close",
            source_date=day,
            quality=point["quality"],
        )
        exposure_points.append(current_exposure)
        last_point = point
        last_exposure_point = current_exposure
        last_source_date = day
        analytics_state = state
        analytics_market_values = market_values
        analytics_nav = nav
        day += dt.timedelta(days=1)

    expected_dates = [formation_date + dt.timedelta(days=index) for index in range((history_end - formation_date).days + 1)]
    if [point["date"] for point in points] != [day.isoformat() for day in expected_dates]:
        raise HistoryError("History points are not unique, sorted, and calendar-contiguous")
    if [point["date"] for point in exposure_points] != [day.isoformat() for day in expected_dates]:
        raise HistoryError("Exposure-history points are not unique, sorted, and calendar-contiguous")

    used_exposure_categories = {
        category["id"]
        for category in raw_exposure_categories
        if any(float(point["values"][category["id"]]["value"]) > EPSILON for point in exposure_points)
    }
    final_exposure_categories = [
        category for category in raw_exposure_categories if category["id"] in used_exposure_categories
    ]
    for exposure in exposure_points:
        exposure["values"] = {
            category["id"]: exposure["values"][category["id"]]
            for category in final_exposure_categories
        }
    exposure_history = {
        "basis": "gross_marked_value",
        "units": "percent_of_gross_marked_value",
        "categories": final_exposure_categories,
        "points": exposure_points,
    }

    benchmark_fetch = benchmark_fetcher or fetcher
    benchmark_failures: list[str] = []
    comparisons: list[dict[str, Any]] = []
    account_id = str(ledger["account_id"])
    for spec in BENCHMARK_SPECS:
        comparison_id = str(spec["id"])
        benchmark_end = history_end
        if comparison_id == "btc-usd":
            # Yahoo can expose the still-open UTC daily candle. Never record it
            # as a completed nightly close when this workflow runs from a push.
            last_closed_utc_candle = as_of.astimezone(dt.timezone.utc).date() - dt.timedelta(days=1)
            benchmark_end = min(benchmark_end, last_closed_utc_candle)
        retained = fallback_comparison(
            fallback,
            account_id,
            spec,
            formation_date,
            history_end,
            last_source_date=benchmark_end,
        )
        fetched_benchmark: dict[dt.date, float] = {}
        fetch_failed = False
        try:
            if benchmark_end < formation_date:
                raise HistoryError("no completed benchmark observation")
            fetched_benchmark = normalize_observations(
                benchmark_fetch(str(spec["symbol"]), formation_date, benchmark_end),
                formation_date,
                benchmark_end,
                str(spec["symbol"]),
            )
            if not fetched_benchmark:
                raise HistoryError("no usable benchmark observations")
        except Exception as error:  # Comparisons must never prevent account NAV publication.
            fetch_failed = True
            benchmark_failures.append(f"{comparison_id}: {type(error).__name__}")
        comparison = build_comparison(
            spec,
            formation_date=formation_date,
            calendar_dates=expected_dates,
            opening_nav=formation_nav,
            market_sessions=session_set,
            fetched=fetched_benchmark,
            fallback_series=retained,
            fetch_failed=fetch_failed,
        )
        if comparison["status"] == "unavailable" and not fetch_failed:
            benchmark_failures.append(f"{comparison_id}: missing formation baseline")
        comparisons.append(comparison)

    available_comparisons = [comparison for comparison in comparisons if comparison["points"]]
    benchmark_coverage_status = (
        "complete"
        if len(available_comparisons) == len(BENCHMARK_SPECS) and all(comparison["status"] == "ready" for comparison in comparisons)
        else "degraded"
        if available_comparisons
        else "unavailable"
    )

    mark_history: dict[str, list[dict[str, Any]]] = {}
    for symbol in symbols:
        mark_history[symbol] = [
            {
                "date": observed.isoformat(),
                "price": round(observations[symbol][observed], 6),
                "source": observation_source[symbol].get(observed, "fallback"),
            }
            for observed in sorted(observations[symbol])
            if observed <= last_session
        ]

    analytics_through = dt.datetime.combine(last_session, MARKET_CLOSE, MARKET_ZONE)
    analytics = build_analytics(
        ledger,
        through=analytics_through,
        state=analytics_state,
        market_values=analytics_market_values,
        latest_nav=analytics_nav,
        exposure_history=exposure_history,
    )

    return {
        "schema_version": 1,
        "demo": True,
        "account_id": account_id,
        "currency": str(ledger.get("currency", "USD")),
        "generated_at": utc_text(as_of),
        "formation_date": formation_date.isoformat(),
        "last_completed_session": last_session.isoformat(),
        "coverage_status": "degraded" if failures or any(point["quality"] == "degraded" for point in points) else "complete",
        "source": "Yahoo Finance daily closes plus the published QSF synthetic ledger and calibrated option models; not an official NAV.",
        "failures": sorted(failures),
        "benchmark_coverage_status": benchmark_coverage_status,
        "benchmark_failures": sorted(set(benchmark_failures)),
        "market_sessions": [day.isoformat() for day in calculated_sessions],
        "accounts": {
            account_id: {
                "currency": str(ledger.get("currency", "USD")),
                "opening_nav": formation_nav,
                "points": points,
                "comparisons": comparisons,
                "analytics": analytics,
            }
        },
        "mark_history": mark_history,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=pathlib.Path, default=DEFAULT_LEDGER)
    parser.add_argument("--accounts", type=pathlib.Path, default=DEFAULT_ACCOUNTS)
    parser.add_argument("--output", type=pathlib.Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--fallback", type=pathlib.Path)
    parser.add_argument("--as-of", help="ISO timestamp; a date alone means the end of that New York day")
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args(argv)

    ledger = load_json(args.ledger)
    validate_ledger(ledger)
    assert_current_account_snapshot(ledger, load_json(args.accounts))
    fallback = load_json(args.fallback) if args.fallback and args.fallback.exists() else None
    as_of = parse_datetime(args.as_of, date_at_end_of_day=True) if args.as_of else dt.datetime.now(dt.timezone.utc)

    def fetch(symbol: str, start: dt.date, end: dt.date) -> Mapping[dt.date, float]:
        return fetch_yahoo_daily_closes(symbol, start, end, timeout=args.timeout)

    benchmark_spec_by_symbol = {str(spec["symbol"]): spec for spec in BENCHMARK_SPECS}

    def fetch_benchmark(symbol: str, start: dt.date, end: dt.date) -> Mapping[dt.date, float]:
        spec = benchmark_spec_by_symbol[symbol]
        return fetch_yahoo_daily_closes(
            symbol,
            start,
            end,
            timeout=args.timeout,
            adjusted=spec["price_basis"] == "adjusted_close",
            utc_dates=symbol == "BTC-USD",
        )

    history = build_history(
        ledger,
        as_of=as_of,
        fetcher=fetch,
        benchmark_fetcher=fetch_benchmark,
        fallback=fallback,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(history, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "points": len(history["accounts"][str(ledger["account_id"])]["points"]),
        "last_completed_session": history["last_completed_session"],
        "coverage_status": history["coverage_status"],
        "benchmark_coverage_status": history["benchmark_coverage_status"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

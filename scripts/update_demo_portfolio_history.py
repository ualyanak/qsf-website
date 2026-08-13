#!/usr/bin/env python3
"""Build nightly NAV history for the intentionally public ``ahub`` demo.

The current portfolio remains defined by ``data/demo-accounts.json``.  This
module replays the separate synthetic ledger, downloads raw Yahoo daily closes,
and values the same seeded option models used by ``update_demo_quotes.py``.
It never creates a point for a market session that has not fully closed.
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


def apply_trade_leg(state: dict[str, Any], ledger: Mapping[str, Any], leg: Mapping[str, Any]) -> None:
    instrument_id = str(leg.get("instrument", ""))
    multiplier = instrument_multiplier(ledger, instrument_id)
    signed_quantity = finite_number(leg.get("signed_quantity"), f"{instrument_id} trade quantity")
    price = finite_number(leg.get("price"), f"{instrument_id} trade price")
    if abs(signed_quantity) < EPSILON or price < 0:
        raise HistoryError(f"Invalid trade leg for {instrument_id}")

    state["cash"] -= signed_quantity * price * multiplier
    old_lot = state["positions"].get(instrument_id)
    old_quantity = finite_number(old_lot.get("quantity"), "old quantity") if old_lot else 0.0
    old_basis = finite_number(old_lot.get("basis_price"), "old basis") if old_lot else price
    new_quantity = old_quantity + signed_quantity

    if abs(new_quantity) < EPSILON:
        state["positions"].pop(instrument_id, None)
        return
    if abs(old_quantity) < EPSILON or old_quantity * new_quantity < 0:
        new_basis = price
    elif old_quantity * signed_quantity > 0:
        new_basis = (abs(old_quantity) * old_basis + abs(signed_quantity) * price) / abs(new_quantity)
    else:
        new_basis = old_basis
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


def fetch_yahoo_daily_closes(symbol: str, start: dt.date, end: dt.date, timeout: int = 20) -> dict[dt.date, float]:
    period_start = int(dt.datetime.combine(start - dt.timedelta(days=5), dt.time(), dt.timezone.utc).timestamp())
    period_end = int(dt.datetime.combine(end + dt.timedelta(days=2), dt.time(), dt.timezone.utc).timestamp())
    parameters = urllib.parse.urlencode({
        "period1": period_start,
        "period2": period_end,
        "interval": "1d",
        "events": "div,splits",
        "includeAdjustedClose": "false",
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
            timestamps = result.get("timestamp") or []
            quote_rows = result.get("indicators", {}).get("quote") or []
            closes = quote_rows[0].get("close") if quote_rows else []
            observations: dict[dt.date, float] = {}
            for timestamp, close in zip(timestamps, closes or []):
                if close is None:
                    continue
                price = finite_number(close, f"{symbol} Yahoo close")
                if price <= 0:
                    continue
                session_date = dt.datetime.fromtimestamp(int(timestamp), dt.timezone.utc).astimezone(MARKET_ZONE).date()
                if start <= session_date <= end:
                    observations[session_date] = price
            if not observations:
                raise HistoryError(f"Yahoo returned no usable daily closes for {symbol}")
            return observations
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
            result[observed_date] = price
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


def position_value(
    ledger: Mapping[str, Any],
    state: Mapping[str, Any],
    marks: Mapping[str, float],
    valuation_time: dt.datetime,
) -> float:
    total = 0.0
    instruments = ledger["instruments"]
    volatility_cache: dict[str, float] = {}
    for instrument_id, lot in state["positions"].items():
        instrument = instruments[instrument_id]
        quantity = finite_number(lot["quantity"], f"{instrument_id} quantity")
        multiplier = instrument_multiplier(ledger, instrument_id)
        if instrument["kind"] == "equity":
            mark = marks[str(instrument["symbol"])]
        else:
            model_id = str(instrument["model_spec_id"])
            spec = option_models.OPTION_MODEL_SPECS.get(model_id)
            if not spec:
                raise HistoryError(f"Missing option model specification: {model_id}")
            underlier = str(instrument["underlying"])
            volatility = volatility_cache.setdefault(model_id, option_models.calibrated_volatility(spec))
            mark = option_models.modeled_strategy_value(spec, marks[underlier], valuation_time.astimezone(dt.timezone.utc), volatility)
        if not math.isfinite(mark) or mark < 0:
            raise HistoryError(f"Invalid mark for {instrument_id}")
        total += quantity * multiplier * mark
    return total


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
    formation_nav = finite_number(ledger["formation"]["nav"], "formation NAV")
    formation_cash = finite_number(ledger["formation"]["cash"], "formation cash")
    formation_positions_value = formation_nav - formation_cash
    formation_state = initial_state(ledger)
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
    session_set = set(calculated_sessions)
    last_source_date = formation_date
    last_point = points[0]
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
        positions_total = position_value(ledger, state, marks, close_time)
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
        last_point = point
        last_source_date = day
        day += dt.timedelta(days=1)

    expected_dates = [formation_date + dt.timedelta(days=index) for index in range((history_end - formation_date).days + 1)]
    if [point["date"] for point in points] != [day.isoformat() for day in expected_dates]:
        raise HistoryError("History points are not unique, sorted, and calendar-contiguous")

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

    return {
        "schema_version": 1,
        "demo": True,
        "account_id": str(ledger["account_id"]),
        "currency": str(ledger.get("currency", "USD")),
        "generated_at": utc_text(as_of),
        "formation_date": formation_date.isoformat(),
        "last_completed_session": last_session.isoformat(),
        "coverage_status": "degraded" if failures or any(point["quality"] == "degraded" for point in points) else "complete",
        "source": "Yahoo Finance raw daily closes plus the published QSF synthetic ledger and calibrated option models; not an official NAV.",
        "failures": sorted(failures),
        "market_sessions": [day.isoformat() for day in calculated_sessions],
        "accounts": {
            str(ledger["account_id"]): {
                "currency": str(ledger.get("currency", "USD")),
                "opening_nav": formation_nav,
                "points": points,
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

    history = build_history(ledger, as_of=as_of, fetcher=fetch, fallback=fallback)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(history, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "points": len(history["accounts"][str(ledger["account_id"])]["points"]),
        "last_completed_session": history["last_completed_session"],
        "coverage_status": history["coverage_status"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

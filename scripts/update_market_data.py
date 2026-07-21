#!/usr/bin/env python3
"""Build the public performance snapshot without API keys.

The browser never calls finance providers directly.  This script is run by
GitHub Actions and publishes its deterministic JSON output on the dedicated
``market-data`` branch.  SPY and GLD use Yahoo's adjusted-close series (so
distributions and splits are reflected), BTC-USD uses Yahoo price history, and
the risk-free comparator compounds the FRED DGS3MO annual yield.

QSF strategy figures are deliberately *not* extrapolated.  They are the exact
management-reported monthly observations that were already on the site through
September 2025 and carry an explicit stale/as-of label in the output.
"""

from __future__ import annotations

import argparse
import calendar
import copy
import csv
import io
import json
import math
import sys
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPOSITORY_ROOT / "data" / "performance.json"
START_MONTH = "2020-01"
QSF_AS_OF = "2025-09-30"
USER_AGENT = "QSFMarketData/1.0 (+https://quantumstrategyfund.com/)"
YAHOO_HOSTS = ("query2.finance.yahoo.com", "query1.finance.yahoo.com")
FRED_SERIES = "DGS3MO"
FRESHNESS_WINDOWS = {
    # Exchange-traded funds do not update while their market is closed. Five
    # days covers ordinary weekends and common long-weekend closures.
    "spy": timedelta(days=5),
    "gold-gld": timedelta(days=5),
    # Bitcoin trades continuously, so an old but technically successful
    # provider response should be treated as stale much sooner.
    "btc-usd": timedelta(hours=3),
    # FRED generally publishes this daily series on business days and can lag
    # around holidays or source revisions.
    "risk-free": timedelta(days=10),
}


def month_keys(start: str, end: str) -> list[str]:
    year, month = (int(part) for part in start.split("-"))
    end_year, end_month = (int(part) for part in end.split("-"))
    result: list[str] = []
    while (year, month) <= (end_year, end_month):
        result.append(f"{year:04d}-{month:02d}")
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
    return result


QSF_MONTHS = month_keys(START_MONTH, "2025-09")
QSF_VALUES: dict[str, list[float]] = {
    "qsf-medium": [
        0.00, -0.49, -9.37, 40.01, 50.47, 29.66, 19.35, 11.59, 9.43,
        23.57, 20.54, 52.85, 62.51, 71.55, 61.95, 57.38, 52.49,
        61.53, 70.45, 89.69, 76.41, 65.48, 83.36, 94.72, 111.97,
        119.95, 120.98, 88.92, 75.61, 70.91, 69.78, 74.30, 64.25,
        50.08, 44.62, 49.82, 65.57, 78.63, 88.70, 109.95, 124.30,
        122.89, 129.65, 152.58, 158.95, 149.12, 197.87, 211.63,
        151.89, 160.89, 170.40, 180.34, 179.67, 181.91, 190.58,
        186.65, 175.96, 187.38, 189.32, 198.02, 175.02, 193.67,
        291.72, 301.08, 311.60, 310.33, 322.34, 334.48, 310.20,
    ],
    "qsf-high": [
        0.00, -0.49, -9.37, 40.01, 50.47, 29.66, 19.35, 11.59, 9.43,
        23.57, 20.54, 52.85, 62.51, 71.55, 61.95, 57.38, 52.49,
        61.53, 70.45, 89.69, 76.41, 65.48, 83.36, 94.72, 111.97,
        119.95, 120.98, 88.92, 75.61, 70.91, 69.78, 74.30, 64.25,
        50.08, 44.62, 49.82, 65.57, 78.63, 88.70, 109.95, 124.30,
        122.89, 129.65, 152.58, 358.95, 631.62, 295.33, 693.57,
        1392.11, 1559.67, 1760.43, 651.98, 666.13, 823.27, 1019.41,
        1374.12, 1847.99, 1995.27, 1098.03, 873.61, 1088.45,
        1560.34, 2201.27, 1904.62, 2313.56, 2622.45, 2036.88,
        2123.54, 1863.23,
    ],
    "qsf-extreme": [
        0.00, -0.49, -9.37, 40.01, 50.47, 29.66, 19.35, 11.59, 9.43,
        23.57, 20.54, 52.85, 62.51, 71.55, 61.95, 57.38, 52.49,
        61.53, 70.45, 89.69, 76.41, 65.48, 83.36, 94.72, 111.97,
        119.95, 120.98, 88.92, 75.61, 70.91, 69.78, 74.30, 64.25,
        50.08, 44.62, 49.82, 65.57, 78.63, 88.70, 109.95, 124.30,
        122.89, 129.65, 152.58, 358.95, 631.62, 295.33, 693.57,
        1392.11, 1559.67, 1760.43, 651.98, 666.13, 823.27, 1019.41,
        1374.12, 1847.99, 1995.27, 1098.03, 873.61, 888.45,
        3260.49, 5401.22, 3504.32, 5913.23, 8322.45, 7036.88,
        8057.54, 6587.34,
    ],
}

# Used only when the network is unavailable and no prior live snapshot exists.
# These are the site's former static values through September 2025.  They are
# labeled as a fallback in JSON and are never presented as current/live.
LEGACY_FALLBACK_VALUES: dict[str, list[float]] = {
    "spy": [
        0.00, -0.04, -7.97, -19.46, -9.23, -4.89, -3.21, 2.51, 9.64,
        5.54, 2.91, 14.10, 18.34, 17.12, 20.40, 25.84, 32.48, 33.34,
        35.91, 39.27, 43.34, 36.65, 46.29, 45.10, 51.78, 43.80,
        39.56, 44.84, 32.13, 32.42, 21.49, 32.68, 27.23, 15.51,
        24.84, 31.80, 24.23, 32.07, 28.77, 33.47, 35.61, 36.27,
        45.08, 49.85, 47.33, 40.38, 37.33, 49.90, 56.73, 59.26,
        67.52, 73.10, 66.04, 74.47, 80.59, 82.77, 87.09, 90.96,
        89.35, 100.55, 95.74, 101.01, 98.45, 87.35, 85.76, 97.03,
        107.62, 112.37, 113.60,
    ],
    "gold-gld": [
        0.00, 4.41, 6.54, 7.11, 12.75, 13.73, 16.84, 28.14, 27.85,
        23.49, 23.57, 13.73, 22.42, 18.82, 12.10, 9.65, 14.23,
        24.20, 14.72, 17.42, 17.34, 12.83, 15.29, 14.14, 17.42,
        16.93, 23.22, 25.67, 22.42, 17.60, 15.94, 13.00, 9.00,
        4.66, 3.11, 11.36, 15.29, 22.51, 14.99, 26.41, 26.84,
        26.15, 21.99, 25.12, 23.88, 17.09, 26.73, 29.72, 30.27,
        28.77, 29.10, 41.44, 44.61, 47.85, 47.59, 55.20, 59.17,
        66.61, 74.37, 67.27, 64.65, 77.03, 80.75, 96.62, 105.58,
        105.66, 107.27, 105.79, 122.20,
    ],
    "btc-usd": [
        0.00, 30.01, 19.23, -13.38, -10.51, 20.48, 32.44, 26.60,
        58.01, 62.27, 49.82, 90.11, 170.17, 306.14, 366.43,
        542.56, 719.36, 708.34, 407.94, 374.97, 486.94, 549.88,
        505.40, 751.33, 690.52, 540.91, 433.82, 499.31, 531.53,
        422.38, 340.81, 176.88, 223.43, 178.17, 169.43, 184.27,
        138.12, 129.40, 220.88, 221.07, 294.99, 305.67, 277.61,
        322.67, 305.50, 259.69, 273.98, 481.00, 529.81, 588.24,
        582.81, 754.00, 879.73, 735.15, 839.18, 779.62, 886.41,
        712.31, 883.74, 861.51, 1239.17, 1195.64, 1319.33,
        1195.14, 1051.45, 1215.00, 1349.28, 1504.94, 1394.42,
    ],
    "risk-free": [
        0.00, 0.12, 0.25, 0.38, 0.38, 0.38, 0.38, 0.38, 0.38,
        0.38, 0.38, 0.38, 0.38, 0.38, 0.38, 0.38, 0.38, 0.38,
        0.38, 0.38, 0.38, 0.38, 0.38, 0.38, 0.38, 0.38, 0.38,
        0.38, 0.38, 0.38, 0.38, 0.38, 0.40, 0.42, 0.44, 0.51,
        0.57, 0.70, 0.89, 1.08, 1.33, 1.59, 1.91, 2.23, 2.59,
        2.97, 3.36, 3.75, 4.16, 4.59, 5.02, 5.46, 5.91, 6.36,
        6.82, 7.29, 7.77, 8.25, 8.73, 9.21, 9.69, 10.19, 10.68,
        11.18, 11.69, 12.19, 12.69, 13.09, 13.38,
    ],
}


SERIES_STYLE = {
    "qsf-medium": ("QSF Medium Risk", "#36c98f", False),
    "qsf-high": ("QSF High Risk", "#e5a93d", False),
    "qsf-extreme": ("QSF Extreme Risk", "#e15b64", False),
    "spy": ("SPY (adjusted)", "#5da9e9", True),
    "gold-gld": ("Gold (GLD proxy, adjusted)", "#d6b35a", True),
    "btc-usd": ("Bitcoin (BTC-USD)", "#f7931a", True),
    "risk-free": ("Risk-Free (3-Month Treasury accrual proxy)", "#93a8c4", True),
}


class SourceUnavailable(RuntimeError):
    """Raised when a public source cannot return a usable series."""


@dataclass(frozen=True)
class YahooObservation:
    timestamp: int
    day: date
    adjusted: float
    close: float


def utc_iso(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def month_end_day(month: str) -> str:
    year, number = (int(part) for part in month.split("-"))
    return f"{month}-{calendar.monthrange(year, number)[1]:02d}"


def round_number(value: float, places: int = 4) -> float:
    rounded = round(float(value), places)
    return 0.0 if rounded == 0 else rounded


def json_from_url(url: str, timeout: float) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def text_from_url(url: str, timeout: float) -> str:
    request = Request(url, headers={"Accept": "text/csv", "User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8-sig")


def yahoo_chart(symbol: str, parameters: dict[str, str | int], timeout: float) -> dict[str, Any]:
    """Fetch a Yahoo chart response, using query1 if query2 is unavailable."""
    path_symbol = quote(symbol, safe="")
    query = urlencode(parameters)
    last_error: Exception | None = None
    for host in YAHOO_HOSTS:
        url = f"https://{host}/v8/finance/chart/{path_symbol}?{query}"
        try:
            payload = json_from_url(url, timeout)
            chart = payload.get("chart") or {}
            if chart.get("error"):
                raise SourceUnavailable("Yahoo returned a chart error")
            results = chart.get("result") or []
            if not results:
                raise SourceUnavailable("Yahoo returned no chart result")
            return results[0]
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, SourceUnavailable) as error:
            last_error = error
    raise SourceUnavailable(f"Yahoo chart unavailable for {symbol}") from last_error


def yahoo_observations(result: dict[str, Any]) -> list[YahooObservation]:
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    quote_rows = (indicators.get("quote") or [{}])[0]
    close_values = quote_rows.get("close") or []
    adj_rows = indicators.get("adjclose") or []
    adjusted_values = (adj_rows[0].get("adjclose") if adj_rows else None) or close_values
    rows: list[YahooObservation] = []
    for timestamp, adjusted, close in zip(timestamps, adjusted_values, close_values):
        if adjusted is None or close is None:
            continue
        adjusted_number = float(adjusted)
        close_number = float(close)
        if not (math.isfinite(adjusted_number) and math.isfinite(close_number)) or adjusted_number <= 0 or close_number <= 0:
            continue
        rows.append(
            YahooObservation(
                timestamp=int(timestamp),
                day=datetime.fromtimestamp(int(timestamp), timezone.utc).date(),
                adjusted=adjusted_number,
                close=close_number,
            )
        )
    rows.sort(key=lambda item: item.timestamp)
    if not rows:
        raise SourceUnavailable("Yahoo returned no valid prices")
    return rows


def make_points(monthly: OrderedDict[str, tuple[date, float]], baseline: float) -> list[dict[str, Any]]:
    return [
        {
            "date": observed_day.isoformat(),
            "kind": "month_end",
            "period": month,
            "value": round_number((value / baseline - 1.0) * 100.0),
        }
        for month, (observed_day, value) in monthly.items()
    ]


def fetch_yahoo_series(series_id: str, symbol: str, timeout: float, today: date) -> dict[str, Any]:
    period_start = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp())
    period_end = int(datetime.combine(today + timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc).timestamp())
    daily_result = yahoo_chart(
        symbol,
        {
            "period1": period_start,
            "period2": period_end,
            "interval": "1d",
            "events": "div,splits",
            "includeAdjustedClose": "true",
        },
        timeout,
    )
    daily = yahoo_observations(daily_result)
    by_month: OrderedDict[str, YahooObservation] = OrderedDict()
    for observation in daily:
        by_month[observation.day.strftime("%Y-%m")] = observation
    if START_MONTH not in by_month:
        raise SourceUnavailable(f"Yahoo has no {START_MONTH} baseline for {symbol}")
    baseline = by_month[START_MONTH].adjusted
    current_month = today.strftime("%Y-%m")
    completed: OrderedDict[str, tuple[date, float]] = OrderedDict(
        (month, (observation.day, observation.adjusted))
        for month, observation in by_month.items()
        if START_MONTH <= month < current_month
    )
    if START_MONTH not in completed:
        completed[START_MONTH] = (by_month[START_MONTH].day, baseline)

    latest_daily = daily[-1]
    latest_timestamp = latest_daily.timestamp
    latest_price = latest_daily.close
    latest_adjusted = latest_daily.adjusted
    latest_kind = "daily_close"

    # Fifteen-minute bars provide the best no-key delayed quote available.  An
    # intraday raw price is multiplied by the latest adj-close/close factor so
    # it remains comparable with the dividend-adjusted historical baseline.
    try:
        intraday_result = yahoo_chart(
            symbol,
            {
                "range": "5d",
                "interval": "15m",
                "events": "div,splits",
                "includePrePost": "false",
            },
            timeout,
        )
        intraday = yahoo_observations(intraday_result)
        newest = intraday[-1]
        if newest.timestamp >= latest_timestamp:
            adjustment_factor = latest_daily.adjusted / latest_daily.close
            latest_timestamp = newest.timestamp
            latest_price = newest.close
            latest_adjusted = newest.close * adjustment_factor
            latest_kind = "intraday_15m"
    except SourceUnavailable:
        # Daily adjusted data still produces a valid snapshot.  The output's
        # `kind` makes the lower-frequency fallback visible to the browser.
        pass

    label, color, hidden = SERIES_STYLE[series_id]
    return {
        "category": "benchmark",
        "color": color,
        "default_hidden": hidden,
        "id": series_id,
        "label": label,
        "latest": {
            "kind": latest_kind,
            "market_price": round_number(latest_price, 6),
            "timestamp": utc_iso(latest_timestamp),
            "value": round_number((latest_adjusted / baseline - 1.0) * 100.0),
        },
        "points": make_points(completed, baseline),
        "provenance": {
            "adjustment": "Yahoo adjusted close" if symbol != "BTC-USD" else "Yahoo BTC-USD close",
            "as_of": utc_iso(latest_timestamp),
            "live": True,
            "source": "Yahoo Finance chart endpoint",
            "symbol": symbol,
        },
        "units": "cumulative_return_percent",
    }


def fred_rows(csv_text: str) -> list[tuple[date, float]]:
    rows: list[tuple[date, float]] = []
    for row in csv.DictReader(io.StringIO(csv_text)):
        day_text = row.get("observation_date") or row.get("DATE") or ""
        value_text = row.get(FRED_SERIES, "")
        if not day_text or value_text in {"", "."}:
            continue
        try:
            day = date.fromisoformat(day_text)
            value = float(value_text)
        except ValueError:
            continue
        if math.isfinite(value):
            rows.append((day, value))
    rows.sort(key=lambda item: item[0])
    if not rows:
        raise SourceUnavailable("FRED returned no DGS3MO observations")
    return rows


def fetch_risk_free_series(timeout: float, today: date) -> dict[str, Any]:
    query = urlencode({"id": FRED_SERIES, "cosd": "2020-01-01", "coed": today.isoformat()})
    try:
        csv_text = text_from_url(f"https://fred.stlouisfed.org/graph/fredgraph.csv?{query}", timeout)
    except (HTTPError, URLError, TimeoutError) as error:
        raise SourceUnavailable("FRED CSV unavailable") from error
    observations = fred_rows(csv_text)
    baseline_candidates = [row for row in observations if row[0].strftime("%Y-%m") == START_MONTH]
    if not baseline_candidates:
        raise SourceUnavailable(f"FRED has no {START_MONTH} baseline")
    baseline_day, previous_rate = baseline_candidates[-1]
    previous_day = baseline_day
    wealth = 1.0
    wealth_path: list[tuple[date, float]] = [(baseline_day, wealth)]
    for observed_day, annual_rate in observations:
        if observed_day <= baseline_day:
            continue
        elapsed_days = (observed_day - previous_day).days
        wealth *= (1.0 + previous_rate / 100.0) ** (elapsed_days / 365.0)
        wealth_path.append((observed_day, wealth))
        previous_day = observed_day
        previous_rate = annual_rate

    current_month = today.strftime("%Y-%m")
    completed: OrderedDict[str, tuple[date, float]] = OrderedDict()
    for observed_day, observed_wealth in wealth_path:
        month = observed_day.strftime("%Y-%m")
        if START_MONTH <= month < current_month:
            completed[month] = (observed_day, observed_wealth)
    if START_MONTH not in completed:
        completed[START_MONTH] = (baseline_day, 1.0)
    latest_day, latest_wealth = wealth_path[-1]
    label, color, hidden = SERIES_STYLE["risk-free"]
    return {
        "category": "benchmark",
        "color": color,
        "default_hidden": hidden,
        "id": "risk-free",
        "label": label,
        "latest": {
            "kind": "daily_fred_observation",
            "timestamp": f"{latest_day.isoformat()}T00:00:00Z",
            "value": round_number((latest_wealth - 1.0) * 100.0),
        },
        "points": make_points(completed, 1.0),
        "provenance": {
            "as_of": latest_day.isoformat(),
            "compounding": "Previous observed DGS3MO annual yield, ACT/365, compounded between observations",
            "live": True,
            "series": FRED_SERIES,
            "source": "Federal Reserve Bank of St. Louis (FRED CSV)",
        },
        "units": "cumulative_return_percent",
    }


def qsf_series(series_id: str) -> dict[str, Any]:
    values = QSF_VALUES[series_id]
    if len(values) != len(QSF_MONTHS):
        raise RuntimeError(f"{series_id} history length does not match its month labels")
    label, color, hidden = SERIES_STYLE[series_id]
    return {
        "category": "strategy",
        "color": color,
        "default_hidden": hidden,
        "id": series_id,
        "label": label,
        "latest": None,
        "points": [
            {
                "date": month_end_day(month),
                "kind": "management_reported_month_end",
                "period": month,
                "value": value,
            }
            for month, value in zip(QSF_MONTHS, values)
        ],
        "provenance": {
            "as_of": QSF_AS_OF,
            "live": False,
            "source": "QSF management-reported website series",
            "statement": "Historical monthly figures through September 2025; not automatically updated or extrapolated.",
        },
        "units": "cumulative_return_percent",
    }


def legacy_fallback(series_id: str) -> dict[str, Any]:
    label, color, hidden = SERIES_STYLE[series_id]
    values = LEGACY_FALLBACK_VALUES[series_id]
    return {
        "category": "benchmark",
        "color": color,
        "default_hidden": hidden,
        "id": series_id,
        "label": label,
        "latest": None,
        "points": [
            {"date": month_end_day(month), "kind": "legacy_static_fallback", "period": month, "value": value}
            for month, value in zip(QSF_MONTHS, values)
        ],
        "provenance": {
            "as_of": QSF_AS_OF,
            "live": False,
            "source": "Legacy static website chart fallback",
            "statement": "Fallback only; public live source was unavailable during generation.",
        },
        "units": "cumulative_return_percent",
    }


def load_existing(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def existing_series(payload: dict[str, Any] | None, series_id: str) -> dict[str, Any] | None:
    if not payload:
        return None
    for series in payload.get("series", []):
        if isinstance(series, dict) and series.get("id") == series_id and series.get("points"):
            return copy.deepcopy(series)
    return None


def latest_as_of(series_collection: Iterable[dict[str, Any]]) -> str:
    timestamps: list[str] = []
    for series in series_collection:
        latest = series.get("latest")
        if isinstance(latest, dict) and latest.get("timestamp"):
            timestamps.append(str(latest["timestamp"]))
        provenance = series.get("provenance") or {}
        if provenance.get("as_of"):
            value = str(provenance["as_of"])
            timestamps.append(value if "T" in value else f"{value}T00:00:00Z")
    return max(timestamps, default=f"{QSF_AS_OF}T00:00:00Z")


def parse_as_of(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if "T" not in text:
        text += "T00:00:00Z"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def add_freshness(series: dict[str, Any], checked_at: datetime) -> bool:
    series_id = str(series.get("id", ""))
    window = FRESHNESS_WINDOWS[series_id]
    latest = series.get("latest") or {}
    provenance = series.get("provenance") or {}
    observed = parse_as_of(latest.get("timestamp") or provenance.get("as_of"))
    if observed is None:
        series["freshness"] = {
            "as_of": None,
            "max_age_minutes": int(window.total_seconds() // 60),
            "within_expected_window": False,
        }
        return False
    age = max(timedelta(0), checked_at - observed)
    within_window = observed <= checked_at + timedelta(minutes=5) and age <= window
    series["freshness"] = {
        "age_minutes_at_check": round(age.total_seconds() / 60, 1),
        "as_of": observed.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "max_age_minutes": int(window.total_seconds() // 60),
        "within_expected_window": within_window,
    }
    return within_window


def build_snapshot(timeout: float, offline: bool, strict: bool, existing: dict[str, Any] | None) -> dict[str, Any]:
    checked_at = datetime.now(timezone.utc)
    today = checked_at.date()
    series: list[dict[str, Any]] = [qsf_series(series_id) for series_id in ("qsf-medium", "qsf-high", "qsf-extreme")]
    fetchers = {
        "spy": lambda: fetch_yahoo_series("spy", "SPY", timeout, today),
        "gold-gld": lambda: fetch_yahoo_series("gold-gld", "GLD", timeout, today),
        "btc-usd": lambda: fetch_yahoo_series("btc-usd", "BTC-USD", timeout, today),
        "risk-free": lambda: fetch_risk_free_series(timeout, today),
    }
    failures: list[str] = []
    stale_sources: list[str] = []
    for series_id, fetcher in fetchers.items():
        try:
            if offline:
                raise SourceUnavailable("offline mode")
            benchmark = fetcher()
        except Exception as error:  # Each benchmark may safely retain its last good snapshot.
            if not isinstance(error, (SourceUnavailable, HTTPError, URLError, TimeoutError, json.JSONDecodeError)):
                raise
            failures.append(series_id)
            benchmark = existing_series(existing, series_id) or legacy_fallback(series_id)
            benchmark["refresh_status"] = "retained_after_source_unavailable"
        else:
            benchmark["refresh_status"] = "refreshed"
        if not add_freshness(benchmark, checked_at):
            stale_sources.append(series_id)
            if benchmark["refresh_status"] == "refreshed":
                benchmark["refresh_status"] = "refreshed_but_stale"
        series.append(benchmark)

    if strict and (failures or stale_sources):
        reasons = []
        if failures:
            reasons.append("unavailable: " + ", ".join(failures))
        if stale_sources:
            reasons.append("stale: " + ", ".join(stale_sources))
        raise SourceUnavailable("Strict refresh failed (" + "; ".join(reasons) + ")")

    benchmark_series = [item for item in series if item["category"] == "benchmark"]
    strategy_series = [item for item in series if item["category"] == "strategy"]
    all_current = (
        not failures
        and not stale_sources
        and all(item.get("provenance", {}).get("live") for item in benchmark_series)
    )
    checked_at_iso = checked_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if failures:
        benchmark_status = "fallback_or_retained"
    elif stale_sources:
        benchmark_status = "stale_public_sources"
    else:
        benchmark_status = "current_public_sources"
    return {
        "generated_at": checked_at_iso,
        "methodology": {
            "baseline": "Last available January 2020 observation (displayed as 0%).",
            "benchmark_frequency": "Completed calendar month-end observations plus the latest available delayed observation.",
            "risk_free": "Hypothetical cash-accrual proxy using DGS3MO annual yields compounded between published observations on an ACT/365 basis; it is not an investable total-return index.",
            "returns": "Cumulative, not annualized. SPY and GLD use adjusted close; BTC-USD uses price return.",
        },
        "schema_version": 1,
        "series": series,
        "snapshot": {
            "benchmark_as_of": latest_as_of(benchmark_series),
            "benchmark_status": benchmark_status,
            "checked_at": checked_at_iso,
            "delay_notice": "Best-effort public data and workflow runs can be delayed beyond 15 minutes. FRED generally updates once per business day; each series retains its own as-of time.",
            "failed_sources": failures,
            "fund_as_of": QSF_AS_OF,
            "fund_status": "management_reported_not_live",
            "strategy_as_of": latest_as_of(strategy_series),
            "stale_sources": stale_sources,
        },
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.write_text(rendered, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Destination JSON file")
    parser.add_argument("--timeout", type=float, default=20.0, help="Per-request timeout in seconds")
    parser.add_argument("--offline", action="store_true", help="Generate/retain the deterministic fallback without network access")
    parser.add_argument("--strict", action="store_true", help="Fail without writing unless every public source refresh succeeds")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    existing = load_existing(args.output) or load_existing(DEFAULT_OUTPUT)
    try:
        payload = build_snapshot(args.timeout, args.offline, args.strict, existing)
    except SourceUnavailable as error:
        print(f"Market-data refresh aborted: {error}", file=sys.stderr)
        return 1
    write_json(args.output, payload)
    status = payload["snapshot"]["benchmark_status"]
    print(f"Wrote {args.output} ({status}; as of {payload['generated_at']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

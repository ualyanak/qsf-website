#!/usr/bin/env python3
"""Render a validated private account snapshot into the public QSF LaTeX template.

This program deliberately writes only a .tex file. A production worker should invoke
it in an isolated, network-disabled container and compile with shell escape disabled.
Private JSON and output files must live outside the public GitHub Pages tree.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable


TOKENS = {
    "ACCOUNT_NAME",
    "ACCOUNT_CODE",
    "VALUATION_AS_OF",
    "REPORT_GENERATED_AT",
    "ACCOUNT_NAV",
    "SINCE_INCEPTION_RETURN",
    "CASH_BALANCE",
    "QUOTE_QUALITY",
    "GROSS_ASSETS",
    "LIABILITIES",
    "MONTH_RETURN",
    "YTD_RETURN",
    "ALLOCATION_ROWS",
    "NAV_XMAX",
    "NAV_START_LABEL",
    "NAV_END_LABEL",
    "NAV_COORDINATES",
    "CURRENCY",
    "HOLDINGS_ROWS",
    "DISCLOSURE_ITEMS",
}

LATEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def latex_escape(value: Any, max_length: int = 500) -> str:
    text = "" if value is None else str(value)
    if len(text) > max_length:
        raise ValueError(f"text value exceeds {max_length} characters")
    return "".join(LATEX_ESCAPES.get(char, char) for char in text).replace("\n", r" ")


def decimal_value(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError(f"{field} must be numeric") from error
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result


def currency(value: Any, code: str) -> str:
    amount = decimal_value(value, "currency value")
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    symbol = r"\$" if code == "USD" else latex_escape(code) + r"\ "
    return f"{sign}{symbol}{amount:,.2f}"


def percent(value: Any) -> str:
    number = decimal_value(value, "percentage")
    prefix = "+" if number > 0 else ""
    return f"{prefix}{number:.2f}\\%"


def iso_display(value: Any, field: str) -> str:
    text = str(value or "")
    if not text:
        raise ValueError(f"{field} is required")
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError(f"{field} must be ISO-8601") from error
    suffix = parsed.strftime("%Z")
    return latex_escape(parsed.strftime("%b %d, %Y %H:%M") + (f" {suffix}" if suffix else ""))


def rows(value: Any, field: str, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    if len(value) > limit:
        raise ValueError(f"{field} exceeds {limit} rows")
    if not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{field} must contain objects")
    return value


def build_allocation(items: Iterable[dict[str, Any]], code: str) -> str:
    result = []
    for item in items:
        result.append(
            f"  {latex_escape(item.get('category', 'Other'), 100)} & "
            f"{currency(item.get('market_value', 0), code)} & "
            f"{percent(item.get('positive_asset_weight_pct', 0))} \\\\"
        )
    return "\n".join(result) or r"  No allocation available & --- & --- \\"


def build_holdings(items: Iterable[dict[str, Any]], code: str) -> str:
    result = []
    for item in items:
        quantity = decimal_value(item.get("quantity"), "holding quantity")
        mark = item.get("mark")
        market_value = item.get("market_value")
        result.append(
            "  {symbol} & {name} & {quantity} & {mark} & {market_value} & {quality} \\\\".format(
                symbol=latex_escape(item.get("symbol", ""), 40),
                name=latex_escape(item.get("name", ""), 180),
                quantity=f"{quantity:,.6f}".rstrip("0").rstrip("."),
                mark="---" if mark is None else currency(mark, item.get("currency", code)),
                market_value="---" if market_value is None else currency(market_value, code),
                quality=latex_escape(item.get("mark_quality", "unavailable"), 40),
            )
        )
    return "\n".join(result) or r"  --- & No posted holdings & --- & --- & --- & unavailable \\"


def build_history(items: list[dict[str, Any]]) -> tuple[str, str, str, str]:
    if not items:
        return "1", "No history", "No history", "(0,0) (1,0)"
    values: list[tuple[str, Decimal]] = []
    for item in items:
        date = str(item.get("date", ""))
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError as error:
            raise ValueError("nav_history dates must use YYYY-MM-DD") from error
        values.append((date, decimal_value(item.get("nav"), "nav history value")))
    xmax = max(1, len(values) - 1)
    coordinates = " ".join(f"({index},{nav:.4f})" for index, (_, nav) in enumerate(values))
    if len(values) == 1:
        coordinates += f" (1,{values[0][1]:.4f})"
    return str(xmax), latex_escape(values[0][0]), latex_escape(values[-1][0]), coordinates


def render(snapshot: dict[str, Any], template: str) -> str:
    account = snapshot.get("account")
    summary = snapshot.get("summary")
    if not isinstance(account, dict) or not isinstance(summary, dict):
        raise ValueError("snapshot requires account and summary objects")

    code = str(account.get("currency", "USD")).upper()
    if len(code) != 3 or not code.isalpha():
        raise ValueError("account.currency must be a three-letter code")

    allocation = rows(snapshot.get("allocation", []), "allocation", 100)
    holdings = rows(snapshot.get("holdings", []), "holdings", 500)
    history = rows(snapshot.get("nav_history", []), "nav_history", 5000)
    disclosures = snapshot.get("disclosures", [])
    if not isinstance(disclosures, list) or len(disclosures) > 30:
        raise ValueError("disclosures must be an array of at most 30 items")
    history_tokens = build_history(history)

    replacements = {
        "ACCOUNT_NAME": latex_escape(account.get("name", "Investor Account"), 160),
        "ACCOUNT_CODE": latex_escape(account.get("code", "Private"), 40),
        "VALUATION_AS_OF": iso_display(account.get("valuation_as_of"), "account.valuation_as_of"),
        "REPORT_GENERATED_AT": iso_display(snapshot.get("report_generated_at"), "report_generated_at"),
        "ACCOUNT_NAV": currency(summary.get("nav"), code),
        "SINCE_INCEPTION_RETURN": percent(summary.get("return_since_inception_pct", 0)),
        "CASH_BALANCE": currency(summary.get("cash_balance", 0), code),
        "QUOTE_QUALITY": latex_escape(summary.get("quote_quality", "unavailable"), 80),
        "GROSS_ASSETS": currency(summary.get("gross_assets", 0), code),
        "LIABILITIES": currency(summary.get("liabilities", 0), code),
        "MONTH_RETURN": percent(summary.get("month_return_pct", 0)),
        "YTD_RETURN": percent(summary.get("ytd_return_pct", 0)),
        "ALLOCATION_ROWS": build_allocation(allocation, code),
        "NAV_XMAX": history_tokens[0],
        "NAV_START_LABEL": history_tokens[1],
        "NAV_END_LABEL": history_tokens[2],
        "NAV_COORDINATES": history_tokens[3],
        "CURRENCY": latex_escape(code),
        "HOLDINGS_ROWS": build_holdings(holdings, code),
        "DISCLOSURE_ITEMS": "\n".join(
            f"  \\item {latex_escape(item, 500)}" for item in disclosures
        ) or r"  \item No additional valuation notes were supplied.",
    }

    output = template
    for token, value in replacements.items():
        output = output.replace(f"@@{token}@@", value)
    unresolved = sorted(token for token in TOKENS if f"@@{token}@@" in output)
    if unresolved:
        raise ValueError("unresolved template tokens: " + ", ".join(unresolved))
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a private QSF snapshot to LaTeX")
    parser.add_argument("snapshot", type=Path, help="private JSON snapshot path")
    parser.add_argument("output", type=Path, help="destination .tex path")
    parser.add_argument(
        "--template",
        type=Path,
        default=Path(__file__).resolve().parent / "templates" / "investor-report.tex",
    )
    args = parser.parse_args()

    if args.output.suffix.lower() != ".tex":
        parser.error("output must use the .tex extension")
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot root must be an object")
    rendered = render(snapshot, args.template.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Deterministic tests for the public demo portfolio-history generator."""

from __future__ import annotations

import copy
import datetime as dt
import pathlib
import sys
import unittest


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import update_demo_portfolio_history as history  # noqa: E402


REPOSITORY = pathlib.Path(__file__).resolve().parents[1]
APPROVED_RISK_LEVELS = {
    "infq": "high",
    "bull": "medium",
    "bull-dec2026-call": "high",
    "pltr": "medium",
    "phys": "low",
    "qbts": "medium",
    "ibm": "medium",
    "spy": "medium",
    "nvda": "medium",
    "wmt": "medium",
    "sgov": "low",
    "tssi": "high",
    "ivr": "high",
    "tmp": "medium",
}
PRE_AUGUST_21_RISK_LEVELS = {
    group_id: risk_level
    for group_id, risk_level in APPROVED_RISK_LEVELS.items()
    if group_id not in {"bull-dec2026-call", "tmp"}
}
PRE_AUGUST_26_RISK_LEVELS = {
    group_id: risk_level
    for group_id, risk_level in APPROVED_RISK_LEVELS.items()
    if group_id != "tmp"
}


class PortfolioHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ledger = history.load_json(REPOSITORY / "data/demo-portfolio-ledger.json")
        cls.accounts = history.load_json(REPOSITORY / "data/demo-accounts.json")
        cls.symbols = history.required_symbols(cls.ledger)
        cls.sessions = [
            dt.date(2026, 7, 17) + dt.timedelta(days=offset)
            for offset in range((dt.date(2026, 8, 26) - dt.date(2026, 7, 17)).days + 1)
            if (dt.date(2026, 7, 17) + dt.timedelta(days=offset)).weekday() < 5
        ]
        seeds = history.seed_marks(cls.ledger)
        cls.prices: dict[str, dict[dt.date, float]] = {}
        for symbol_index, symbol in enumerate(cls.symbols):
            base = seeds[symbol][0]
            cls.prices[symbol] = {
                day: base + session_index * 0.07 + symbol_index * 0.003
                for session_index, day in enumerate(cls.sessions)
            }
        cls.prices["GLD"] = {
            day: 350.0 + session_index * 1.25
            for session_index, day in enumerate(cls.sessions)
        }
        calendar_dates = [
            dt.date(2026, 7, 17) + dt.timedelta(days=offset)
            for offset in range((dt.date(2026, 8, 26) - dt.date(2026, 7, 17)).days + 1)
        ]
        cls.prices["BTC-USD"] = {
            day: 60000.0 + day_index * 275.0
            for day_index, day in enumerate(calendar_dates)
        }

    def fetcher(self, symbol: str, start: dt.date, end: dt.date) -> dict[dt.date, float]:
        return {day: price for day, price in self.prices[symbol].items() if start <= day <= end}

    def points(self, payload: dict[str, object]) -> list[dict[str, object]]:
        return payload["accounts"]["ahub"]["points"]  # type: ignore[index]

    def comparisons(self, payload: dict[str, object]) -> dict[str, dict[str, object]]:
        series = payload["accounts"]["ahub"]["comparisons"]  # type: ignore[index]
        return {item["id"]: item for item in series}

    @staticmethod
    def after_close(day: dt.date) -> dt.datetime:
        return dt.datetime.combine(day, dt.time(16, 30), history.MARKET_ZONE)

    @staticmethod
    def after_all_daily_closes(day: dt.date) -> dt.datetime:
        return dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0, 30), dt.timezone.utc)

    def test_formation_and_current_snapshot_reconcile(self) -> None:
        history.validate_ledger(self.ledger)
        history.assert_current_account_snapshot(self.ledger, self.accounts)
        opening = history.initial_state(self.ledger)
        basis_value = opening["cash"]
        for instrument_id, lot in opening["positions"].items():
            basis_value += lot["quantity"] * lot["basis_price"] * history.instrument_multiplier(self.ledger, instrument_id)
        self.assertAlmostEqual(opening["cash"], 389.83, places=2)
        self.assertAlmostEqual(basis_value, 9900.0, places=2)

    def test_attribution_groups_have_complete_approved_risk_levels(self) -> None:
        groups = self.ledger["attribution_groups"]
        self.assertEqual({group_id: group["risk_level"] for group_id, group in groups.items()}, APPROVED_RISK_LEVELS)
        referenced = {instrument["attribution_group_id"] for instrument in self.ledger["instruments"].values()}
        self.assertEqual(referenced, set(APPROVED_RISK_LEVELS))

        payload = history.build_history(
            self.ledger,
            as_of=self.after_all_daily_closes(dt.date(2026, 8, 19)),
            fetcher=self.fetcher,
        )
        contributors = payload["accounts"]["ahub"]["analytics"]["contributors"]
        self.assertEqual({item["id"]: item["risk_level"] for item in contributors}, PRE_AUGUST_21_RISK_LEVELS)

    def test_invalid_or_mismatched_attribution_risk_metadata_is_rejected(self) -> None:
        invalid_risk = copy.deepcopy(self.ledger)
        invalid_risk["attribution_groups"]["bull"]["risk_level"] = "extreme"
        with self.assertRaisesRegex(history.HistoryError, "unsupported risk level"):
            history.validate_ledger(invalid_risk)

        mismatched_label = copy.deepcopy(self.ledger)
        mismatched_label["instruments"]["BULL"]["attribution_group_label"] = "Different label"
        with self.assertRaisesRegex(history.HistoryError, "attribution label does not match"):
            history.validate_ledger(mismatched_label)

    def test_later_added_equity_seed_metadata_is_required_and_trade_anchored(self) -> None:
        missing = copy.deepcopy(self.ledger)
        missing["instruments"]["TMP"].pop("history_seed")
        with self.assertRaisesRegex(history.HistoryError, "Missing historical seed marks: TMP"):
            history.validate_ledger(missing)

        wrong_date = copy.deepcopy(self.ledger)
        wrong_date["instruments"]["TMP"]["history_seed"]["date"] = "2026-08-25"
        with self.assertRaisesRegex(history.HistoryError, "seed date must match its first trade date"):
            history.validate_ledger(wrong_date)

        wrong_price = copy.deepcopy(self.ledger)
        wrong_price["instruments"]["TMP"]["history_seed"]["price"] = 99.0
        with self.assertRaisesRegex(history.HistoryError, "seed price must match its first trade price"):
            history.validate_ledger(wrong_price)

        missing_source = copy.deepcopy(self.ledger)
        missing_source["instruments"]["TMP"]["history_seed"]["source"] = ""
        with self.assertRaisesRegex(history.HistoryError, "invalid history seed metadata"):
            history.validate_ledger(missing_source)

    def test_ledger_replays_each_supplied_portfolio_state(self) -> None:
        july_17 = history.replay_ledger(
            self.ledger,
            dt.datetime(2026, 7, 17, 16, 0, tzinfo=history.MARKET_ZONE),
        )
        july_20 = history.replay_ledger(
            self.ledger,
            dt.datetime(2026, 7, 20, 16, 0, tzinfo=history.MARKET_ZONE),
        )
        august_4 = history.replay_ledger(
            self.ledger,
            dt.datetime(2026, 8, 4, 16, 0, tzinfo=history.MARKET_ZONE),
        )
        before_august_13 = history.replay_ledger(
            self.ledger,
            dt.datetime(2026, 8, 13, 10, 59, tzinfo=history.MARKET_ZONE),
        )
        august_13 = history.replay_ledger(
            self.ledger,
            dt.datetime(2026, 8, 13, 16, 0, tzinfo=history.MARKET_ZONE),
        )
        before_august_14 = history.replay_ledger(
            self.ledger,
            dt.datetime(2026, 8, 14, 9, 29, tzinfo=history.MARKET_ZONE),
        )
        august_14 = history.replay_ledger(
            self.ledger,
            dt.datetime(2026, 8, 14, 9, 30, tzinfo=history.MARKET_ZONE),
        )
        before_august_18 = history.replay_ledger(
            self.ledger,
            dt.datetime(2026, 8, 18, 9, 29, tzinfo=history.MARKET_ZONE),
        )
        august_18 = history.replay_ledger(
            self.ledger,
            dt.datetime(2026, 8, 18, 9, 30, tzinfo=history.MARKET_ZONE),
        )
        before_august_19 = history.replay_ledger(
            self.ledger,
            dt.datetime(2026, 8, 19, 9, 29, tzinfo=history.MARKET_ZONE),
        )
        august_19 = history.replay_ledger(
            self.ledger,
            dt.datetime(2026, 8, 19, 9, 30, tzinfo=history.MARKET_ZONE),
        )
        before_august_21 = history.replay_ledger(
            self.ledger,
            dt.datetime(2026, 8, 21, 9, 29, tzinfo=history.MARKET_ZONE),
        )
        august_21 = history.replay_ledger(
            self.ledger,
            dt.datetime(2026, 8, 21, 9, 30, tzinfo=history.MARKET_ZONE),
        )
        before_august_26 = history.replay_ledger(
            self.ledger,
            history.parse_datetime("2026-08-26T09:05:59-05:00"),
        )
        august_26 = history.replay_ledger(
            self.ledger,
            history.parse_datetime("2026-08-26T09:06:00-05:00"),
        )

        self.assertAlmostEqual(july_17["cash"], 389.83, places=2)
        self.assertAlmostEqual(july_20["cash"], 413.83, places=2)
        self.assertEqual(history.position_quantities(july_20)["SGOV"], 26)
        self.assertAlmostEqual(august_4["cash"], 525.46, places=2)
        self.assertEqual(history.position_quantities(august_4)["SGOV"], 2)
        self.assertEqual(history.position_quantities(august_4)["BULL"], 320)
        self.assertEqual(history.position_quantities(before_august_13)["INFQ_C25_20270115"], 4)
        self.assertEqual(history.position_quantities(before_august_13)["INFQ_C10_C17_5_20270115"], 20)
        self.assertAlmostEqual(august_13["cash"], 583.43, places=2)
        self.assertEqual(history.position_quantities(august_13)["TSSI"], 30)
        self.assertAlmostEqual(before_august_14["cash"], 583.43, places=2)
        self.assertEqual(history.position_quantities(before_august_14)["TSSI"], 30)
        self.assertAlmostEqual(august_14["cash"], 872.93, places=2)
        self.assertNotIn("TSSI", history.position_quantities(august_14))
        self.assertEqual(history.position_quantities(august_14)["BULL"], 320)
        self.assertAlmostEqual(before_august_18["cash"], 872.93, places=2)
        self.assertAlmostEqual(august_18["cash"], 885.54, places=2)
        self.assertEqual(history.position_quantities(august_18), history.position_quantities(august_14))
        self.assertAlmostEqual(before_august_19["cash"], 885.54, places=2)
        self.assertEqual(history.position_quantities(before_august_19)["BULL"], 320)
        self.assertAlmostEqual(august_19["cash"], 1899.54, places=2)
        self.assertEqual(history.position_quantities(august_19)["BULL"], 200)
        self.assertAlmostEqual(before_august_21["cash"], 1899.54, places=2)
        self.assertNotIn("BULL_C10_20261218", history.position_quantities(before_august_21))
        self.assertAlmostEqual(august_21["cash"], 1917.04, places=2)
        self.assertEqual(history.position_quantities(august_21)["BULL_C10_20261218"], 2.5)
        self.assertEqual(august_21["positions"]["BULL_C10_20261218"]["basis_price"], 0.0)
        self.assertAlmostEqual(before_august_26["cash"], 1917.04, places=2)
        self.assertEqual(history.position_quantities(before_august_26)["SGOV"], 53)
        self.assertNotIn("TMP", history.position_quantities(before_august_26))
        self.assertAlmostEqual(august_26["cash"], 1930.29, places=2)
        self.assertEqual(history.position_quantities(august_26)["SGOV"], 48)
        self.assertEqual(history.position_quantities(august_26)["TMP"], 5)
        self.assertEqual(history.position_quantities(august_26), self.ledger["expected_current_snapshot"]["positions"])

    def test_august_fourteenth_tssi_sale_cash_and_loss(self) -> None:
        proceeds = 30 * 9.65
        basis = 30 * 9.94
        realized_pnl = proceeds - basis
        current = history.replay_ledger(
            self.ledger,
            dt.datetime(2026, 8, 14, 16, 0, tzinfo=history.MARKET_ZONE),
        )

        self.assertAlmostEqual(proceeds, 289.5, places=2)
        self.assertAlmostEqual(basis, 298.2, places=2)
        self.assertAlmostEqual(realized_pnl, -8.7, places=2)
        self.assertAlmostEqual(current["cash"], 583.43 + proceeds, places=2)
        self.assertNotIn("TSSI", history.position_quantities(current))
        self.assertEqual(len(history.position_quantities(current)), 12)

    def test_august_eighteenth_dividends_increase_cash_without_changing_positions(self) -> None:
        before = history.replay_ledger(
            self.ledger,
            dt.datetime(2026, 8, 18, 9, 29, tzinfo=history.MARKET_ZONE),
        )
        after = history.replay_ledger(
            self.ledger,
            dt.datetime(2026, 8, 18, 9, 30, tzinfo=history.MARKET_ZONE),
        )

        self.assertAlmostEqual(before["cash"], 872.93, places=2)
        self.assertAlmostEqual(after["cash"], 885.54, places=2)
        self.assertAlmostEqual(after["cash"] - before["cash"], 0.61 + 12.0, places=2)
        self.assertEqual(history.position_quantities(after), history.position_quantities(before))

    def test_september_fifth_sgov_dividend_is_income_not_a_trade_or_deposit(self) -> None:
        before_time = history.parse_datetime("2026-09-05T09:29:59-04:00")
        after_time = history.parse_datetime("2026-09-05T09:30:00-04:00")
        before = history.replay_ledger(self.ledger, before_time)
        after = history.replay_ledger(self.ledger, after_time)
        self.assertAlmostEqual(before["cash"], 1930.29, places=2)
        self.assertAlmostEqual(after["cash"], 1945.03, places=2)
        self.assertAlmostEqual(after["cash"] - before["cash"], 14.74, places=2)
        self.assertEqual(after["positions"], before["positions"])
        self.assertEqual(history.realized_trade_records(self.ledger, after_time),
                         history.realized_trade_records(self.ledger, before_time))
        income, unattributed, external = history.performance_cash_adjustments(self.ledger, after_time)
        self.assertAlmostEqual(income["SGOV"], 15.35, places=2)
        self.assertAlmostEqual(income["IVR"], 12.0, places=2)
        self.assertEqual(external, 0.0)
        self.assertEqual(unattributed["total"], 24.0)
        event = next(event for event in self.ledger["events"] if event["id"] == "2026-09-05-sgov-dividend")
        self.assertEqual(event["effective_date"], "2026-09-05")
        self.assertEqual(event["classification"], "dividend")
        self.assertEqual(sum(event["id"] == "2026-09-05-sgov-dividend" for event in self.ledger["events"]), 1)
        note = next(note for note in self.accounts["accounts"]["ahub"]["cash_notes"]
                    if note["date"] == "2026-09-05")
        self.assertEqual(note["kind"], "illustrative_dividend")
        self.assertEqual(note["instrument"], "SGOV")
        self.assertEqual(note["amount"], 14.74)

    def test_september_dividend_enters_next_completed_session_once(self) -> None:
        seeds = history.seed_marks(self.ledger)

        def september_fetcher(symbol: str, start: dt.date, end: dt.date) -> dict[dt.date, float]:
            base = 350.0 if symbol == "GLD" else 60000.0 if symbol == "BTC-USD" else seeds[symbol][0]
            days = [start + dt.timedelta(days=offset) for offset in range((end - start).days + 1)]
            return {day: base for day in days if day >= dt.date(2026, 7, 17) and (
                symbol == "BTC-USD" or (day.weekday() < 5 and day != dt.date(2026, 9, 7))
            )}

        prior_ledger = copy.deepcopy(self.ledger)
        prior_ledger["events"] = [event for event in prior_ledger["events"]
                                  if event["id"] != "2026-09-05-sgov-dividend"]
        prior_ledger["expected_current_snapshot"]["cash"] = 1930.29
        as_of = self.after_all_daily_closes(dt.date(2026, 9, 8))
        updated = history.build_history(self.ledger, as_of=as_of, fetcher=september_fetcher)
        prior = history.build_history(prior_ledger, as_of=as_of, fetcher=september_fetcher)
        updated_points = {point["date"]: point for point in self.points(updated)}
        prior_points = {point["date"]: point for point in self.points(prior)}
        for date, point in updated_points.items():
            if date < "2026-09-08":
                self.assertEqual(point, prior_points[date])
        for date in ("2026-09-04", "2026-09-05", "2026-09-06", "2026-09-07"):
            self.assertEqual(updated_points[date]["cash"], 1930.29)
        closing = updated_points["2026-09-08"]
        self.assertEqual(closing["kind"], "session_close")
        self.assertEqual(closing["cash"], 1945.03)
        self.assertAlmostEqual(closing["value"] - prior_points["2026-09-08"]["value"], 14.74, places=2)
        analytics = updated["accounts"]["ahub"]["analytics"]
        sgov = next(item for item in analytics["contributors"] if item["id"] == "sgov")
        self.assertEqual(sgov["income"], 15.35)
        self.assertEqual(analytics["reconciliation"]["external_flows"], 0.0)
        self.assertEqual(analytics["reconciliation"]["residual"], 0.0)
        self.assertEqual(analytics["realized_trades"], prior["accounts"]["ahub"]["analytics"]["realized_trades"])
        for key, group in (("exposure_history", "cash-cash-equivalents"), ("risk_history", "low")):
            latest_mix = analytics[key]["points"][-1]["values"][group]["value"]
            prior_mix = prior["accounts"]["ahub"]["analytics"][key]["points"][-1]["values"][group]["value"]
            self.assertAlmostEqual(latest_mix - prior_mix, 14.74, places=2)

    def test_august_nineteenth_bull_sale_increases_cash_and_reduces_shares(self) -> None:
        before = history.replay_ledger(
            self.ledger,
            dt.datetime(2026, 8, 19, 9, 29, tzinfo=history.MARKET_ZONE),
        )
        after = history.replay_ledger(
            self.ledger,
            dt.datetime(2026, 8, 19, 9, 30, tzinfo=history.MARKET_ZONE),
        )
        proceeds = 120 * 8.45
        realized_pnl = proceeds - 120 * 7.2

        self.assertAlmostEqual(before["cash"], 885.54, places=2)
        self.assertAlmostEqual(after["cash"], 1899.54, places=2)
        self.assertAlmostEqual(after["cash"] - before["cash"], proceeds, places=2)
        self.assertAlmostEqual(realized_pnl, 150.0, places=2)
        self.assertEqual(history.position_quantities(before)["BULL"], 320)
        self.assertEqual(history.position_quantities(after)["BULL"], 200)
        self.assertAlmostEqual(after["positions"]["BULL"]["basis_price"], 7.2, places=2)

    def test_august_twenty_first_bull_call_uses_explicit_basis_allocation(self) -> None:
        before = history.replay_ledger(
            self.ledger,
            dt.datetime(2026, 8, 21, 9, 29, tzinfo=history.MARKET_ZONE),
        )
        after = history.replay_ledger(
            self.ledger,
            dt.datetime(2026, 8, 21, 9, 30, tzinfo=history.MARKET_ZONE),
        )
        buy_cost = 10 * 0.89 * 100
        sale_proceeds = 7.5 * 1.21 * 100

        self.assertEqual(buy_cost, 890.0)
        self.assertAlmostEqual(sale_proceeds, 907.5, places=2)
        self.assertAlmostEqual(before["cash"], 1899.54, places=2)
        self.assertAlmostEqual(after["cash"] - before["cash"], 17.5, places=2)
        self.assertAlmostEqual(after["cash"], 1917.04, places=2)
        self.assertEqual(after["positions"]["BULL_C10_20261218"], {
            "quantity": 2.5,
            "basis_price": 0.0,
        })
        account_call = next(
            position
            for position in self.accounts["accounts"]["ahub"]["positions"]
            if position["instrument"] == "BULL_C10_20261218"
        )
        self.assertEqual(account_call, {
            "instrument": "BULL_C10_20261218",
            "quantity": 2.5,
            "basis_price": 0.0,
        })
        catalog = self.accounts["instruments"]["BULL_C10_20261218"]
        self.assertEqual(catalog["option_symbol"], "BULL261218C00010000")
        self.assertEqual(catalog["expiry"], "2026-12-18")
        self.assertEqual(catalog["manual_mark"], 1.21)
        self.assertEqual(
            catalog["manual_source"],
            "User-supplied Aug 21 same-day sale premium; automatic model fallback",
        )
        self.assertEqual(catalog["attribution_group_id"], "bull-dec2026-call")
        self.assertEqual(catalog["risk_level"], "high")

        before_records = history.realized_trade_records(
            self.ledger,
            dt.datetime(2026, 8, 20, 16, 0, tzinfo=history.MARKET_ZONE),
        )
        self.assertNotIn("BULL_C10_20261218", {record["instrument_id"] for record in before_records})
        records = history.realized_trade_records(
            self.ledger,
            dt.datetime(2026, 8, 21, 16, 0, tzinfo=history.MARKET_ZONE),
        )
        call = next(record for record in records if record["instrument_id"] == "BULL_C10_20261218")
        self.assertEqual(call["event_id"], "2026-08-21-bull-dec2026-call-trades")
        self.assertEqual(call["closed_side"], "long")
        self.assertEqual(call["closed_quantity"], 7.5)
        self.assertEqual(call["fill_count"], 1)
        self.assertEqual(call["average_exit_price"], 1.21)
        self.assertEqual(call["closing_value"], 907.5)
        self.assertEqual(call["closed_basis"], 890.0)
        self.assertEqual(call["fees"], 0.0)
        self.assertEqual(call["realized_pnl"], 17.5)
        self.assertEqual(call["return_pct"], 1.9663)
        self.assertEqual(call["basis_allocation_override"]["remaining_basis_price"], 0.0)

    def test_august_twenty_six_sgov_sale_funds_tmp_purchase(self) -> None:
        before = history.replay_ledger(
            self.ledger,
            history.parse_datetime("2026-08-26T09:05:59-05:00"),
        )
        after = history.replay_ledger(
            self.ledger,
            history.parse_datetime("2026-08-26T09:06:00-05:00"),
        )
        sgov_proceeds = 5 * 100.65
        tmp_cost = 5 * 98.0

        self.assertAlmostEqual(sgov_proceeds, 503.25, places=2)
        self.assertAlmostEqual(tmp_cost, 490.0, places=2)
        self.assertAlmostEqual(before["cash"], 1917.04, places=2)
        self.assertAlmostEqual(after["cash"] - before["cash"], 13.25, places=2)
        self.assertAlmostEqual(after["cash"], 1930.29, places=2)
        self.assertEqual(history.position_quantities(before)["SGOV"], 53)
        self.assertNotIn("TMP", history.position_quantities(before))
        self.assertEqual(history.position_quantities(after)["SGOV"], 48)
        self.assertEqual(history.position_quantities(after)["TMP"], 5)
        self.assertAlmostEqual(
            after["positions"]["SGOV"]["basis_price"],
            before["positions"]["SGOV"]["basis_price"],
            places=10,
        )
        self.assertAlmostEqual(after["positions"]["SGOV"]["basis_price"], 100.5318867925, places=10)
        self.assertEqual(after["positions"]["TMP"]["basis_price"], 98.0)
        event = next(
            item
            for item in self.ledger["events"]
            if item["id"] == "2026-08-26-sgov-to-tmp"
        )
        self.assertEqual(event["effective_at"], "2026-08-26T09:06:00-05:00")
        self.assertNotIn("effective_date", event)
        self.assertNotIn("timing", event)

        account = self.accounts["accounts"]["ahub"]
        self.assertEqual(account["cash"], 1945.03)
        self.assertEqual(len(account["positions"]), 14)
        update_note = next(note for note in account["cash_notes"] if note["date"] == "2026-08-26")
        self.assertEqual(update_note["as_of"], "2026-08-26T09:06:00-05:00")
        self.assertEqual(update_note["instrument"], "TMP")
        self.assertEqual(update_note["amount"], 13.25)
        catalog = self.accounts["instruments"]["TMP"]
        self.assertEqual(catalog["name"], "Tompkins Financial Corporation")
        self.assertEqual(catalog["quote_symbol"], "TMP")
        self.assertEqual(catalog["mark_mode"], "public_delayed")
        self.assertEqual(catalog["asset_class"], "Regional Banking")
        self.assertEqual(catalog["attribution_group_id"], "tmp")
        self.assertEqual(catalog["risk_level"], "medium")
        self.assertEqual(self.ledger["instruments"]["TMP"]["exposure_group_id"], "regional-banking")
        self.assertEqual(
            history.seed_marks(self.ledger)["TMP"],
            (98.0, dt.date(2026, 8, 26)),
        )

        records = history.realized_trade_records(
            self.ledger,
            dt.datetime(2026, 8, 26, 16, 0, tzinfo=history.MARKET_ZONE),
        )
        sgov_records = sorted(
            (record for record in records if record["instrument_id"] == "SGOV"),
            key=lambda record: record["date"],
        )
        self.assertEqual(len(sgov_records), 2)
        latest = sgov_records[-1]
        self.assertEqual(latest["event_id"], "2026-08-26-sgov-to-tmp")
        self.assertEqual(latest["closed_quantity"], 5)
        self.assertEqual(latest["average_exit_price"], 100.65)
        self.assertEqual(latest["closing_value"], 503.25)
        self.assertEqual(latest["closed_basis"], 502.66)
        self.assertEqual(latest["realized_pnl"], 0.59)
        self.assertEqual(latest["return_pct"], 0.1174)
        self.assertEqual(round(sum(record["realized_pnl"] for record in sgov_records), 2), 2.3)
        self.assertEqual(round(sum(record["closed_basis"] for record in sgov_records), 2), 2916.58)
        self.assertNotIn("TMP", {record["instrument_id"] for record in records})

    def test_inconsistent_basis_allocation_override_is_rejected(self) -> None:
        inconsistent = copy.deepcopy(self.ledger)
        event = next(
            item
            for item in inconsistent["events"]
            if item["id"] == "2026-08-21-bull-dec2026-call-trades"
        )
        event["basis_allocation_overrides"][0]["closed_basis"] = 889.0
        with self.assertRaisesRegex(history.HistoryError, "does not conserve basis"):
            history.validate_ledger(inconsistent)

        duplicate = copy.deepcopy(self.ledger)
        event = next(
            item
            for item in duplicate["events"]
            if item["id"] == "2026-08-21-bull-dec2026-call-trades"
        )
        event["basis_allocation_overrides"].append(copy.deepcopy(event["basis_allocation_overrides"][0]))
        with self.assertRaisesRegex(history.HistoryError, "duplicate basis allocation"):
            history.validate_ledger(duplicate)

    def test_realized_trade_leaderboard_uses_average_cost_and_groups_fills(self) -> None:
        records = history.realized_trade_records(
            self.ledger,
            dt.datetime(2026, 8, 19, 16, 0, tzinfo=history.MARKET_ZONE),
        )
        self.assertEqual(
            [record["instrument_id"] for record in records],
            ["INFQ_C10_C17_5_20270115", "BULL", "INFQ_C25_20270115", "SGOV", "TSSI"],
        )
        by_instrument = {record["instrument_id"]: record for record in records}

        vertical = by_instrument["INFQ_C10_C17_5_20270115"]
        self.assertEqual(vertical["fill_count"], 2)
        self.assertEqual(vertical["closed_quantity"], 20)
        self.assertAlmostEqual(vertical["average_exit_price"], 2.4275, places=6)
        self.assertEqual(vertical["closing_value"], 4855.0)
        self.assertEqual(vertical["closed_basis"], 2740.0)
        self.assertEqual(vertical["realized_pnl"], 2115.0)
        self.assertAlmostEqual(vertical["return_pct"], 77.1898, places=4)

        bull = by_instrument["BULL"]
        self.assertEqual(bull["closed_quantity"], 120)
        self.assertEqual(bull["closing_value"], 1014.0)
        self.assertEqual(bull["closed_basis"], 864.0)
        self.assertEqual(bull["realized_pnl"], 150.0)
        self.assertAlmostEqual(bull["return_pct"], 17.3611, places=4)

        call = by_instrument["INFQ_C25_20270115"]
        self.assertEqual(call["realized_pnl"], 105.0)
        self.assertAlmostEqual(call["return_pct"], 46.6667, places=4)
        self.assertEqual(by_instrument["TSSI"]["realized_pnl"], -8.7)
        self.assertAlmostEqual(by_instrument["TSSI"]["return_pct"], -2.9175, places=4)
        self.assertEqual(by_instrument["SGOV"]["fill_count"], 2)
        self.assertEqual(by_instrument["SGOV"]["closed_quantity"], 24)
        self.assertEqual(by_instrument["SGOV"]["closing_value"], 2415.63)
        self.assertEqual(by_instrument["SGOV"]["closed_basis"], 2413.92)
        self.assertEqual(by_instrument["SGOV"]["realized_pnl"], 1.71)
        self.assertAlmostEqual(by_instrument["SGOV"]["return_pct"], 0.0708, places=4)

    def test_realized_trade_math_handles_partial_flip_short_cover_and_fees(self) -> None:
        ledger = copy.deepcopy(self.ledger)
        ledger["formation"] = {
            "as_of": "2026-07-17T09:30:00-04:00",
            "nav": 100.0,
            "cash": 0.0,
            "positions": [{"instrument": "BULL", "quantity": 10, "basis_price": 10.0}],
        }
        ledger["events"] = [
            {
                "id": "partial-long-close",
                "kind": "trade",
                "effective_date": "2026-07-20",
                "timing": "before_close",
                "legs": [{"instrument": "BULL", "signed_quantity": -4, "price": 12.0, "fees": 4.0}],
            },
            {
                "id": "long-to-short-flip",
                "kind": "trade",
                "effective_date": "2026-07-21",
                "timing": "before_close",
                "legs": [{"instrument": "BULL", "signed_quantity": -10, "price": 8.0, "fees": 10.0}],
            },
            {
                "id": "partial-short-cover",
                "kind": "trade",
                "effective_date": "2026-07-22",
                "timing": "before_close",
                "legs": [{"instrument": "BULL", "signed_quantity": 2, "price": 5.0, "fees": 2.0}],
            },
        ]
        through = dt.datetime(2026, 7, 22, 16, 0, tzinfo=history.MARKET_ZONE)
        records = {record["event_id"]: record for record in history.realized_trade_records(ledger, through)}

        self.assertEqual(records["partial-long-close"]["realized_pnl"], 4.0)
        self.assertEqual(records["long-to-short-flip"]["closed_quantity"], 6)
        self.assertEqual(records["long-to-short-flip"]["fees"], 6.0)
        self.assertEqual(records["long-to-short-flip"]["realized_pnl"], -18.0)
        self.assertEqual(records["partial-short-cover"]["closed_side"], "short")
        self.assertEqual(records["partial-short-cover"]["realized_pnl"], 2.0)

        ending = history.replay_ledger(ledger, through)
        self.assertEqual(ending["positions"]["BULL"]["quantity"], -2)
        self.assertAlmostEqual(ending["positions"]["BULL"]["basis_price"], 7.0, places=6)

    def test_date_only_august_four_event_is_applied_before_close(self) -> None:
        before = history.replay_ledger(
            self.ledger,
            dt.datetime(2026, 8, 4, 9, 29, tzinfo=history.MARKET_ZONE),
        )
        after = history.replay_ledger(
            self.ledger,
            dt.datetime(2026, 8, 4, 9, 30, tzinfo=history.MARKET_ZONE),
        )
        self.assertNotIn("BULL", history.position_quantities(before))
        self.assertEqual(history.position_quantities(after)["BULL"], 320)
        self.assertEqual(history.position_quantities(after)["SGOV"], 2)
        self.assertAlmostEqual(after["cash"] - before["cash"], 111.63, places=2)

    def test_history_is_calendar_contiguous_and_carries_weekends(self) -> None:
        payload = history.build_history(
            self.ledger,
            as_of=self.after_close(dt.date(2026, 8, 13)),
            fetcher=self.fetcher,
        )
        self.assertEqual(payload["accounts"]["ahub"]["currency"], "USD")
        self.assertEqual(payload["accounts"]["ahub"]["opening_nav"], 9900.0)
        points = self.points(payload)
        self.assertEqual(points[0]["date"], "2026-07-17")
        self.assertEqual(points[0]["value"], 9900.0)
        self.assertEqual(points[-1]["date"], "2026-08-13")
        self.assertEqual(len(points), 28)
        by_date = {point["date"]: point for point in points}
        for saturday, friday in (
            ("2026-07-18", "2026-07-17"),
            ("2026-07-25", "2026-07-24"),
            ("2026-08-01", "2026-07-31"),
            ("2026-08-08", "2026-08-07"),
        ):
            sunday = (dt.date.fromisoformat(saturday) + dt.timedelta(days=1)).isoformat()
            self.assertEqual(by_date[saturday]["kind"], "carry_forward")
            self.assertEqual(by_date[saturday]["value"], by_date[friday]["value"])
            self.assertEqual(by_date[sunday]["value"], by_date[friday]["value"])
            self.assertEqual(by_date[sunday]["positions_value"], by_date[friday]["positions_value"])
        self.assertEqual(payload["generated_at"], "2026-08-13T20:30:00Z")

    def test_exposure_history_is_gross_percent_complete_and_carries_weekends_exactly(self) -> None:
        payload = history.build_history(
            self.ledger,
            as_of=self.after_all_daily_closes(dt.date(2026, 8, 19)),
            fetcher=self.fetcher,
        )
        exposure = payload["accounts"]["ahub"]["analytics"]["exposure_history"]
        self.assertEqual(exposure["basis"], "gross_marked_value")
        self.assertEqual(exposure["units"], "percent_of_gross_marked_value")
        category_ids = [category["id"] for category in exposure["categories"]]
        self.assertEqual(category_ids, [
            "cash-cash-equivalents",
            "financial-technology",
            "options",
            "technology",
            "real-estate",
            "precious-metals",
            "index-funds",
            "consumer",
        ])
        self.assertEqual(len({category["color"] for category in exposure["categories"]}), len(category_ids))
        points = exposure["points"]
        self.assertEqual([point["date"] for point in points], [point["date"] for point in self.points(payload)])
        for point in points:
            self.assertAlmostEqual(sum(value["percent"] for value in point["values"].values()), 100.0, places=6)
            self.assertGreater(point["gross_exposure"], 0)

        by_date = {point["date"]: point for point in points}
        formation = by_date["2026-07-17"]
        self.assertEqual(formation["gross_exposure"], 10532.0)
        self.assertEqual(formation["values"]["cash-cash-equivalents"]["value"], 3004.91)
        self.assertEqual(formation["values"]["options"]["value"], 3356.0)
        self.assertEqual(formation["values"]["technology"]["value"], 1589.05)
        self.assertEqual(formation["values"]["financial-technology"]["value"], 0.0)
        self.assertNotEqual(
            by_date["2026-08-04"]["values"]["financial-technology"]["percent"],
            by_date["2026-08-03"]["values"]["financial-technology"]["percent"],
        )
        for weekend_day, prior_session in (("2026-07-18", "2026-07-17"), ("2026-08-16", "2026-08-14")):
            self.assertEqual(by_date[weekend_day]["kind"], "carry_forward")
            self.assertEqual(by_date[weekend_day]["gross_exposure"], by_date[prior_session]["gross_exposure"])
            self.assertEqual(by_date[weekend_day]["values"], by_date[prior_session]["values"])

    def test_risk_history_uses_the_same_completed_nightly_gross_exposure(self) -> None:
        payload = history.build_history(
            self.ledger,
            as_of=self.after_all_daily_closes(dt.date(2026, 8, 19)),
            fetcher=self.fetcher,
        )
        analytics = payload["accounts"]["ahub"]["analytics"]
        exposure = analytics["exposure_history"]
        risk = analytics["risk_history"]
        self.assertEqual(risk["basis"], "gross_marked_value")
        self.assertEqual(risk["units"], "percent_of_gross_marked_value")
        self.assertEqual([category["id"] for category in risk["categories"]], ["low", "medium", "high"])
        self.assertEqual([category["label"] for category in risk["categories"]], ["Low", "Medium", "High"])
        self.assertEqual(len({category["color"] for category in risk["categories"]}), 3)
        self.assertEqual([point["date"] for point in risk["points"]], [point["date"] for point in self.points(payload)])
        self.assertEqual(len(risk["points"]), len(exposure["points"]))

        for risk_point, exposure_point in zip(risk["points"], exposure["points"]):
            with self.subTest(date=risk_point["date"]):
                self.assertEqual(
                    (risk_point["date"], risk_point["kind"], risk_point["source_date"], risk_point["quality"]),
                    (exposure_point["date"], exposure_point["kind"], exposure_point["source_date"], exposure_point["quality"]),
                )
                self.assertEqual(risk_point["gross_exposure"], exposure_point["gross_exposure"])
                self.assertEqual(set(risk_point["values"]), {"low", "medium", "high"})
                self.assertAlmostEqual(sum(value["value"] for value in risk_point["values"].values()), risk_point["gross_exposure"], places=2)
                self.assertAlmostEqual(sum(value["percent"] for value in risk_point["values"].values()), 100.0, places=6)

        by_date = {point["date"]: point for point in risk["points"]}
        formation = by_date["2026-07-17"]
        self.assertEqual(formation["gross_exposure"], 10532.0)
        self.assertEqual(formation["values"]["low"]["value"], 3459.56)
        self.assertEqual(formation["values"]["medium"]["value"], 2924.25)
        self.assertEqual(formation["values"]["high"]["value"], 4148.19)
        self.assertNotEqual(by_date["2026-08-04"]["values"], by_date["2026-08-03"]["values"])
        for weekend_day, prior_session in (("2026-07-18", "2026-07-17"), ("2026-08-16", "2026-08-14")):
            self.assertEqual(by_date[weekend_day]["kind"], "carry_forward")
            self.assertEqual(by_date[weekend_day]["gross_exposure"], by_date[prior_session]["gross_exposure"])
            self.assertEqual(by_date[weekend_day]["values"], by_date[prior_session]["values"])

    def test_positive_cash_and_long_sgov_are_low_risk(self) -> None:
        point = history.risk_point(
            self.ledger,
            {"cash": 50.0, "positions": {"SGOV": {"quantity": 1.0, "basis_price": 100.0}}},
            {"SGOV": 101.0},
            day=dt.date(2026, 8, 19),
            kind="session_close",
            source_date=dt.date(2026, 8, 19),
            quality="complete",
        )
        self.assertEqual(point["gross_exposure"], 151.0)
        self.assertEqual(point["values"]["low"], {"value": 151.0, "percent": 100.0})
        self.assertEqual(point["values"]["medium"], {"value": 0.0, "percent": 0.0})
        self.assertEqual(point["values"]["high"], {"value": 0.0, "percent": 0.0})

    def test_contributors_reconcile_and_keep_unattributed_adjustment_separate(self) -> None:
        payload = history.build_history(
            self.ledger,
            as_of=self.after_all_daily_closes(dt.date(2026, 8, 19)),
            fetcher=self.fetcher,
        )
        analytics = payload["accounts"]["ahub"]["analytics"]
        self.assertEqual(analytics["as_of"], "2026-08-19")
        self.assertEqual(analytics["schema_version"], 1)
        self.assertEqual(analytics["unattributed_pnl"]["total"], 24.0)
        self.assertEqual([event["id"] for event in analytics["unattributed_pnl"]["events"]], ["2026-07-20-cash-adjustment"])
        reconciliation = analytics["reconciliation"]
        self.assertEqual(reconciliation["external_flows"], 0.0)
        self.assertEqual(reconciliation["residual"], 0.0)
        self.assertAlmostEqual(
            reconciliation["attributed_pnl"] + reconciliation["unattributed_pnl"],
            reconciliation["nav_change"],
            places=2,
        )
        contributors = {contributor["id"]: contributor for contributor in analytics["contributors"]}
        self.assertEqual(
            {group_id: item["risk_level"] for group_id, item in contributors.items()},
            PRE_AUGUST_21_RISK_LEVELS,
        )
        self.assertEqual(contributors["infq"]["realized_pnl"], 2220.0)
        self.assertEqual(contributors["bull"]["realized_pnl"], 150.0)
        self.assertEqual(contributors["ivr"]["income"], 12.0)
        self.assertEqual(contributors["sgov"]["income"], 0.61)
        self.assertEqual(contributors["infq"]["tracked_basis"], 3040.0)
        self.assertEqual(contributors["bull"]["tracked_basis"], 2620.0)

    def test_analytics_excludes_ledger_events_after_last_completed_session(self) -> None:
        payload = history.build_history(
            self.ledger,
            as_of=self.after_all_daily_closes(dt.date(2026, 8, 18)),
            fetcher=self.fetcher,
        )
        analytics = payload["accounts"]["ahub"]["analytics"]
        self.assertEqual(analytics["as_of"], "2026-08-18")
        self.assertNotIn("BULL", {trade["instrument_id"] for trade in analytics["realized_trades"]})
        self.assertEqual(analytics["exposure_history"]["points"][-1]["date"], "2026-08-18")
        self.assertEqual(analytics["risk_history"]["points"][-1]["date"], "2026-08-18")
        self.assertEqual(analytics["reconciliation"]["residual"], 0.0)

    def test_external_cash_flow_is_reconciled_but_not_presented_as_pnl(self) -> None:
        ledger = copy.deepcopy(self.ledger)
        ledger["events"].append({
            "id": "2026-08-18-test-contribution",
            "kind": "cash_adjustment",
            "effective_date": "2026-08-18",
            "timing": "before_close",
            "amount": 100.0,
            "classification": "contribution",
            "note": "Synthetic unit-test flow.",
        })
        ledger["expected_current_snapshot"]["cash"] += 100.0
        payload = history.build_history(
            ledger,
            as_of=self.after_all_daily_closes(dt.date(2026, 8, 19)),
            fetcher=self.fetcher,
        )
        analytics = payload["accounts"]["ahub"]["analytics"]
        self.assertEqual(analytics["reconciliation"]["external_flows"], 100.0)
        self.assertEqual(analytics["unattributed_pnl"]["total"], 24.0)
        self.assertEqual(analytics["reconciliation"]["residual"], 0.0)
        self.assertAlmostEqual(
            analytics["reconciliation"]["nav_change"],
            analytics["reconciliation"]["explained_change"] + 100.0,
            places=2,
        )

    def test_negative_cash_and_short_cash_equivalent_are_financing_exposure(self) -> None:
        categories = history.exposure_categories(self.ledger, include_financing=True)
        state = {"cash": -100.0, "positions": {"SGOV": {"quantity": -1.0, "basis_price": 100.0}}}
        market_values = {"SGOV": -101.0}
        point = history.exposure_point(
            self.ledger,
            state,
            market_values,
            categories,
            day=dt.date(2026, 8, 19),
            kind="session_close",
            source_date=dt.date(2026, 8, 19),
            quality="complete",
        )
        self.assertEqual(point["gross_exposure"], 201.0)
        self.assertEqual(point["values"]["financing"], {"value": 201.0, "percent": 100.0})
        self.assertEqual(point["values"]["cash-cash-equivalents"], {"value": 0.0, "percent": 0.0})

        risk = history.risk_point(
            self.ledger,
            state,
            market_values,
            day=dt.date(2026, 8, 19),
            kind="session_close",
            source_date=dt.date(2026, 8, 19),
            quality="complete",
        )
        self.assertEqual(risk["gross_exposure"], point["gross_exposure"])
        self.assertEqual(risk["values"]["low"], {"value": 0.0, "percent": 0.0})
        self.assertEqual(risk["values"]["medium"], {"value": 0.0, "percent": 0.0})
        self.assertEqual(risk["values"]["high"], {"value": 201.0, "percent": 100.0})

    def test_public_comparisons_are_normalized_to_formation_nav(self) -> None:
        payload = history.build_history(
            self.ledger,
            as_of=self.after_all_daily_closes(dt.date(2026, 8, 14)),
            fetcher=self.fetcher,
        )
        raw_series = payload["accounts"]["ahub"]["comparisons"]
        self.assertEqual([series["id"] for series in raw_series], ["spy", "gold-gld", "btc-usd"])
        comparisons = self.comparisons(payload)
        self.assertEqual(payload["benchmark_coverage_status"], "complete")
        self.assertEqual(payload["benchmark_failures"], [])

        for comparison_id, expected_basis in (
            ("spy", "adjusted_close"),
            ("gold-gld", "adjusted_close"),
            ("btc-usd", "close"),
        ):
            series = comparisons[comparison_id]
            self.assertEqual(series["price_basis"], expected_basis)
            self.assertEqual(series["baseline_date"], "2026-07-17")
            self.assertEqual(series["baseline_value"], 9900.0)
            self.assertEqual(series["units"], "normalized_account_value_usd")
            self.assertEqual(series["status"], "ready")
            self.assertEqual(series["points"][0]["date"], "2026-07-17")
            self.assertEqual(series["points"][0]["value"], 9900.0)
            self.assertEqual(len(series["points"]), len(self.points(payload)))

        spy = comparisons["spy"]
        spy_by_date = {point["date"]: point for point in spy["points"]}
        expected = 9900.0 * self.prices["SPY"][dt.date(2026, 7, 20)] / self.prices["SPY"][dt.date(2026, 7, 17)]
        self.assertAlmostEqual(spy_by_date["2026-07-20"]["value"], round(expected, 2), places=2)
        self.assertAlmostEqual(spy["baseline_price"], self.prices["SPY"][dt.date(2026, 7, 17)], places=6)

    def test_equity_comparisons_carry_weekends_while_bitcoin_moves(self) -> None:
        payload = history.build_history(
            self.ledger,
            as_of=self.after_all_daily_closes(dt.date(2026, 7, 20)),
            fetcher=self.fetcher,
        )
        comparisons = self.comparisons(payload)
        for comparison_id in ("spy", "gold-gld"):
            by_date = {point["date"]: point for point in comparisons[comparison_id]["points"]}
            self.assertEqual(by_date["2026-07-18"]["value"], by_date["2026-07-17"]["value"])
            self.assertEqual(by_date["2026-07-19"]["value"], by_date["2026-07-17"]["value"])
            self.assertEqual(by_date["2026-07-18"]["quality"], "carry_forward")
        bitcoin = {point["date"]: point for point in comparisons["btc-usd"]["points"]}
        self.assertNotEqual(bitcoin["2026-07-18"]["value"], bitcoin["2026-07-17"]["value"])
        self.assertNotEqual(bitcoin["2026-07-19"]["value"], bitcoin["2026-07-18"]["value"])
        self.assertEqual(bitcoin["2026-07-18"]["kind"], "daily_close")

    def test_comparison_fallback_is_retained_and_extended_without_blocking_nav(self) -> None:
        fallback = history.build_history(
            self.ledger,
            as_of=self.after_all_daily_closes(dt.date(2026, 8, 12)),
            fetcher=self.fetcher,
        )

        def failing_benchmark_fetcher(_symbol: str, _start: dt.date, _end: dt.date) -> dict[dt.date, float]:
            raise TimeoutError("benchmark source unavailable")

        payload = history.build_history(
            self.ledger,
            as_of=self.after_all_daily_closes(dt.date(2026, 8, 13)),
            fetcher=self.fetcher,
            benchmark_fetcher=failing_benchmark_fetcher,
            fallback=fallback,
        )
        self.assertEqual(self.points(payload)[-1]["date"], "2026-08-13")
        self.assertEqual(payload["benchmark_coverage_status"], "degraded")
        self.assertEqual(len(payload["benchmark_failures"]), 3)
        for series in payload["accounts"]["ahub"]["comparisons"]:
            self.assertEqual(series["status"], "degraded")
            self.assertEqual(series["points"][-1]["date"], "2026-08-13")
            self.assertEqual(series["points"][-1]["quality"], "stale_fallback")
            self.assertEqual(series["points"][-1]["value"], series["points"][-2]["value"])

    def test_missing_formation_baseline_only_makes_that_comparison_unavailable(self) -> None:
        def missing_gold_baseline(symbol: str, start: dt.date, end: dt.date) -> dict[dt.date, float]:
            result = self.fetcher(symbol, start, end)
            if symbol == "GLD":
                result.pop(dt.date(2026, 7, 17), None)
            return result

        payload = history.build_history(
            self.ledger,
            as_of=self.after_all_daily_closes(dt.date(2026, 7, 20)),
            fetcher=self.fetcher,
            benchmark_fetcher=missing_gold_baseline,
        )
        comparisons = self.comparisons(payload)
        self.assertEqual(self.points(payload)[-1]["date"], "2026-07-20")
        self.assertEqual(comparisons["spy"]["status"], "ready")
        self.assertEqual(comparisons["btc-usd"]["status"], "ready")
        self.assertEqual(comparisons["gold-gld"]["status"], "unavailable")
        self.assertEqual(comparisons["gold-gld"]["points"], [])
        self.assertIn("gold-gld: missing formation baseline", payload["benchmark_failures"])

    def test_yahoo_parser_selects_adjusted_or_raw_and_maps_btc_to_utc_date(self) -> None:
        market_timestamp = int(dt.datetime(2026, 7, 17, 13, 30, tzinfo=dt.timezone.utc).timestamp())
        result = {
            "timestamp": [market_timestamp],
            "indicators": {
                "quote": [{"close": [101.25]}],
                "adjclose": [{"adjclose": [99.75]}],
            },
        }
        raw = history.parse_yahoo_daily_result(
            result,
            symbol="SPY",
            start=dt.date(2026, 7, 17),
            end=dt.date(2026, 7, 17),
        )
        adjusted = history.parse_yahoo_daily_result(
            result,
            symbol="SPY",
            start=dt.date(2026, 7, 17),
            end=dt.date(2026, 7, 17),
            adjusted=True,
        )
        self.assertEqual(raw[dt.date(2026, 7, 17)], 101.25)
        self.assertEqual(adjusted[dt.date(2026, 7, 17)], 99.75)

        utc_midnight = int(dt.datetime(2026, 7, 17, tzinfo=dt.timezone.utc).timestamp())
        btc_result = {
            "timestamp": [utc_midnight],
            "indicators": {"quote": [{"close": [60000.0]}]},
        }
        utc_prices = history.parse_yahoo_daily_result(
            btc_result,
            symbol="BTC-USD",
            start=dt.date(2026, 7, 17),
            end=dt.date(2026, 7, 17),
            utc_dates=True,
        )
        market_prices = history.parse_yahoo_daily_result(
            btc_result,
            symbol="BTC-USD",
            start=dt.date(2026, 7, 16),
            end=dt.date(2026, 7, 16),
        )
        self.assertIn(dt.date(2026, 7, 17), utc_prices)
        self.assertIn(dt.date(2026, 7, 16), market_prices)

    def test_bitcoin_open_utc_candle_is_not_recorded_as_a_daily_close(self) -> None:
        payload = history.build_history(
            self.ledger,
            as_of=self.after_close(dt.date(2026, 7, 20)),
            fetcher=self.fetcher,
        )
        bitcoin = self.comparisons(payload)["btc-usd"]
        by_date = {point["date"]: point for point in bitcoin["points"]}
        self.assertEqual(by_date["2026-07-20"]["source_date"], "2026-07-19")
        self.assertEqual(by_date["2026-07-20"]["quality"], "stale_fallback")
        self.assertEqual(bitcoin["status"], "degraded")

    def test_trailing_weekend_nights_carry_the_last_completed_session(self) -> None:
        payload = history.build_history(
            self.ledger,
            as_of=self.after_close(dt.date(2026, 8, 16)),
            fetcher=self.fetcher,
        )
        points = self.points(payload)
        by_date = {point["date"]: point for point in points}
        self.assertEqual(payload["last_completed_session"], "2026-08-14")
        self.assertEqual(points[-1]["date"], "2026-08-16")
        self.assertEqual(by_date["2026-08-14"]["kind"], "session_close")
        self.assertAlmostEqual(by_date["2026-08-14"]["cash"], 872.93, places=2)
        self.assertEqual(by_date["2026-08-14"]["position_count"], 12)
        self.assertEqual(by_date["2026-08-15"]["value"], by_date["2026-08-14"]["value"])
        self.assertEqual(by_date["2026-08-16"]["value"], by_date["2026-08-14"]["value"])

    def test_no_current_incomplete_session_or_early_august_thirteen_trade(self) -> None:
        before_close = dt.datetime(2026, 8, 13, 15, 59, tzinfo=history.MARKET_ZONE)
        payload = history.build_history(self.ledger, as_of=before_close, fetcher=self.fetcher)
        self.assertEqual(payload["last_completed_session"], "2026-08-12")
        points = self.points(payload)
        self.assertEqual(points[-1]["date"], "2026-08-12")
        self.assertNotIn("2026-08-13", {point["date"] for point in points})
        self.assertAlmostEqual(points[-1]["cash"], 525.46, places=2)

    def test_august_thirteen_close_uses_updated_holdings_and_cash(self) -> None:
        payload = history.build_history(
            self.ledger,
            as_of=self.after_close(dt.date(2026, 8, 13)),
            fetcher=self.fetcher,
        )
        closing = self.points(payload)[-1]
        self.assertEqual(closing["kind"], "session_close")
        self.assertAlmostEqual(closing["cash"], 583.43, places=2)
        self.assertEqual(closing["position_count"], 13)
        self.assertTrue(math_is_positive(closing["value"]))

    def test_august_fourteen_close_uses_tssi_sale_and_cash_proceeds(self) -> None:
        payload = history.build_history(
            self.ledger,
            as_of=self.after_close(dt.date(2026, 8, 14)),
            fetcher=self.fetcher,
        )
        closing = self.points(payload)[-1]
        self.assertEqual(closing["date"], "2026-08-14")
        self.assertEqual(closing["kind"], "session_close")
        self.assertAlmostEqual(closing["cash"], 872.93, places=2)
        self.assertEqual(closing["position_count"], 12)
        self.assertNotIn("TSSI", closing["forward_filled_symbols"])
        self.assertTrue(math_is_positive(closing["value"]))

    def test_august_eighteenth_close_uses_dividend_cash(self) -> None:
        payload = history.build_history(
            self.ledger,
            as_of=self.after_all_daily_closes(dt.date(2026, 8, 18)),
            fetcher=self.fetcher,
        )
        closing = self.points(payload)[-1]

        self.assertEqual(closing["date"], "2026-08-18")
        self.assertEqual(closing["kind"], "session_close")
        self.assertAlmostEqual(closing["cash"], 885.54, places=2)
        self.assertAlmostEqual(closing["value"] - closing["positions_value"], 885.54, places=2)
        self.assertEqual(closing["position_count"], 12)
        self.assertNotIn("TSSI", closing["forward_filled_symbols"])

    def test_august_nineteenth_close_uses_bull_sale_and_cash_proceeds(self) -> None:
        payload = history.build_history(
            self.ledger,
            as_of=self.after_all_daily_closes(dt.date(2026, 8, 19)),
            fetcher=self.fetcher,
        )
        closing = self.points(payload)[-1]

        self.assertEqual(closing["date"], "2026-08-19")
        self.assertEqual(closing["kind"], "session_close")
        self.assertAlmostEqual(closing["cash"], 1899.54, places=2)
        self.assertAlmostEqual(closing["value"] - closing["positions_value"], 1899.54, places=2)
        self.assertEqual(closing["position_count"], 12)
        self.assertNotIn("BULL", closing["forward_filled_symbols"])

    def test_august_twentieth_completed_history_remains_pre_trade(self) -> None:
        payload = history.build_history(
            self.ledger,
            as_of=self.after_all_daily_closes(dt.date(2026, 8, 20)),
            fetcher=self.fetcher,
        )
        closing = self.points(payload)[-1]
        analytics = payload["accounts"]["ahub"]["analytics"]

        self.assertEqual(payload["last_completed_session"], "2026-08-20")
        self.assertEqual(closing["date"], "2026-08-20")
        self.assertEqual(closing["kind"], "session_close")
        self.assertAlmostEqual(closing["cash"], 1899.54, places=2)
        self.assertEqual(closing["position_count"], 12)
        self.assertEqual(analytics["as_of"], "2026-08-20")
        self.assertNotIn(
            "BULL_C10_20261218",
            {trade["instrument_id"] for trade in analytics["realized_trades"]},
        )
        self.assertNotIn(
            "bull-dec2026-call",
            {contributor["id"] for contributor in analytics["contributors"]},
        )

    def test_august_twenty_first_close_includes_call_analytics_and_high_risk(self) -> None:
        payload = history.build_history(
            self.ledger,
            as_of=self.after_all_daily_closes(dt.date(2026, 8, 21)),
            fetcher=self.fetcher,
        )
        closing = self.points(payload)[-1]
        analytics = payload["accounts"]["ahub"]["analytics"]

        self.assertEqual(payload["last_completed_session"], "2026-08-21")
        self.assertEqual(closing["date"], "2026-08-21")
        self.assertAlmostEqual(closing["cash"], 1917.04, places=2)
        self.assertEqual(closing["position_count"], 13)
        call_trade = next(
            trade
            for trade in analytics["realized_trades"]
            if trade["instrument_id"] == "BULL_C10_20261218"
        )
        self.assertEqual(call_trade["realized_pnl"], 17.5)
        self.assertEqual(call_trade["closed_basis"], 890.0)
        self.assertEqual(call_trade["return_pct"], 1.9663)
        contributor = next(
            item
            for item in analytics["contributors"]
            if item["id"] == "bull-dec2026-call"
        )
        self.assertEqual(contributor["risk_level"], "high")
        self.assertEqual(contributor["instrument_ids"], ["BULL_C10_20261218"])
        self.assertEqual(contributor["realized_pnl"], 17.5)
        self.assertEqual(contributor["tracked_basis"], 890.0)
        self.assertEqual(contributor["unrealized_pnl"], contributor["market_value"])
        self.assertEqual(
            contributor["total_pnl"],
            round(contributor["realized_pnl"] + contributor["unrealized_pnl"], 2),
        )
        exposure_latest = analytics["exposure_history"]["points"][-1]
        risk_latest = analytics["risk_history"]["points"][-1]
        self.assertEqual(risk_latest["gross_exposure"], exposure_latest["gross_exposure"])
        self.assertAlmostEqual(
            sum(value["percent"] for value in risk_latest["values"].values()),
            100.0,
            places=6,
        )

    def test_tmp_history_seed_starts_on_acquisition_and_covers_offline_close(self) -> None:
        def tmp_unavailable_fetcher(symbol: str, start: dt.date, end: dt.date) -> dict[dt.date, float]:
            if symbol == "TMP":
                raise TimeoutError("TMP source unavailable")
            return self.fetcher(symbol, start, end)

        before = history.build_history(
            self.ledger,
            as_of=self.after_all_daily_closes(dt.date(2026, 8, 25)),
            fetcher=tmp_unavailable_fetcher,
        )
        self.assertEqual(before["last_completed_session"], "2026-08-25")
        self.assertNotIn(
            "tmp",
            {contributor["id"] for contributor in before["accounts"]["ahub"]["analytics"]["contributors"]},
        )
        pre_trade_records = before["accounts"]["ahub"]["analytics"]["realized_trades"]
        self.assertNotIn(
            "2026-08-26-sgov-to-tmp",
            {record["event_id"] for record in pre_trade_records},
        )
        self.assertEqual(
            len([record for record in pre_trade_records if record["instrument_id"] == "SGOV"]),
            1,
        )
        before_state = history.replay_ledger(
            self.ledger,
            dt.datetime(2026, 8, 25, 16, 0, tzinfo=history.MARKET_ZONE),
        )
        self.assertNotIn("TMP", history.active_symbols(self.ledger, before_state))

        payload = history.build_history(
            self.ledger,
            as_of=self.after_all_daily_closes(dt.date(2026, 8, 26)),
            fetcher=tmp_unavailable_fetcher,
        )
        closing = self.points(payload)[-1]
        analytics = payload["accounts"]["ahub"]["analytics"]
        self.assertEqual(payload["last_completed_session"], "2026-08-26")
        self.assertEqual(payload["coverage_status"], "degraded")
        self.assertEqual(closing["date"], "2026-08-26")
        self.assertEqual(closing["quality"], "degraded")
        self.assertIn("TMP", closing["forward_filled_symbols"])
        self.assertEqual(closing["cash"], 1930.29)
        self.assertEqual(closing["position_count"], 14)
        contributor = next(item for item in analytics["contributors"] if item["id"] == "tmp")
        self.assertEqual(contributor["risk_level"], "medium")
        self.assertEqual(contributor["instrument_ids"], ["TMP"])
        self.assertEqual(contributor["market_value"], 490.0)
        self.assertEqual(contributor["tracked_basis"], 490.0)
        self.assertEqual(contributor["unrealized_pnl"], 0.0)
        sgov = next(item for item in analytics["contributors"] if item["id"] == "sgov")
        self.assertEqual(sgov["realized_pnl"], 2.3)
        self.assertEqual(sgov["tracked_basis"], 7742.11)
        exposure = analytics["exposure_history"]
        category = next(item for item in exposure["categories"] if item["id"] == "regional-banking")
        self.assertEqual(category["label"], "Regional Banking")
        self.assertEqual(exposure["points"][-1]["values"]["regional-banking"]["value"], 490.0)
        self.assertEqual(
            analytics["risk_history"]["points"][-1]["gross_exposure"],
            exposure["points"][-1]["gross_exposure"],
        )

    def test_missing_individual_mark_is_forward_filled_and_degraded(self) -> None:
        missing_day = dt.date(2026, 8, 6)

        def incomplete_fetcher(symbol: str, start: dt.date, end: dt.date) -> dict[dt.date, float]:
            result = self.fetcher(symbol, start, end)
            if symbol == "IBM":
                result.pop(missing_day, None)
            return result

        payload = history.build_history(
            self.ledger,
            as_of=self.after_close(dt.date(2026, 8, 6)),
            fetcher=incomplete_fetcher,
        )
        closing = self.points(payload)[-1]
        self.assertEqual(closing["quality"], "degraded")
        self.assertIn("IBM", closing["forward_filled_symbols"])
        self.assertTrue(math_is_positive(closing["value"]))
        risk_closing = payload["accounts"]["ahub"]["analytics"]["risk_history"]["points"][-1]
        self.assertEqual(risk_closing["date"], closing["date"])
        self.assertEqual(risk_closing["quality"], "degraded")

    def test_missing_anchor_session_is_treated_as_holiday_carry_forward(self) -> None:
        omitted_day = dt.date(2026, 8, 6)

        def holiday_fetcher(symbol: str, start: dt.date, end: dt.date) -> dict[dt.date, float]:
            result = self.fetcher(symbol, start, end)
            result.pop(omitted_day, None)
            return result

        payload = history.build_history(
            self.ledger,
            as_of=self.after_close(dt.date(2026, 8, 7)),
            fetcher=holiday_fetcher,
        )
        by_date = {point["date"]: point for point in self.points(payload)}
        self.assertEqual(by_date["2026-08-06"]["kind"], "carry_forward")
        self.assertEqual(by_date["2026-08-06"]["value"], by_date["2026-08-05"]["value"])
        self.assertEqual(by_date["2026-08-07"]["kind"], "session_close")

    def test_fallback_preserves_history_when_every_fetch_fails(self) -> None:
        as_of = self.after_close(dt.date(2026, 8, 12))
        original = history.build_history(self.ledger, as_of=as_of, fetcher=self.fetcher)

        def failing_fetcher(_symbol: str, _start: dt.date, _end: dt.date) -> dict[dt.date, float]:
            raise TimeoutError("offline test")

        recovered = history.build_history(
            self.ledger,
            as_of=as_of,
            fetcher=failing_fetcher,
            fallback=original,
        )
        self.assertEqual(
            [(point["date"], point["value"]) for point in self.points(recovered)],
            [(point["date"], point["value"]) for point in self.points(original)],
        )
        self.assertEqual(recovered["last_completed_session"], "2026-08-12")
        self.assertEqual(len(recovered["failures"]), len(self.symbols))
        self.assertEqual(recovered["coverage_status"], "degraded")
        self.assertEqual(
            recovered["accounts"]["ahub"]["analytics"]["contributors"],
            original["accounts"]["ahub"]["analytics"]["contributors"],
        )
        self.assertEqual(
            recovered["accounts"]["ahub"]["analytics"]["exposure_history"]["points"],
            original["accounts"]["ahub"]["analytics"]["exposure_history"]["points"],
        )
        self.assertEqual(
            recovered["accounts"]["ahub"]["analytics"]["risk_history"]["points"],
            original["accounts"]["ahub"]["analytics"]["risk_history"]["points"],
        )

    def test_fallback_marks_extend_a_new_session_with_degraded_values(self) -> None:
        fallback = history.build_history(
            self.ledger,
            as_of=self.after_close(dt.date(2026, 8, 12)),
            fetcher=self.fetcher,
        )

        def anchor_only_fetcher(symbol: str, start: dt.date, end: dt.date) -> dict[dt.date, float]:
            if symbol == "SPY":
                return self.fetcher(symbol, start, end)
            raise TimeoutError("individual source unavailable")

        payload = history.build_history(
            self.ledger,
            as_of=self.after_close(dt.date(2026, 8, 13)),
            fetcher=anchor_only_fetcher,
            fallback=fallback,
        )
        closing = self.points(payload)[-1]
        self.assertEqual(closing["date"], "2026-08-13")
        self.assertEqual(closing["quality"], "degraded")
        self.assertNotIn("SPY", closing["forward_filled_symbols"])
        self.assertIn("IBM", closing["forward_filled_symbols"])
        self.assertTrue(math_is_positive(closing["value"]))
        risk_closing = payload["accounts"]["ahub"]["analytics"]["risk_history"]["points"][-1]
        self.assertEqual(risk_closing["date"], "2026-08-13")
        self.assertEqual(risk_closing["quality"], "degraded")

    def test_no_network_and_no_fallback_cannot_invent_session_calendar(self) -> None:
        def failing_fetcher(_symbol: str, _start: dt.date, _end: dt.date) -> dict[dt.date, float]:
            raise TimeoutError("offline test")

        with self.assertRaisesRegex(history.HistoryError, "No completed SPY session"):
            history.build_history(
                self.ledger,
                as_of=self.after_close(dt.date(2026, 8, 13)),
                fetcher=failing_fetcher,
            )

    def test_packaged_august_twenty_fifth_analytics_match_published_fixtures(self) -> None:
        payload = history.load_json(REPOSITORY / "data/demo-portfolio-history.json")
        analytics = payload["accounts"]["ahub"].get("analytics")
        self.assertIsInstance(analytics, dict, "Regenerate the packaged history after analytics changes")
        self.assertEqual(payload["last_completed_session"], "2026-08-25")
        self.assertEqual(analytics["as_of"], "2026-08-25")

        trades = {trade["instrument_id"]: trade for trade in analytics["realized_trades"]}
        self.assertEqual(trades["INFQ_C10_C17_5_20270115"]["realized_pnl"], 2115.0)
        self.assertEqual(trades["BULL"]["realized_pnl"], 150.0)
        self.assertEqual(trades["INFQ_C25_20270115"]["realized_pnl"], 105.0)
        self.assertEqual(trades["TSSI"]["realized_pnl"], -8.7)
        self.assertEqual(trades["SGOV"]["realized_pnl"], 1.71)
        self.assertEqual(trades["BULL_C10_20261218"]["realized_pnl"], 17.5)
        self.assertNotIn("TMP", trades)

        contributors = analytics["contributors"]
        self.assertEqual(
            {item["id"]: item["risk_level"] for item in contributors},
            PRE_AUGUST_26_RISK_LEVELS,
        )
        self.assertEqual(contributors[0]["id"], "infq")
        self.assertEqual(contributors[0]["total_pnl"], 2334.03)
        self.assertAlmostEqual(contributors[0]["return_pct"], 76.7773, places=4)
        self.assertEqual(contributors[-1]["id"], "ivr")
        self.assertEqual(contributors[-1]["total_pnl"], -53.99)
        self.assertAlmostEqual(contributors[-1]["return_pct"], -6.6655, places=4)
        bull = next(contributor for contributor in contributors if contributor["id"] == "bull")
        self.assertEqual(bull["total_pnl"], 617.16)
        self.assertAlmostEqual(bull["return_pct"], 23.5558, places=4)
        call = next(contributor for contributor in contributors if contributor["id"] == "bull-dec2026-call")
        self.assertEqual(call["realized_pnl"], 17.5)
        self.assertEqual(call["unrealized_pnl"], 226.63)
        self.assertEqual(analytics["unattributed_pnl"]["total"], 24.0)
        self.assertEqual(analytics["reconciliation"], {
            "opening_nav": 9900.0,
            "latest_nav": 13351.5,
            "nav_change": 3451.5,
            "external_flows": 0.0,
            "attributed_pnl": 3427.5,
            "unattributed_pnl": 24.0,
            "explained_change": 3451.5,
            "residual": 0.0,
        })

        exposure = analytics["exposure_history"]
        formation = exposure["points"][0]
        latest = exposure["points"][-1]
        self.assertEqual(formation["gross_exposure"], 10532.0)
        self.assertEqual(latest["date"], "2026-08-25")
        self.assertEqual(latest["gross_exposure"], 13745.17)
        self.assertEqual(latest["values"]["cash-cash-equivalents"]["value"], 7250.96)
        self.assertEqual(latest["values"]["options"]["value"], 612.49)
        self.assertEqual(latest["values"]["technology"]["value"], 1525.16)
        self.assertNotIn("regional-banking", latest["values"])
        self.assertAlmostEqual(sum(value["percent"] for value in latest["values"].values()), 100.0, places=6)

        risk = analytics["risk_history"]
        self.assertEqual([category["id"] for category in risk["categories"]], ["low", "medium", "high"])
        self.assertEqual([point["date"] for point in risk["points"]], [point["date"] for point in exposure["points"]])
        risk_formation = risk["points"][0]
        risk_latest = risk["points"][-1]
        self.assertEqual(risk_formation["gross_exposure"], 10532.0)
        self.assertEqual(risk_formation["values"], {
            "low": {"value": 3459.56, "percent": 32.848082},
            "medium": {"value": 2924.25, "percent": 27.765382},
            "high": {"value": 4148.19, "percent": 39.386536},
        })
        self.assertEqual(risk_latest["date"], "2026-08-25")
        self.assertEqual(risk_latest["gross_exposure"], 13745.17)
        self.assertEqual(risk_latest["values"], {
            "low": {"value": 7782.7, "percent": 56.621402},
            "medium": {"value": 4802.81, "percent": 34.941772},
            "high": {"value": 1159.66, "percent": 8.436826},
        })
        self.assertAlmostEqual(
            sum(value["value"] for value in risk_latest["values"].values()),
            risk_latest["gross_exposure"],
            places=2,
        )
        self.assertAlmostEqual(sum(value["percent"] for value in risk_latest["values"].values()), 100.0, places=6)

    def test_packaged_fallback_before_august_twenty_first_close_excludes_trade(self) -> None:
        fallback = history.load_json(REPOSITORY / "data/demo-portfolio-history.json")

        def failing_fetcher(_symbol: str, _start: dt.date, _end: dt.date) -> dict[dt.date, float]:
            raise TimeoutError("offline test")

        payload = history.build_history(
            self.ledger,
            as_of=dt.datetime(2026, 8, 21, 12, 0, tzinfo=history.MARKET_ZONE),
            fetcher=failing_fetcher,
            fallback=fallback,
        )
        analytics = payload["accounts"]["ahub"]["analytics"]
        self.assertEqual(payload["last_completed_session"], "2026-08-20")
        self.assertEqual(self.points(payload)[-1]["date"], "2026-08-20")
        self.assertEqual(self.points(payload)[-1]["cash"], 1899.54)
        self.assertNotIn(
            "BULL_C10_20261218",
            {trade["instrument_id"] for trade in analytics["realized_trades"]},
        )
        self.assertNotIn(
            "bull-dec2026-call",
            {contributor["id"] for contributor in analytics["contributors"]},
        )


def math_is_positive(value: object) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


if __name__ == "__main__":
    unittest.main()

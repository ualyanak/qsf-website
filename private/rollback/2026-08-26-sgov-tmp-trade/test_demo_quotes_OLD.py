#!/usr/bin/env python3
"""Deterministic tests for public demo equity and automatic option marks."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
import unittest
from unittest import mock


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import update_demo_quotes as quotes  # noqa: E402


class OptionModelTests(unittest.TestCase):
    def test_bull_december_call_has_exact_registered_model_contract(self) -> None:
        instrument_id = "BULL_C10_20261218"
        spec = quotes.OPTION_MODEL_SPECS[instrument_id]

        self.assertEqual(spec["underlying"], "BULL")
        self.assertEqual(spec["expiry"], "2026-12-18T21:00:00Z")
        self.assertEqual(spec["opening_as_of"], "2026-08-21T13:30:00Z")
        self.assertEqual(spec["opening_spot"], 8.85)
        self.assertIn("completed public close proxy", spec["opening_spot_source"])
        self.assertEqual(spec["opening_mark"], 0.89)
        self.assertEqual(spec["option_symbols"], ["BULL261218C00010000"])
        self.assertEqual(spec["legs"], [{"type": "call", "strike": 10.0, "ratio": 1.0}])

    def test_bull_december_call_model_moves_with_bull_and_time_decay(self) -> None:
        spec = quotes.OPTION_MODEL_SPECS["BULL_C10_20261218"]
        volatility = quotes.calibrated_volatility(spec)
        early = quotes.parse_utc("2026-08-21T13:30:00Z")
        later = quotes.parse_utc("2026-09-21T13:30:00Z")

        lower_spot = quotes.modeled_strategy_value(spec, 8.5, early, volatility)
        higher_spot = quotes.modeled_strategy_value(spec, 9.5, early, volatility)
        later_same_spot = quotes.modeled_strategy_value(spec, 8.85, later, volatility)

        self.assertGreater(higher_spot, lower_spot)
        self.assertLess(later_same_spot, float(spec["opening_mark"]))
        self.assertGreater(volatility, 0)
        self.assertLessEqual(volatility, quotes.MODEL_MAX_VOLATILITY)

    def test_bull_december_call_quote_discloses_calibration_proxy(self) -> None:
        instrument_id = "BULL_C10_20261218"
        spec = quotes.OPTION_MODEL_SPECS[instrument_id]
        with mock.patch.object(
            quotes,
            "utc_now",
            return_value=dt.datetime(2026, 8, 21, 13, 45, tzinfo=dt.timezone.utc),
        ):
            result = quotes.build_model_quote(
                instrument_id,
                spec,
                {
                    "price": 9.5,
                    "as_of": "2026-08-21T13:45:00Z",
                    "quality": "public_delayed",
                },
            )

        self.assertEqual(result["quality"], "model_delayed")
        self.assertEqual(result["instrument_id"], instrument_id)
        self.assertEqual(result["option_symbols"], ["BULL261218C00010000"])
        self.assertEqual(result["calibration_as_of"], "2026-08-21T13:30:00Z")
        self.assertEqual(result["calibration_opening_spot"], 8.85)
        self.assertEqual(result["calibration_opening_mark"], 0.89)
        self.assertIn("completed public close proxy", result["calibration_spot_source"])
        self.assertAlmostEqual(result["price"], 1.209089, places=6)

    def test_opening_premiums_are_reproduced(self) -> None:
        for instrument_id, spec in quotes.OPTION_MODEL_SPECS.items():
            with self.subTest(instrument_id=instrument_id):
                volatility = quotes.calibrated_volatility(spec)
                value = quotes.modeled_strategy_value(
                    spec,
                    float(spec["opening_spot"]),
                    quotes.parse_utc(spec["opening_as_of"]),
                    volatility,
                )
                self.assertAlmostEqual(value, float(spec["opening_mark"]), delta=0.000001)

    def test_model_mark_uses_underlier_timestamp_and_quality(self) -> None:
        spec = quotes.OPTION_MODEL_SPECS["INFQ_C25_20270115"]
        with mock.patch.object(
            quotes,
            "utc_now",
            return_value=dt.datetime(2026, 7, 21, 15, 15, tzinfo=dt.timezone.utc),
        ):
            result = quotes.build_model_quote(
                "INFQ_C25_20270115",
                spec,
                {
                    "price": 9.5,
                    "as_of": "2026-07-21T15:15:00Z",
                    "quality": "public_delayed",
                },
            )
        self.assertEqual(result["quality"], "model_delayed")
        self.assertEqual(result["as_of"], "2026-07-21T15:15:00Z")
        self.assertEqual(result["valuation_as_of"], "2026-07-21T15:15:00Z")
        self.assertEqual(result["underlying_symbol"], "INFQ")
        self.assertGreater(float(result["price"]), 0)

    def test_stale_underlier_never_becomes_current_model(self) -> None:
        spec = quotes.OPTION_MODEL_SPECS["BULL_P10_JAN2027"]
        result = quotes.build_model_quote(
            "BULL_P10_JAN2027",
            spec,
            {
                "price": 8.1,
                "as_of": "2026-07-17T13:30:00Z",
                "quality": "stale_fallback",
            },
        )
        self.assertEqual(result["quality"], "stale_model")

    def test_model_valuation_time_advances_without_relabeling_market_input(self) -> None:
        spec = quotes.OPTION_MODEL_SPECS["BULL_P10_JAN2027"]
        with mock.patch.object(
            quotes,
            "utc_now",
            return_value=dt.datetime(2026, 7, 21, 15, 30, tzinfo=dt.timezone.utc),
        ):
            result = quotes.build_model_quote(
                "BULL_P10_JAN2027",
                spec,
                {
                    "price": 8.1,
                    "as_of": "2026-07-21T15:15:00Z",
                    "quality": "public_delayed",
                },
            )
        self.assertEqual(result["as_of"], "2026-07-21T15:15:00Z")
        self.assertEqual(result["valuation_as_of"], "2026-07-21T15:30:00Z")

    def test_vertical_is_bounded_by_strike_width(self) -> None:
        spec = quotes.OPTION_MODEL_SPECS["INFQ_C10_C17_5_20270115"]
        volatility = quotes.calibrated_volatility(spec)
        observed = dt.datetime(2026, 12, 15, tzinfo=dt.timezone.utc)
        for spot in (0.01, 10.0, 1000.0):
            value = quotes.modeled_strategy_value(spec, spot, observed, volatility)
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 7.5)

    def test_snapshot_contains_every_registered_option(self) -> None:
        prices = {"BULL": 8.1, "INFQ": 9.5}

        def fake_quote(symbol: str) -> dict[str, object]:
            return {
                "price": prices.get(symbol, 100.0),
                "as_of": "2026-07-21T15:15:00Z",
                "source": "Test fixture",
                "quality": "public_delayed",
            }

        with mock.patch.object(quotes, "fetch_quote", side_effect=fake_quote):
            snapshot, missing = quotes.build_snapshot({})
        self.assertEqual(missing, [])
        for instrument_id in quotes.OPTION_MODEL_SPECS:
            self.assertIn(instrument_id, snapshot["quotes"])
            self.assertEqual(snapshot["quotes"][instrument_id]["quality"], "model_delayed")

    def test_newer_fallback_cannot_be_replaced_by_older_fetch(self) -> None:
        fetched = {"price": 99.0, "as_of": "2026-07-21T14:00:00Z", "quality": "public_delayed"}
        fallback = {"price": 101.0, "as_of": "2026-07-21T15:00:00Z", "quality": "public_delayed"}
        with mock.patch.object(
            quotes,
            "utc_now",
            return_value=dt.datetime(2026, 7, 21, 15, 1, tzinfo=dt.timezone.utc),
        ):
            selected = quotes.newest_equity_quote(fetched, fallback)
        self.assertEqual(selected["price"], 101.0)
        self.assertEqual(selected["as_of"], "2026-07-21T15:00:00Z")
        self.assertEqual(selected["quality"], "public_delayed")

    def test_zero_option_fallback_is_valid(self) -> None:
        self.assertEqual(quotes.finite_nonnegative(0), 0.0)

    def test_option_metadata_matches_published_instruments(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[1]
        data = json.loads((repository / "data/demo-accounts.json").read_text(encoding="utf-8"))
        for instrument_id, spec in quotes.OPTION_MODEL_SPECS.items():
            with self.subTest(instrument_id=instrument_id):
                instrument = data["instruments"][instrument_id]
                self.assertEqual(instrument["quote_symbol"], instrument_id)
                self.assertEqual(instrument["underlying_symbol"], spec["underlying"])
                self.assertEqual(instrument["expiry"], str(spec["expiry"])[:10])
                published_symbols = instrument.get("option_symbols") or [instrument.get("option_symbol")]
                self.assertEqual(published_symbols, spec["option_symbols"])

    def test_short_option_sign_and_contract_multiplier(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[1]
        data = json.loads((repository / "data/demo-accounts.json").read_text(encoding="utf-8"))
        position = next(item for item in data["accounts"]["ahub"]["positions"] if item["instrument"] == "BULL_P10_JAN2027")
        instrument = data["instruments"][position["instrument"]]
        self.assertEqual(position["quantity"], -1)
        self.assertEqual(instrument["multiplier"], 100)
        self.assertLess(position["quantity"] * instrument["multiplier"] * 2.5, 0)

    def test_bull_purchase_is_funded_by_sgov_reallocation(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[1]
        data = json.loads((repository / "data/demo-accounts.json").read_text(encoding="utf-8"))
        account = data["accounts"]["ahub"]
        positions = {position["instrument"]: position for position in account["positions"]}

        self.assertEqual(positions["BULL"]["quantity"], 200)
        self.assertEqual(positions["BULL"]["basis_price"], 7.2)
        sgov_sale_proceeds = 23 * 100.65 + 1 * 100.68
        bull_purchase_cost = 320 * 7.2
        self.assertAlmostEqual(sgov_sale_proceeds, 2415.63, places=2)
        self.assertEqual(bull_purchase_cost, 2304.0)
        self.assertAlmostEqual(sgov_sale_proceeds - bull_purchase_cost, 111.63, places=2)
        reallocation_note = next(note for note in account["cash_notes"] if note["date"] == "2026-08-04")
        self.assertAlmostEqual(reallocation_note["amount"], 111.63, places=2)
        self.assertEqual(data["instruments"]["BULL"]["mark_mode"], "public_delayed")
        for instrument_id in positions:
            self.assertIn(instrument_id, data["instruments"])

    def test_august_thirteenth_infq_sales_and_sgov_purchase(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[1]
        data = json.loads((repository / "data/demo-accounts.json").read_text(encoding="utf-8"))
        account = data["accounts"]["ahub"]
        positions = {position["instrument"]: position for position in account["positions"]}

        infq_sale_proceeds = 3 * 100 * 1.10 + 5 * 100 * 2.42 + 15 * 100 * 2.43
        sgov_purchase_cost = 51 * 100.53
        net_cash_change = infq_sale_proceeds - sgov_purchase_cost
        weighted_sgov_basis = (2 * 100.58 + sgov_purchase_cost) / 53

        self.assertAlmostEqual(infq_sale_proceeds, 5185.0, places=2)
        self.assertAlmostEqual(sgov_purchase_cost, 5127.03, places=2)
        self.assertAlmostEqual(net_cash_change, 57.97, places=2)
        self.assertAlmostEqual(525.46 + net_cash_change, 583.43, places=2)
        self.assertEqual(positions["INFQ_C25_20270115"]["quantity"], 1)
        self.assertNotIn("INFQ_C10_C17_5_20270115", positions)
        self.assertEqual(positions["SGOV"]["quantity"], 53)
        self.assertAlmostEqual(positions["SGOV"]["basis_price"], weighted_sgov_basis, places=10)
        self.assertEqual(data["instruments"]["SGOV"]["mark_mode"], "public_delayed")
        self.assertIn("SGOV", quotes.SYMBOLS)
        self.assertNotIn("GOVT", positions)

        update_note = next(note for note in account["cash_notes"] if note["date"] == "2026-08-13")
        self.assertEqual(update_note["as_of"], "2026-08-13T10:00:00-05:00")
        self.assertAlmostEqual(update_note["amount"], net_cash_change, places=2)

        for instrument_id in positions:
            instrument = data["instruments"][instrument_id]
            if instrument["mark_mode"] == "public_delayed":
                self.assertIn(instrument.get("quote_symbol", instrument_id), quotes.SYMBOLS)
            elif instrument["mark_mode"] == "model_delayed":
                self.assertIn(instrument.get("quote_symbol", instrument_id), quotes.OPTION_MODEL_SPECS)

    def test_august_fourteenth_tssi_sale_is_converted_to_cash(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[1]
        data = json.loads((repository / "data/demo-accounts.json").read_text(encoding="utf-8"))
        account = data["accounts"]["ahub"]
        positions = {position["instrument"]: position for position in account["positions"]}
        proceeds = 30 * 9.65
        basis = 30 * 9.94
        realized_pnl = proceeds - basis

        self.assertAlmostEqual(proceeds, 289.5, places=2)
        self.assertAlmostEqual(basis, 298.2, places=2)
        self.assertAlmostEqual(realized_pnl, -8.7, places=2)
        self.assertAlmostEqual(583.43 + proceeds, 872.93, places=2)
        self.assertNotIn("TSSI", positions)
        self.assertEqual(len(positions), 13)

        update_note = next(note for note in account["cash_notes"] if note["date"] == "2026-08-14")
        self.assertAlmostEqual(update_note["amount"], proceeds, places=2)
        self.assertIn("$8.70", update_note["note"])

    def test_august_eighteenth_dividends_are_credited_to_cash(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[1]
        data = json.loads((repository / "data/demo-accounts.json").read_text(encoding="utf-8"))
        account = data["accounts"]["ahub"]
        positions = {position["instrument"]: position for position in account["positions"]}
        dividend_notes = {
            note["instrument"]: note
            for note in account["cash_notes"]
            if note.get("date") == "2026-08-18" and note.get("kind") == "illustrative_dividend"
        }

        self.assertEqual(data["published_at"], "2026-08-21")
        self.assertEqual(set(dividend_notes), {"SGOV", "IVR"})
        self.assertAlmostEqual(dividend_notes["SGOV"]["amount"], 0.61, places=2)
        self.assertAlmostEqual(dividend_notes["IVR"]["amount"], 12.0, places=2)
        self.assertAlmostEqual(872.93 + 0.61 + 12.0, 885.54, places=2)
        self.assertEqual(positions["SGOV"]["quantity"], 53)
        self.assertEqual(positions["IVR"]["quantity"], 100)
        self.assertEqual(len(positions), 13)

    def test_august_nineteenth_bull_sale_is_converted_to_cash(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[1]
        data = json.loads((repository / "data/demo-accounts.json").read_text(encoding="utf-8"))
        account = data["accounts"]["ahub"]
        positions = {position["instrument"]: position for position in account["positions"]}
        proceeds = 120 * 8.45
        sold_basis = 120 * 7.2
        realized_pnl = proceeds - sold_basis

        self.assertAlmostEqual(proceeds, 1014.0, places=2)
        self.assertAlmostEqual(sold_basis, 864.0, places=2)
        self.assertAlmostEqual(realized_pnl, 150.0, places=2)
        self.assertAlmostEqual(account["cash"], 885.54 + proceeds + 17.5, places=2)
        self.assertEqual(positions["BULL"]["quantity"], 200)
        self.assertAlmostEqual(positions["BULL"]["basis_price"], 7.2, places=2)
        self.assertEqual(len(positions), 13)

        update_note = next(note for note in account["cash_notes"] if note["date"] == "2026-08-19")
        self.assertEqual(update_note["instrument"], "BULL")
        self.assertAlmostEqual(update_note["amount"], proceeds, places=2)
        self.assertIn("$150.00", update_note["note"])

    def test_august_twenty_first_bull_call_uses_automatic_model_and_zero_remaining_basis(self) -> None:
        repository = pathlib.Path(__file__).resolve().parents[1]
        data = json.loads((repository / "data/demo-accounts.json").read_text(encoding="utf-8"))
        account = data["accounts"]["ahub"]
        positions = {position["instrument"]: position for position in account["positions"]}
        instrument_id = "BULL_C10_20261218"
        instrument = data["instruments"][instrument_id]
        position = positions[instrument_id]

        purchase_cost = 10 * 100 * 0.89
        sale_proceeds = 7.5 * 100 * 1.21
        supplied_realized_pnl = sale_proceeds - purchase_cost
        self.assertAlmostEqual(purchase_cost, 890.0, places=2)
        self.assertAlmostEqual(sale_proceeds, 907.5, places=2)
        self.assertAlmostEqual(supplied_realized_pnl, 17.5, places=2)
        self.assertAlmostEqual(account["cash"], 1899.54 + supplied_realized_pnl, places=2)
        self.assertEqual(position["quantity"], 2.5)
        self.assertEqual(position["basis_price"], 0.0)

        self.assertEqual(instrument["quote_symbol"], instrument_id)
        self.assertEqual(instrument["option_symbol"], "BULL261218C00010000")
        self.assertEqual(instrument["underlying_symbol"], "BULL")
        self.assertEqual(instrument["expiry"], "2026-12-18")
        self.assertEqual(instrument["multiplier"], 100)
        self.assertEqual(instrument["mark_mode"], "model_delayed")
        self.assertEqual(instrument["attribution_group_id"], "bull-dec2026-call")
        self.assertEqual(instrument["risk_level"], "high")
        self.assertIn(instrument_id, quotes.OPTION_MODEL_SPECS)

        update_note = next(note for note in account["cash_notes"] if note["date"] == "2026-08-21")
        self.assertEqual(update_note["instrument"], instrument_id)
        self.assertAlmostEqual(update_note["amount"], supplied_realized_pnl, places=2)
        self.assertIn("remaining 2.5 contracts carry zero basis", update_note["note"])


if __name__ == "__main__":
    unittest.main()

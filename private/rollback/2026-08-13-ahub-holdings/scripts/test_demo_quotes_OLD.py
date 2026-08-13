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

        self.assertEqual(account["cash"], 413.83)
        self.assertEqual(positions["SGOV"]["quantity"], 3)
        self.assertEqual(positions["BULL"]["quantity"], 320)
        self.assertEqual(positions["BULL"]["basis_price"], 7.2)
        self.assertEqual(320 * 7.2, 2304.0)
        self.assertEqual(data["instruments"]["BULL"]["mark_mode"], "public_delayed")
        for instrument_id in positions:
            self.assertIn(instrument_id, data["instruments"])


if __name__ == "__main__":
    unittest.main()

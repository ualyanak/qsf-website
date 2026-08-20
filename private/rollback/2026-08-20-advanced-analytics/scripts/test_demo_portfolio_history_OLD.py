#!/usr/bin/env python3
"""Deterministic tests for the public demo portfolio-history generator."""

from __future__ import annotations

import datetime as dt
import pathlib
import sys
import unittest


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import update_demo_portfolio_history as history  # noqa: E402


REPOSITORY = pathlib.Path(__file__).resolve().parents[1]


class PortfolioHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ledger = history.load_json(REPOSITORY / "data/demo-portfolio-ledger.json")
        cls.accounts = history.load_json(REPOSITORY / "data/demo-accounts.json")
        cls.symbols = history.required_symbols(cls.ledger)
        cls.sessions = [
            dt.date(2026, 7, 17) + dt.timedelta(days=offset)
            for offset in range((dt.date(2026, 8, 19) - dt.date(2026, 7, 17)).days + 1)
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
            for offset in range((dt.date(2026, 8, 19) - dt.date(2026, 7, 17)).days + 1)
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

        self.assertAlmostEqual(july_17["cash"], 389.83, places=2)
        self.assertAlmostEqual(july_20["cash"], 413.83, places=2)
        self.assertEqual(history.position_quantities(july_20)["SGOV"], 26)
        self.assertAlmostEqual(august_4["cash"], 413.83, places=2)
        self.assertEqual(history.position_quantities(august_4)["SGOV"], 3)
        self.assertEqual(history.position_quantities(august_4)["BULL"], 320)
        self.assertEqual(history.position_quantities(before_august_13)["INFQ_C25_20270115"], 4)
        self.assertEqual(history.position_quantities(before_august_13)["INFQ_C10_C17_5_20270115"], 20)
        self.assertAlmostEqual(august_13["cash"], 471.8, places=2)
        self.assertEqual(history.position_quantities(august_13)["TSSI"], 30)
        self.assertAlmostEqual(before_august_14["cash"], 471.8, places=2)
        self.assertEqual(history.position_quantities(before_august_14)["TSSI"], 30)
        self.assertAlmostEqual(august_14["cash"], 761.3, places=2)
        self.assertNotIn("TSSI", history.position_quantities(august_14))
        self.assertEqual(history.position_quantities(august_14)["BULL"], 320)
        self.assertAlmostEqual(before_august_18["cash"], 761.3, places=2)
        self.assertAlmostEqual(august_18["cash"], 773.91, places=2)
        self.assertEqual(history.position_quantities(august_18), history.position_quantities(august_14))
        self.assertAlmostEqual(before_august_19["cash"], 773.91, places=2)
        self.assertEqual(history.position_quantities(before_august_19)["BULL"], 320)
        self.assertAlmostEqual(august_19["cash"], 1787.91, places=2)
        self.assertEqual(history.position_quantities(august_19)["BULL"], 200)
        self.assertEqual(history.position_quantities(august_19), self.ledger["expected_current_snapshot"]["positions"])

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
        self.assertAlmostEqual(current["cash"], 471.8 + proceeds, places=2)
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

        self.assertAlmostEqual(before["cash"], 761.3, places=2)
        self.assertAlmostEqual(after["cash"], 773.91, places=2)
        self.assertAlmostEqual(after["cash"] - before["cash"], 0.61 + 12.0, places=2)
        self.assertEqual(history.position_quantities(after), history.position_quantities(before))

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

        self.assertAlmostEqual(before["cash"], 773.91, places=2)
        self.assertAlmostEqual(after["cash"], 1787.91, places=2)
        self.assertAlmostEqual(after["cash"] - before["cash"], proceeds, places=2)
        self.assertAlmostEqual(realized_pnl, 150.0, places=2)
        self.assertEqual(history.position_quantities(before)["BULL"], 320)
        self.assertEqual(history.position_quantities(after)["BULL"], 200)
        self.assertAlmostEqual(after["positions"]["BULL"]["basis_price"], 7.2, places=2)

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
        self.assertAlmostEqual(before["cash"], after["cash"], places=2)

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
        self.assertAlmostEqual(by_date["2026-08-14"]["cash"], 761.3, places=2)
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
        self.assertAlmostEqual(points[-1]["cash"], 413.83, places=2)

    def test_august_thirteen_close_uses_updated_holdings_and_cash(self) -> None:
        payload = history.build_history(
            self.ledger,
            as_of=self.after_close(dt.date(2026, 8, 13)),
            fetcher=self.fetcher,
        )
        closing = self.points(payload)[-1]
        self.assertEqual(closing["kind"], "session_close")
        self.assertAlmostEqual(closing["cash"], 471.8, places=2)
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
        self.assertAlmostEqual(closing["cash"], 761.3, places=2)
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
        self.assertAlmostEqual(closing["cash"], 773.91, places=2)
        self.assertAlmostEqual(closing["value"] - closing["positions_value"], 773.91, places=2)
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
        self.assertAlmostEqual(closing["cash"], 1787.91, places=2)
        self.assertAlmostEqual(closing["value"] - closing["positions_value"], 1787.91, places=2)
        self.assertEqual(closing["position_count"], 12)
        self.assertNotIn("BULL", closing["forward_filled_symbols"])

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

    def test_no_network_and_no_fallback_cannot_invent_session_calendar(self) -> None:
        def failing_fetcher(_symbol: str, _start: dt.date, _end: dt.date) -> dict[dt.date, float]:
            raise TimeoutError("offline test")

        with self.assertRaisesRegex(history.HistoryError, "No completed SPY session"):
            history.build_history(
                self.ledger,
                as_of=self.after_close(dt.date(2026, 8, 13)),
                fetcher=failing_fetcher,
            )


def math_is_positive(value: object) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


if __name__ == "__main__":
    unittest.main()

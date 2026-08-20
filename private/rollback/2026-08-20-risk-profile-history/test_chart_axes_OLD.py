import json
import math
import re
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
BENCHMARK_IDS = ["spy", "gold-gld", "btc-usd"]
APPROVED_RISK_LEVELS = {
    "infq": "high",
    "bull": "medium",
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
}


def tick_indexes(length, maximum):
    count = min(length, maximum)
    if count <= 0:
        return []
    if count == 1:
        return [0]
    indexes = []
    for tick in range(count):
        index = math.floor(tick * (length - 1) / (count - 1) + 0.5)
        if not indexes or indexes[-1] != index:
            indexes.append(index)
    return indexes


class ChartAxesTests(unittest.TestCase):
    def setUp(self):
        self.portal = (REPO / "assets/js/portal-demo.js").read_text(encoding="utf-8")
        self.portal_css = (REPO / "assets/css/portal.css").read_text(encoding="utf-8")
        self.dashboard = (REPO / "investor_login/dashboard.html").read_text(encoding="utf-8")
        history = json.loads((REPO / "data/demo-portfolio-history.json").read_text(encoding="utf-8"))
        self.account = history["accounts"]["ahub"]
        self.points = self.account["points"]
        self.comparisons = self.account.get("comparisons", [])

    def test_tick_counts_adapt_without_depending_on_history_length(self):
        for maximum in (7, 6):
            indexes = tick_indexes(len(self.points), maximum)
            self.assertEqual(len(indexes), min(len(self.points), maximum))
            self.assertEqual(indexes[0], 0)
            self.assertEqual(indexes[-1], len(self.points) - 1)
            self.assertEqual(indexes, sorted(set(indexes)))
        self.assertEqual(tick_indexes(2, 7), [0, 1])

    def test_history_exposes_three_aligned_normalized_benchmarks(self):
        self.assertEqual([series["id"] for series in self.comparisons], BENCHMARK_IDS)
        qsf_dates = [point["date"] for point in self.points]
        opening_value = float(self.account["opening_nav"])
        self.assertEqual(opening_value, 9900.0)
        for series in self.comparisons:
            with self.subTest(series=series["id"]):
                benchmark_points = series["points"]
                self.assertEqual([point["date"] for point in benchmark_points], qsf_dates)
                self.assertAlmostEqual(float(benchmark_points[0]["value"]), opening_value, places=2)
                self.assertTrue(all(math.isfinite(float(point["value"])) for point in benchmark_points))

    def test_dashboard_builds_qsf_plus_benchmarks_on_one_axis(self):
        self.assertIn("comparisonSeries: comparisonSeries", self.portal)
        self.assertIn("renderChart(view.comparisonSeries, view.currency, view.openingNav)", self.portal)
        self.assertIn("const axisValues = [];", self.portal)
        self.assertRegex(
            self.portal,
            r"chartSeries\.forEach\(function \(series\) \{\s*series\.points\.forEach\(function \(point\) \{ axisValues\.push\(point\.value\); \}\);",
        )
        self.assertIn("const axis = chartAxis(axisValues);", self.portal)
        self.assertIn("const intervalCount = 5;", self.portal)
        self.assertIn("dataMin >= 9000 ? 9000", self.portal)
        self.assertIn("chartTickIndexes(points.length, mobile ? 6 : 7)", self.portal)

    def test_renderer_has_four_distinct_series_and_latest_markers_without_area_fill(self):
        for series_id in ["qsf", *BENCHMARK_IDS]:
            self.assertIn(f'"{series_id}"', self.portal)
        self.assertIn('"btc-usd": { label: "Bitcoin"', self.portal)
        self.assertIn('dash: "7 5"', self.portal)
        self.assertNotIn("areaPath", self.portal)
        self.assertNotIn("rgba(201, 162, 79, .17)", self.portal)
        self.assertEqual(self.portal.count('"data-chart-latest"'), 1)
        self.assertRegex(
            self.portal,
            re.compile(
                r"drawOrder\.forEach\(function \(series\) \{.*?const marker = svgNode\(\"circle\".*?svg\.append\(marker\);",
                re.DOTALL,
            ),
        )

    def test_chart_remains_completed_nightly_only(self):
        self.assertNotIn('historyByDate.set(today, { date: today, value: nav', self.portal)
        self.assertNotIn('kind: "live_delayed_marks"', self.portal)
        self.assertIn("point.date <= today", self.portal)
        self.assertIn("Latest night ", self.portal)

    def test_analytics_schema_reconciles_and_exposure_points_total_one_hundred_percent(self):
        analytics = self.account.get("analytics")
        self.assertIsInstance(analytics, dict)
        self.assertGreater(len(analytics.get("contributors", [])), 0)
        self.assertGreater(len(analytics.get("realized_trades", [])), 0)
        self.assertEqual(
            {item["id"]: item["risk_level"] for item in analytics["contributors"]},
            APPROVED_RISK_LEVELS,
        )
        reconciliation = analytics["reconciliation"]
        attributed = sum(float(item["total_pnl"]) for item in analytics["contributors"])
        self.assertAlmostEqual(attributed, float(reconciliation["attributed_pnl"]), places=2)
        self.assertAlmostEqual(
            attributed + float(analytics["unattributed_pnl"]["total"]),
            float(reconciliation["explained_change"]),
            places=2,
        )
        exposure = analytics["exposure_history"]
        self.assertEqual([point["date"] for point in exposure["points"]], [point["date"] for point in self.points])
        category_ids = {item["id"] for item in exposure["categories"]}
        self.assertGreaterEqual(len(category_ids), 4)
        for point in exposure["points"]:
            with self.subTest(date=point["date"]):
                self.assertEqual(set(point["values"]), category_ids)
                self.assertAlmostEqual(sum(float(item["percent"]) for item in point["values"].values()), 100.0, places=4)

    def test_advanced_view_exposes_semantic_attribution_and_exposure_controls(self):
        for identifier in [
            'id="contribution-chart"',
            'id="realized-trades-body"',
            'id="exposure-chart"',
            'id="exposure-date-control"',
            'id="exposure-breakdown"',
        ]:
            self.assertIn(identifier, self.dashboard)
        self.assertIn("Portfolio analytics, exposure &amp; holdings", self.dashboard)
        self.assertIn("Return on closed basis", self.dashboard)
        self.assertIn("not option delta, leverage, or notional exposure", self.dashboard)

    def test_advanced_renderers_preserve_rich_analytics_and_current_basis(self):
        self.assertIn("function mergeAnalytics(previous, next)", self.portal)
        self.assertIn("values: Object.assign({}, existing && existing.values || {}, point.values || {})", self.portal)
        self.assertIn("basisPrice: basisPrice", self.portal)
        self.assertIn("unrealizedPnl: marketValue - basisValue", self.portal)
        self.assertIn("renderAdvancedAnalytics(view)", self.portal)
        self.assertIn("renderExposureAnalytics(view)", self.portal)
        self.assertIn('risk.className = "contribution-risk is-" + riskLevel', self.portal)
        self.assertIn('riskLevel.toUpperCase() + " RISK"', self.portal)
        self.assertIn("riskLabel.toLowerCase()", self.portal)
        self.assertIn("Risk labels are administrator-assigned qualitative categories", self.portal)
        for class_name in [".contribution-risk.is-high", ".contribution-risk.is-medium", ".contribution-risk.is-low"]:
            self.assertIn(class_name, self.portal_css)
        self.assertIn("@media (max-width: 370px)", self.portal_css)
        self.assertIn("chartTickIndexes(points.length, mobile ? 6 : 7)", self.portal)
        for tick in ["0", "25", "50", "75", "100"]:
            self.assertIn(tick, self.portal)
        self.assertIn('kind: view.modifiedAt ? "local_now" : "intraday_now"', self.portal)

    def test_current_portal_pages_share_the_analytics_cache_version(self):
        for relative in ["investor_login/dashboard.html", "investor_login/index.html", "admin/index.html"]:
            page = (REPO / relative).read_text(encoding="utf-8")
            self.assertIn("20260820-risk1", page)


if __name__ == "__main__":
    unittest.main()
